"""QA Copilot 核心：subprocess 调用 claude CLI 流式生成测试点。

设计要点：
- **非交互**：`claude -p <prompt> --output-format stream-json --verbose`，逐行解析事件。
- **安全**（关键）：`--disallowedTools` 禁用一切可改文件/执行命令/联网的内置工具，
  `--strict-mcp-config` + 空 MCP 隔离本机 MCP 服务，cwd 指向临时目录避免读到项目
  CLAUDE.md。纯文本生成本不需要工具，禁用是纵深防御。
- **噪音过滤**：本机 SessionStart hook 会往 stream 里灌 system 事件（memory/skills），
  解析层只挑 `assistant` 文本与最终 `result`，其余一律跳过。
- **成本/资源控制**：全局并发信号量（拿不到即拒绝），单次硬超时（后台读线程 + 队列，
  超时 kill 子进程）。
- runner 只负责「跑 + 解析 + yield 事件」，不碰数据库；落库由 api 层完成。
"""
import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from queue import Empty, Queue
from typing import Iterator

from app.core.config import settings

logger = logging.getLogger("test_platform")


def _load_selector_keys(project_id: int | None = None, pages: list[str] | None = None) -> list[dict]:
    """项目级共享 key 清单(供 prompt 注入),返回 [{key, frame, desc, page}, ...]。

    DB 是唯一事实来源:走服务层读项目共享 key(sub_product='')。生成器脱离请求 db,
    故内部自开 SessionLocal 并关闭。project_id 为空或读不到 → 空列表(prompt 不注入 key 清单)。
    pages 非空时按页面收窄(见 shared_key_dicts):只留该页 + 未分类的 key。
    """
    if not project_id:
        return []
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_dicts
    s = SessionLocal()
    try:
        return shared_key_dicts(s, project_id, pages)
    except Exception:
        logger.warning("读注册表失败(project_id=%s),prompt 不注入 key 清单", project_id)
        return []
    finally:
        s.close()


def _key_page_map(project_id: int | None = None) -> dict[str, str]:
    """项目共享 key → 所属页面 的映射(供按 script 用到的 key 反查页面,自动给用例打页面标)。

    project_id 为空或读不到 → 空 dict。生成器脱离请求 db,内部自开 SessionLocal 并关闭。
    """
    if not project_id:
        return {}
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_page_map
    s = SessionLocal()
    try:
        return shared_key_page_map(s, project_id)
    except Exception:
        return {}
    finally:
        s.close()


def _pages_for_script(script, key_page_map: dict[str, str]) -> str:
    """从 script 里引用的 target.key 反查页面,并集去重、按序逗号拼接。无 key/无页面 → 空串。

    script 可为步骤数组或已 json.dumps 的字符串;key_page_map 传入(批量场景外部读一次复用)。
    """
    if not key_page_map or not script:
        return ""
    steps = script
    if isinstance(script, str):
        try:
            steps = json.loads(script)
        except (json.JSONDecodeError, ValueError):
            return ""
    if not isinstance(steps, list):
        return ""
    pages: list[str] = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        tgt = st.get("target") or {}
        k = tgt.get("key") if isinstance(tgt, dict) else None
        p = (key_page_map.get(k) or "").strip() if k else ""
        if p and p not in pages:
            pages.append(p)
    return ",".join(pages)


def pages_for_script(script, project_id: int | None = None) -> str:
    """单条便捷版:读一次 key→page 映射并推断该 script 的页面(供 gen_script 重生后重新打标)。"""
    return _pages_for_script(script, _key_page_map(project_id))


def _registered_keys(project_id: int | None = None) -> set[str]:
    """项目级**候选有效**的 key 集合(供生成侧校验 script.target.key)——L4 口径。

    只收候选结构可用(至少一个含 by+value 的候选)的 key:注册了但候选坏成 [{}]/空 [] 的 key
    **不算可用**,会被当『选择器待补』降级(而非当可执行 script 放行)。口径由服务层
    usable_key_set 单点定义(与 schema/runner 的「有效候选」一致)。

    project_id 为空或读不到 → 返回空集。空集时校验放行(见 _validate_script),
    避免"读不到注册表就把所有 gui/e2e 全降 manual"这种误伤生成结果。
    """
    if not project_id:
        return set()
    from app.db.session import SessionLocal
    from app.services.selectors import usable_key_set
    s = SessionLocal()
    try:
        return usable_key_set(s, project_id)
    except Exception:
        return set()
    finally:
        s.close()


def _load_api_contract(project_id: int | None = None) -> dict | None:
    """项目级 api 契约(供 prompt 注入):{base_url, contract}。类比 _load_selector_keys。

    DB 单源,走 api_env 服务层(生成器脱离请求 db,自开 SessionLocal 并关闭)。
    project_id 空 / 项目未配 / base_url 与 contract 皆空 → None(prompt 注入"无契约"提示)。
    """
    if not project_id:
        return None
    from app.db.session import SessionLocal
    from app.services.api_env import get_api_env
    s = SessionLocal()
    try:
        env = get_api_env(s, project_id)
    except Exception:
        logger.warning("读 api 契约失败(project_id=%s),prompt 不注入契约", project_id)
        return None
    finally:
        s.close()
    if not env:
        return None
    base_url = (env.get("base_url") or "").strip()
    contract = (env.get("contract") or "").strip()
    if not base_url and not contract:
        return None
    return {"base_url": base_url, "contract": contract}


def _api_contract_block(project_id: int | None = None) -> str:
    """api 契约注入块:有契约给 base_url+接口清单;无契约提示"优先改判 gui/e2e、勿臆造接口"。"""
    c = _load_api_contract(project_id)
    if c:
        return (
            "   项目 api 契约(api 用例的 path 只能来自此清单;鉴权由执行器按项目配置统一注入,勿在 script 写死 token):\n"
            f"   - base_url:{c['base_url'] or '(未配置,执行器下发时补)'}\n"
            f"   - 接口清单:\n{c['contract'] or '(空)'}"
        )
    return (
        "   (当前项目无 api 契约:api 用例无法在被测客户端外鉴权执行(接口鉴权由客户端内部动态签名)。"
        "若该验证点在界面上可操作、可断言,请**优先改判 kind=gui/e2e**(界面触发操作+断言界面结果);"
        "确无界面入口可验证再判 manual。不要臆造接口 path 硬写 api。)"
    )

# 禁用的内置工具：覆盖执行/改文件/联网/子代理，纯生成任务一个都用不到
_DISALLOWED_TOOLS = [
    "Bash", "BashOutput", "KillShell",
    "Edit", "MultiEdit", "Write", "NotebookEdit",
    "Read", "Glob", "Grep", "LS",
    "WebFetch", "WebSearch", "Task", "TodoWrite",
]

_SYSTEM_PROMPT = (
    "你是一名资深测试工程师，擅长把需求快速拆解为高覆盖率、可执行、可落地的测试点。"
    "只按用户要求的格式输出，不寒暄、不解释。"
)

# 全局并发闸：控制同时运行的 claude 子进程数（成本 + 机器负载）
_slots = threading.BoundedSemaphore(max(1, settings.AI_MAX_CONCURRENCY))


def _claude_bin() -> str | None:
    return settings.CLAUDE_BIN or shutil.which("claude")


def is_available() -> bool:
    """AI 功能是否可用（开关打开且能找到 claude 可执行文件）。"""
    return bool(settings.AI_ENABLED and _claude_bin())


# api script 编写规范段(注入 build_testcase_prompt / build_script_prompt)。
# **普通字符串(非 f-string)**:内含 {{变量}} 模板与 {字段} JSON 示例,避免 f-string 花括号转义地狱。
# 由调用方 f-string 以 {_API_SCRIPT_SPEC} 原样插入(f-string 不会二次解释被插值变量的花括号)。
# 不带条目序号:build_testcase_prompt 用时前缀"7. ",单条重生 build_script_prompt 直接用。
_API_SCRIPT_SPEC = """api script(当 kind=api)——请求-断言-提取原子的有序数组,每步一个对象 {name, request, asserts, extract?, cleanup?}:
   - request:{method(GET/POST/PUT/PATCH/DELETE), path(相对路径,如 /api/projects,可含 {{变量}}), headers?, query?, body?}
   - path 只能来自下方「项目 api 契约」的接口清单;契约里没有的接口 → 改判 kind=manual、script=[](不要臆造 path)
   - asserts:至少 1 个,每个 {type, path?, op, value?}:
     · type=status 断言 HTTP 状态码;type=jsonpath 断言响应体字段(path 用点路径,如 data.id、data.list.0.id)
     · op ∈ eq/neq/exists/contains/gt/lt/regex/type;除 exists 外都需 value
     · 优先带业务成功断言 {"type":"jsonpath","path":"code","op":"eq","value":0}(平台统一 {code,msg,data} 信封)
   - extract?:从本步响应体按点路径取值存入变量,如 {"pid":"data.id"};后续步骤用 {{pid}} 引用
   - 变量必须先提取后引用:任何 {{变量}} 必须在之前某步的 extract 里定义过(固定鉴权 token 由执行器按项目配置注入,无需在 script 写死;仅当契约要求登录换 token 时才写登录步 extract token)
   - cleanup?:true 表示清理步骤(无论前面成败都执行、多个逆序执行、其断言失败不算用例失败)
   - 含写操作(POST/PUT/PATCH/DELETE)的 script 必须在末尾补 cleanup:true 的删除步、用已提取的 id 定位删除,否则改判 manual
   - 边界/异常用例给具体示例值(如超长名、缺必填、越权 id),不要只写"传非法参数"
   - 正例(创建→查询→清理):
     [
       {"name":"创建项目","request":{"method":"POST","path":"/api/projects","body":{"name":"自动化项目"}},"asserts":[{"type":"status","op":"eq","value":200},{"type":"jsonpath","path":"code","op":"eq","value":0},{"type":"jsonpath","path":"data.id","op":"exists"}],"extract":{"pid":"data.id"}},
       {"name":"查询项目","request":{"method":"GET","path":"/api/projects/{{pid}}"},"asserts":[{"type":"jsonpath","path":"code","op":"eq","value":0}]},
       {"name":"清理-删除项目","cleanup":true,"request":{"method":"DELETE","path":"/api/projects/{{pid}}"},"asserts":[{"type":"status","op":"eq","value":200}]}
     ]"""


def build_testcase_prompt(requirement: str, project_id: int | None = None, pages: list[str] | None = None) -> str:
    """把需求文本包装成「生成结构化测试点」的指令。

    用 <requirement> 标签包裹用户输入（而非引号），避免内容里的引号破坏边界。
    强约束只输出 JSON 数组；即便模型仍包了 markdown fence，解析层也能兜底剥离。
    project_id:注入该项目共享 key 清单;为空则不注入(见 _load_selector_keys)。
    pages:非空时只注入这些页面(+未分类)的 key,收窄噪声、减少降级(见 _load_selector_keys)。
    """
    # 注入语义 key 清单(供 gui/e2e 的 script.target.key 取值);读不到就给空块、只说明无可用 key
    keys = _load_selector_keys(project_id, pages)
    if keys:
        lines = "\n".join(f"   - {k['key']}（{k['frame']}）：{k['desc']}" for k in keys)
        keys_block = "\n   可用语义 key 清单（script.target.key 只能取这里的 key）：\n" + lines
    else:
        keys_block = "\n   （当前无可用语义 key 清单：gui/e2e 若无法用 key 表达，请改判 manual）"
    api_contract_block = _api_contract_block(project_id)  # api 用例的接口清单/无契约提示
    return f"""请基于以下需求，设计一份结构化测试点清单。

输出要求：
1. 覆盖多个维度：功能、边界、异常、兼容、性能（按需选取，不必每类都有）。
2. 每个测试点是一个对象，字段：
   - category：维度（功能/边界/异常/兼容/性能 之一）
   - title：一句话标题
   - steps：操作步骤（可多步，用换行分隔；给人读）。**只写与本测试点直接相关的操作与断言**；不要写“刷新页面”“确认在首页/确认已进入主界面”“确保已登录”这类环境确认或复位描述（默认已登录、页面就绪，直接从进入目标页开始）。
   - expected：预期结果
   - priority：优先级（P0/P1/P2/P3，判定标准见下）
   - kind：自动化执行类型，只能是 gui/api/cli/e2e/manual 之一（判定规则见下）
   - kind_reason：一句话说明为何判该 kind
   - script:**gui/e2e 给界面步骤数组、api 给请求-断言-提取数组**(schema 各见下);cli/manual 一律给 []
3. priority 判定规则（按"失败后果的严重性"定级，不要随意打分）：
   - P0：核心主流程 / 一旦失败即阻断使用或造成数据错误（如登录、支付、下单、提交保存主数据）。
   - P1：重要功能 / 常见路径上的异常与校验（如必填校验、关键按钮不可用、主功能的边界）。
   - P2：次要功能、一般边界场景，以及**文案、样式、提示语、界面美观**类问题（文案/样式一律 P2）。
   - P3：极端罕见场景 / 影响面很小的细节。
4. kind 判定规则：
   - gui：在被测客户端界面上点击/输入/断言某元素或文案（单点、一两步）
   - api：调接口、校验响应码/响应体
   - cli：跑命令行、校验退出码/输出
   - e2e：跨多个界面步骤的端到端流程（如登录→进入某页→操作→验证结果），比单点 gui 长
   - manual：仅当验证点**依赖人的主观判断、无法给出客观断言**时才用（如"页面美观""交互流畅""体验是否顺滑"这类无明确可断言元素的）。
   **自动化优先原则（重要——直接决定自动化执行率、减轻人力，请认真执行）**：
   - 凡能"在被测客户端界面上操作、并对结果做客观断言"的验证点，一律**优先判 gui/e2e**，不要动辄判 manual：单点/局部验证判 gui，跨界面的流程判 e2e。
   - 后端/接口行为若在界面上有可观察结果（如"创建后列表出现该项""删除后该项消失""出错时界面弹出某提示文案"），**优先设计成 gui/e2e**（在界面触发操作并断言界面结果），而不是判 api。
   - 只有确无界面入口可验证、且非主观感受时，才考虑 api/cli；连客观断言都给不出的，才判 manual。
5. script（gui/e2e）——有序步骤数组，每步一个对象 {{action, target?, args?, desc}}：
   - action 只能取：connect（第一步必须，连接客户端）、click、hover（鼠标悬停到元素，触发悬浮态）、fill、wait_for、wait_response（发消息后等 AI 回复生成完成，e2e 用）、get_text、assert_text、assert_visible、screenshot
   - target：定位元素，**优先用语义 key**：{{"key":"<下方清单里的 key>"}}；清单没有的元素才用 {{"selector":"<CSS>"}}
   - **hover 用于"悬停才显示"的元素**（如列表项 hover 后才出现的更多/菜单按钮、悬浮提示 tooltip）：先 hover 到承载元素，再 wait_for 等浮层出现，然后 click/assert；hover 本身不做断言
   - **wait_for 是"等某个元素出现"，必须带 target（key 或 selector）**——它不是纯计时等待；只想等异步结果（发消息/提交后等生成）用 wait_response，不要写没有 target 的 wait_for
   - args：assert_text 用 {{"expected":"...","contains":true}}；fill 用 {{"text":"..."}}；wait_for 用 {{"timeout_ms":6000}}（超时上限，仍需配 target）
   - desc：该步人读说明
   - **每条 gui/e2e 至少有一个 assert_text 或 assert_visible**（否则没有判定依据，应改判 manual）
   - target.key 优先取下方清单里的 key。**清单里没有合适 key 时**：不要瞎编 selector、也不要直接判 manual——给该元素起一个语义化新 key 名（如 submitOrderBtn），照常写进 script，并在该步 desc 里**描述这个元素**（可见文案 / 角色 / 页面位置）。用到未注册 key 的用例会被自动标为「选择器待补」，补齐后即可自动执行；只有确无界面元素可操作/断言时才判 manual、script=[]。
   - **用例自治（关键——直接决定连续执行成功率，务必执行）**：多条用例在**同一客户端、同一页面**上连续执行，执行器不会在用例之间重置页面。每条用例必须能**单独、从初始态、一步步执行到底**，不得依赖上一条遗留的页面状态。按「进入→执行」两段组织：
     · **进入（不假设当前页）**：connect 后先用导航/入口类 key（如 navHome/navTasks，见下方清单）**显式进入本用例目标功能页**，再开始操作；不要假设“当前已在该页”。默认起点为**已登录的应用主界面**（登录流程单列为一条 e2e 用例，其它用例不重复写登录步）。清单无对应导航 key 时，按上一条缺 key 规则起语义化 key 名 + desc 描述该导航元素。**自治靠每条用例开头这一步自导航保证——下一条用例进来会自己导航到位，故不需要在结尾做任何还原/回起点步。**
     · **进入段只写一步真实导航动作**（一个 click 导航 key），**严禁**出现下列"环境确认/复位"类步骤：❌“刷新页面 / 重新加载”❌“确认当前在首页 / 确认已在首页 / 校验处于主界面”❌“确保已登录 / 检查登录状态”❌“回到首页后再开始”。这些都不是被测点、且锚点不稳会拖垮整条用例——直接 connect→导航到目标页即可，不做任何页面状态的前置确认或刷新。
     · **执行**：完成本用例的操作与断言。**用例到此为止，不要再加“关闭弹窗/清空输入/导航回首页”之类的收尾还原步**（这类结尾步常因锚点不稳而整条失败）。
6. 按 kind 的 script 编写偏重（**务必区分，别把 e2e 写成 gui**）：
   - **gui**：单点/局部验证，但**仍需自治**——结构为「进入导航 + 单点操作/断言」，通常 **2–5 步**（含进入步；不写收尾还原步）。断言聚焦单点，不要串联整条业务流程。
   - **e2e**：**端到端多步流程，通常 ≥5 步**，从已登录主界面**导航进入 → 操作 → 关键节点分别断言**。必须串联多个界面动作，并在**关键节点分别断言**（不止最后断一次）；不写收尾还原步。
     · 若流程中触发了 AI 生成/异步加载（发消息、提交后等结果），**必须插入 wait_response 或 wait_for** 再断言，不能立刻断。
     · 一条 e2e 的 script 明显比 gui 长、动作更丰富；若你发现某"e2e"剔除进入步后只需 2–3 步就能验完，说明它其实是 gui，请改判 kind=gui。
   - **判定自检**：kind=e2e 但剔除进入步后实质交互不足或总步数过短 → 改判 gui。
   正例(gui,单点,含进入)：connect → click(navTasks) → wait_for(任务页锚点) → assert_visible(目标元素)
   正例(e2e,多步,含进入)：connect → click(navTasks) → click(新建按钮) → fill(表单字段) → click(提交) → wait_for(结果锚点) → assert_text(结果文案,contains)
   登录单列(其它用例默认已登录)：connect → fill(loginUserName) → fill(loginPassword) → click(loginAgree) → click(loginSubmit) → wait_for(homepageTitle) → assert_visible(homepageTitle)
{keys_block}
7. {_API_SCRIPT_SPEC}
{api_contract_block}
8. 只输出一个 JSON 数组，不要任何解释文字，不要 markdown 代码块标记。
9. 数量随需求复杂度伸缩：一般 8-20 条；简单需求可少于 8 条，复杂需求可到 30 条。聚焦关键路径与高风险场景，**不要为凑数写重复或无价值的用例**。
10. 各测试点应相互正交：不同用例覆盖不同的验证点，不要用不同措辞重复验证同一件事。
11. 边界/异常类用例的 steps 要给出**具体示例数据**（如手机号填 "13800138000"、金额填 "-1"、超长字符串给出长度），不要只写"输入无效值"这类空泛描述。

需求内容：
<requirement>
{requirement}
</requirement>"""


def _build_cmd(prompt: str) -> list[str]:
    cmd = [
        _claude_bin(), "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", _SYSTEM_PROMPT,
        "--disallowedTools", *_DISALLOWED_TOOLS,
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    if settings.AI_MODEL:
        cmd += ["--model", settings.AI_MODEL]
    return cmd


def build_script_prompt(kind: str, title: str, steps: str, expected: str, project_id: int | None = None) -> str:
    """把单条用例转成"只产出该用例结构化 script"的指令。

    gui/e2e 注入选择器 key 清单;api 注入请求-断言-提取规范段 + 项目 api 契约。
    """
    if kind == "api":
        return f"""为下面这条 api 测试用例设计**可执行的结构化 script**(请求-断言-提取原子)。

用例:
- 标题:{title}
- 步骤:{steps or '(无)'}
- 预期:{expected or '(无)'}

输出要求:
1. 只输出一个 JSON 数组(script),不要任何解释、不要 markdown 代码块标记。
2. 数组每步的结构与规则:
{_API_SCRIPT_SPEC}
{_api_contract_block(project_id)}"""
    keys = _load_selector_keys(project_id)
    lines = "\n".join(f"   - {k['key']}({k['frame']}):{k['desc']}" for k in keys) if keys else "   (无可用 key)"
    return f"""为下面这条 {kind} 测试用例设计**可执行的结构化步骤 script**。

用例:
- 标题:{title}
- 步骤:{steps or '(无)'}
- 预期:{expected or '(无)'}

输出要求:
1. 只输出一个 JSON 数组(script),不要任何解释、不要 markdown 代码块标记。
2. 每步一个对象 {{action, target?, args?, desc}}:
   - action 只能取:connect(第一步必须)、click、hover(鼠标悬停,触发悬浮态)、fill、wait_for、wait_response(发消息后等 AI 回复)、get_text、assert_text、assert_visible、screenshot
   - target:优先 {{"key":"<下方清单里的 key>"}};清单没有合适 key 时,起语义化新 key 名并在 desc 描述该元素(可见文案/角色/位置),走「选择器待补」,不要臆造 selector
   - **hover 用于"悬停才显示"的元素**(列表项 hover 出的更多/菜单按钮、tooltip):先 hover 承载元素→wait_for 等浮层→再 click/assert;hover 本身不断言
   - **wait_for 是"等某个元素出现",必须带 target(key 或 selector)**——它不是纯计时等待;只想等异步结果(发消息/提交后等生成)用 wait_response,不要写没有 target 的 wait_for
   - args:assert_text 用 {{"expected":"...","contains":true}};fill 用 {{"text":"..."}};wait_for 用 {{"timeout_ms":6000}}(超时上限,仍需配 target)
   - desc:该步人读说明
   - **至少含一个 assert_text 或 assert_visible**(否则无判定依据)
   - {'e2e:多步端到端(≥5 步)、跨界面串联、异步处插 wait_response' if kind == 'e2e' else 'gui:单点聚焦,含进入通常 2-5 步'}
   - **用例自治**:connect 后先用导航/入口 key 显式进入目标页(不假设当前页,默认已登录主界面),自治靠这一步自导航保证;**用例到操作与断言为止,不要加关弹窗/清输入/导航回首页之类的收尾还原步**(结尾还原步常因锚点不稳而整条失败)
   - **进入段只写一步真实导航动作**,**严禁**"刷新页面/重新加载""确认当前在首页/确认已在主界面""确保已登录/检查登录状态""回到首页再开始"这类环境确认或复位步——它们不是被测点且锚点不稳,直接 connect→导航到目标页,不做任何页面状态前置确认或刷新
3. target.key 优先取下方清单里的 key(**清单里已有能表达该元素的 key 必须直接复用其 key 名,不要为同一元素另造新名字**,否则重生后仍会缺 key);清单无合适 key 时起语义化新 key 名 + desc 描述元素(走「选择器待补」),不要臆造 selector:
{lines}"""


def generate_script(kind: str, title: str, steps: str, expected: str, project_id: int | None = None, timeout: int | None = None) -> tuple[list, str | None]:
    """同步调 claude 为单条用例生成 script。返回 (script列表, 错误)。

    校验按 kind 分流:gui/e2e → _validate_script(选择器 key);api → _validate_api_script。
    """
    if not is_available():
        return [], "AI 功能未启用或未找到 claude 可执行文件"
    if kind not in ("gui", "e2e", "api"):
        return [], "仅 gui/e2e/api 用例支持生成 script"
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    prompt = build_script_prompt(kind, title, steps or "", expected or "", project_id)
    cmd = [
        _claude_bin(), "-p", prompt, "--output-format", "json",
        "--append-system-prompt", _SYSTEM_PROMPT,
        "--disallowedTools", *_DISALLOWED_TOOLS,
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    if settings.AI_MODEL:
        cmd += ["--model", settings.AI_MODEL]
    if not _slots.acquire(blocking=False):
        return [], "AI 生成繁忙(已达并发上限),请稍后重试"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return [], f"生成超时(>{timeout}s)"
    except OSError as e:
        return [], f"启动 claude 失败:{e}"
    finally:
        _slots.release()
    # --output-format json:最终文本在信封的 result 字段
    raw = proc.stdout or ""
    try:
        env = json.loads(raw)
        text = env.get("result", "") if isinstance(env, dict) else raw
    except (json.JSONDecodeError, ValueError):
        text = raw
    # 抽取 JSON 数组(容错 fence / 裸[])并校验
    m = _FENCE_RE.search(text)
    blob = m.group(1) if m else None
    if blob is None:
        s, e = text.find("["), text.rfind("]")
        blob = text[s:e + 1] if (s != -1 and e > s) else None
    if not blob:
        return [], "未解析出 script 数组"
    try:
        arr = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return [], "script JSON 解析失败"
    if kind == "api":
        script, err = _validate_api_script(arr)
    else:
        script, err = _validate_script(arr, _registered_keys(project_id))
    if err:
        return [], f"生成的 script 不合法:{err}"
    return script, None


def revalidate_for_backfill(script, project_id: int | None = None) -> tuple[list, str | None]:
    """用当前注册表重新校验一份已存的 gui/e2e script(供「选择器待补」重生时确定性回填)。

    返回 (规范化步骤, 错误)。err is None 表示 script 引用的 key 现已全部注册、结构合法
    → 可直接回填、无需再调 AI(避免 AI 盲重写导致 key 名漂移、反复降级);err 非空则调用方
    落 AI 兜底。script 为空/非数组时 _validate_script 亦返回错误。
    """
    return _validate_script(script, _registered_keys(project_id))


def validate_script_for_edit(kind: str, script, project_id: int | None = None, db=None) -> tuple[list, str | None]:
    """校验人工编辑后的 script,按 kind 分流(与生成侧同一批校验器,口径一致)。

    返回 (规范化步骤, 错误说明);err is None 表示合法可入库。
    - gui/e2e → _validate_script + 注册表「可用 key」集(usable_key_set):action 合法、定位步带
      target、至少一个断言、key 必须已注册且候选有效。传了活 db 直接用之(编辑侧 db 未关);
      否则回落 _registered_keys(自开 session)。
    - api → _validate_api_script:请求-断言-提取原子校验(auth_vars 默认空,与生成侧一致)。
    - 其它 kind(manual/cli):不支持结构化 script → 返回错误(调用方拒绝)。
    """
    if kind in ("gui", "e2e"):
        if db is not None:
            from app.services.selectors import usable_key_set
            valid_keys = usable_key_set(db, project_id) if project_id else set()
        else:
            valid_keys = _registered_keys(project_id)
        return _validate_script(script, valid_keys)
    if kind == "api":
        return _validate_api_script(script)
    return [], f"仅 gui/e2e/api 用例支持编辑 script(当前 {kind})"


def _parse_line(line: str) -> dict | None:
    """把一行 stream-json 解析为对外事件；非目标事件返回 None（跳过）。

    - assistant 文本 → {"type":"delta","text":...}
    - 最终 result   → {"type":"result", text/duration_ms/cost_usd/output_tokens/is_error}
    - system/user/thinking/其它 → None
    """
    line = line.strip()
    if not line:
        return None
    try:
        evt = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    etype = evt.get("type")
    if etype == "assistant":
        parts = [
            b.get("text", "")
            for b in evt.get("message", {}).get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        text = "".join(parts)
        return {"type": "delta", "text": text} if text else None
    if etype == "result":
        usage = evt.get("usage") or {}
        return {
            "type": "result",
            "text": evt.get("result", "") or "",
            "duration_ms": evt.get("duration_ms"),
            "cost_usd": evt.get("total_cost_usd"),
            "output_tokens": usage.get("output_tokens"),
            "is_error": bool(evt.get("is_error", False)),
        }
    return None


def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None, pages: list[str] | None = None, prompt_builder=None) -> Iterator[dict]:
    """流式生成测试点。yield 事件 dict：delta / result / error。

    调用方（api 层）负责累积文本、落库、转 SSE。生成器自然结束即代表流结束。
    project_id 透传给 prompt 构造,决定注入哪个项目的 key 清单。
    pages 非空则只注入这些页面的 key(收窄),减少噪声与降级。
    prompt_builder 非空则用它(无参调用)构造 prompt,否则默认生成测试点 prompt。
    """
    if not is_available():
        yield {"type": "error", "msg": "AI 功能未启用或未找到 claude 可执行文件"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)
    cmd = _build_cmd(prompt)

    if not _slots.acquire(blocking=False):
        yield {"type": "error", "msg": "AI 生成繁忙（已达并发上限），请稍后重试"}
        return

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # 合并，非 JSON 行由 _parse_line 忽略，避免 PIPE 死锁
            text=True,
            bufsize=1,
            cwd=tempfile.gettempdir(),  # 隔离：不在项目目录运行，避免读到 CLAUDE.md/触发 project hook
        )
    except OSError as e:
        _slots.release()
        logger.exception("启动 claude 失败")
        yield {"type": "error", "msg": f"启动 claude 失败：{e}"}
        return

    q: Queue = Queue()

    def _reader():
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_reader, daemon=True).start()

    tail = deque(maxlen=20)   # 保留最近的非目标输出，失败时帮助定位
    got_result = False
    start = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                proc.kill()
                yield {"type": "error", "msg": f"生成超时（>{timeout}s）"}
                return
            try:
                line = q.get(timeout=min(remaining, 3))
            except Empty:
                if proc.poll() is not None and q.empty():
                    break
                # 空转(claude 还在思考、未吐字):发心跳,避免反向代理/网关按"空闲"掐断长连接
                # (SSE 一个字节没动 → 常见 60s 空闲超时 → 前端"读取流失败")。端点转成 SSE 注释帧。
                yield {"type": "heartbeat"}
                continue
            if line is None:
                break
            evt = _parse_line(line)
            if evt is None:
                stripped = line.strip()
                if stripped:
                    tail.append(stripped[:500])
                continue
            if evt["type"] == "result":
                got_result = True
            yield evt
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        _slots.release()

    if not got_result:
        # 没拿到 result：多为 CLI 报错/异常退出，附最近输出片段便于排查
        detail = " | ".join(list(tail)[-3:]) or "无输出"
        logger.warning("claude 未返回 result，tail=%s", detail)
        yield {"type": "error", "msg": f"生成未完成：{detail}"}


_FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*\])\s*```", re.S)


def _salvage_objects(text: str) -> list[dict]:
    """从文本里逐个抠出**平衡的** {...} 块并 json.loads,保留成功的 dict。

    整体数组解析失败时的兜底:容忍数组被截断(尾部半个对象)、前后有多余文本、
    个别对象格式坏——扫描时按花括号配平(且跳过字符串内的括号/转义),
    未闭合或解析失败的块直接丢弃,"能救一条是一条"。
    """
    out: list[dict] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(text[i:j + 1])
                        if isinstance(o, dict):
                            out.append(o)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
            j += 1
        i = j + 1   # 从该块之后继续(嵌套对象不会被重复抠)
    return out


def _extract_cases_array(raw: str) -> list:
    """从模型输出稳健提取用例对象数组。多重兜底,最大化"抠出用例":

    ① ```json fence 内数组(贪婪到最后一个 ])  ② 全文首个 [ 到末个 ]  ③ 逐个平衡 {...} salvage。
    ①② 按整体 json.loads;都失败(截断/多余文本/坏对象)才 salvage,避免"模型产出了却全丢"。
    """
    candidates = []
    m = _FENCE_RE.search(raw)
    if m:
        candidates.append(m.group(1))
    s, e = raw.find("["), raw.rfind("]")
    if s != -1 and e > s:
        candidates.append(raw[s:e + 1])
    for blob in candidates:
        try:
            arr = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(arr, list) and arr:
            return arr
    return _salvage_objects(raw)


# 对话测评维度:值 → 生成引导说明(供 build_eval_query_prompt 拼进 prompt)
EVAL_DIMENSIONS = {
    "thinking": "需要多步推理/规划才能回答的问题,考查思考过程是否完整、有条理",
    "tool_use": "需要联网搜索或调用工具(含 MCP 工具)才能完成的任务,考查工具调用是否正常、结果是否被正确使用",
    "artifact": "要求产出网页/文件/代码/文档等交付物的任务,考查产物是否符合预期",
    "multi_turn": "需要多轮对话逐步澄清/细化的场景,考查上下文连贯性(这类应产出同一 conversation_group 下的多条,turn_index 递增)",
    "instruction": "带明确约束或格式要求的任务,考查是否严格遵循指令",
}


def build_eval_query_prompt(requirement: str, dimensions: list[str]) -> str:
    """构造"生成对话测评 query"的 prompt。产物是发给被测大模型的对话提问,不是功能测试点。
    不注入 selector key / api 契约 / script DSL(那些是测试点特有)。
    """
    valid = [d for d in (dimensions or []) if d in EVAL_DIMENSIONS] or ["thinking"]
    dim_lines = "\n".join(f"- {d}: {EVAL_DIMENSIONS[d]}" for d in valid)
    return f"""你是"AI 对话能力测评"的出题专家。基于下面的需求文档,生成一批"对话测评 query"——
即拿去发给被测大模型(如 Claude、codex 等 Agent)对话、用来考查其对话能力的提问。

要覆盖的测评维度(按这些维度出题,尽量均衡覆盖):
{dim_lines}

严格输出一个 JSON 数组,不要任何数组之外的解释文字。每个元素:
{{
  "title": "题目摘要(<=50字)",
  "prompt": "发给被测大模型的完整提问正文(必填,这是要真正发出去对话的内容)",
  "dimension": "该题主考的维度,取值必须是: {", ".join(valid)} 之一",
  "expected": "期望被测模型产出什么或做到什么(用于后续判定的参照,要具体、可核对)",
  "attachments": [],
  "conversation_group": "会话分组名。单轮题给独立唯一名(如 g1/g2);多轮题同一对话的多条用相同名",
  "turn_index": 0
}}

多轮说明:multi_turn 维度的题,把一个对话意图拆成多条,conversation_group 相同、turn_index 从 0 递增
(0=首轮提问,1/2=追问)。单轮题 turn_index 恒为 0、各自独立 conversation_group。
attachments 一般为空数组 [];仅当需求明确涉及上传文件/图片时才给出 [{{"name":"...","url":"..."}}]。
不要输出 dialog_options 等执行参数。

<requirement>
{requirement}
</requirement>"""


_EVAL_DIM_VALUES = set(EVAL_DIMENSIONS.keys())


def parse_eval_queries(raw: str) -> list[dict]:
    """把模型输出解析成对话测评 query dict 列表。复用 _extract_cases_array 的多重兜底提取;
    字段映射为 query 结构,不走 script/selector 校验。丢弃无 prompt 或无 title 的条目。
    """
    arr = _extract_cases_array(raw)
    out: list[dict] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        prompt = (item.get("prompt") or "").strip()
        if not title or not prompt:
            continue  # 无题干或无提问正文的条目无意义,丢弃
        dim = item.get("dimension")
        if dim not in _EVAL_DIM_VALUES:
            dim = None  # 非法维度置空(不猜)
        # turn_index 归一为非负整数
        ti = item.get("turn_index", 0)
        try:
            ti = max(0, int(ti))
        except (TypeError, ValueError):
            ti = 0
        # attachments 归一为 list
        att = item.get("attachments")
        if not isinstance(att, list):
            att = []
        cg = item.get("conversation_group")
        cg = cg.strip() if isinstance(cg, str) and cg.strip() else None
        out.append({
            "title": title[:512],
            "prompt": prompt,
            "dimension": dim,
            "expected": (item.get("expected") or "").strip() or None,
            "attachments": att,
            "conversation_group": cg,   # None → 落库时补唯一组名
            "turn_index": ti,
        })
    return out


def parse_testcases(raw: str, project_id: int | None = None) -> list[dict]:
    """从模型输出全文中提取结构化测试点数组。

    容错顺序：markdown ```json fence → 裸 [ ... ]。字段缺失给空串，超长截断，
    丢弃无 title 的条目。解析失败返回空列表（api 层据此判定，但仍保留 output_raw）。
    project_id:决定用哪个项目的注册表校验 script.target.key(空则不校验)。
    """
    if not raw:
        return []
    arr = _extract_cases_array(raw)   # 稳健提取:fence→裸[]→逐对象 salvage(容忍截断/坏对象)
    if not arr:
        return []
    out = []
    _VALID_KINDS = {"gui", "api", "cli", "e2e", "manual"}
    valid_keys = _registered_keys(project_id)   # 读一次注册表,供本批所有 gui/e2e 校验 target.key
    key_page_map = _key_page_map(project_id)     # 读一次 key→page,供按 script 用到的 key 自动打页面标
    for it in arr:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()[:512]
        if not title:
            continue
        # kind:模型给的若不在合法集内(或漏给)→ 兜底 manual(宁可人工复核,不可误派执行机)
        kind = str(it.get("kind") or "").strip().lower()
        if kind not in _VALID_KINDS:
            kind = "manual"
        # script:gui/e2e 校验界面步骤 + key 合法性;api 校验请求-断言-提取原子。
        # 非法则该用例降级 manual(不派坏 script 给执行机)。
        kind_reason = str(it.get("kind_reason") or "").strip()[:500]
        script_json = None
        if kind in ("gui", "e2e"):
            intended = kind   # 记住原意图(gui/e2e),供"选择器待补"提示
            script, err = _validate_script(it.get("script"), valid_keys)
            if err:
                kind = "manual"  # script 不合法/缺失/含未注册 key → 保守降级,避免执行机拿到坏 script
                # 标识"仅因选择器缺失而降级":收集脚本引用但未注册的 key。
                # 若补齐这些 key 后能通过校验 → 明确告知"补齐即可执行",否则注明仍有其它问题。
                missing = _unregistered_keys(it.get("script"), valid_keys)
                if missing:
                    keys_txt = ", ".join(missing)
                    # 用"补齐这些 key"后的集合重校验:通过 → 仅缺 key(补齐即可执行),把这份规范化 script
                    # 保留下来(不再丢成 None),供重生时确定性回填——避免重生走 AI 盲重写导致 key 名漂移、
                    # 反复降级(同批次多条缺同一 key 时,补一次即可让每条按各自旧 script 回填)。
                    norm2, err2 = _validate_script(it.get("script"), (valid_keys or set()) | set(missing))
                    if err2 is None:
                        kind_reason = f"{_SELECTOR_FIX_MARK} 补齐选择器 key:{keys_txt} 后即可执行 {intended}"[:500]
                        script_json = json.dumps(norm2, ensure_ascii=False)
                    else:
                        kind_reason = f"{_SELECTOR_FIX_MARK} 缺选择器 key:{keys_txt}(补齐后仍需修其它问题,目标 {intended})"[:500]
            elif script:
                # e2e 名不副实纠偏:e2e 应是多步端到端。若步数太少或只有 connect+断言(无实质交互),
                # 说明它其实是单点 gui → 改判 gui,确保"勾选的用例按其真实类型执行"。
                if kind == "e2e" and not _looks_like_e2e(script):
                    kind = "gui"
                script_json = json.dumps(script, ensure_ascii=False)
        elif kind == "api":
            script, err = _validate_api_script(it.get("script"))
            if err:
                kind = "manual"  # api script 非法/缺失/变量不闭环/写操作缺清理 → 降级 manual
            elif script:
                script_json = json.dumps(script, ensure_ascii=False)
        out.append({
            "category": str(it.get("category") or "").strip()[:32],
            "title": title,
            "steps": str(it.get("steps") or "").strip(),
            "expected": str(it.get("expected") or "").strip(),
            "priority": str(it.get("priority") or "").strip()[:8],
            "kind": kind,
            "kind_reason": kind_reason,
            "script": script_json,
            "page": _pages_for_script(script_json, key_page_map) or None,  # 按 script 用到的 key 反查页面
        })
    return out


_VALID_ACTIONS = {"connect", "click", "hover", "fill", "wait_for", "wait_response", "get_text", "assert_text", "assert_visible", "screenshot"}

# gui/e2e 因"选择器未注册"降级 manual 时的 kind_reason 前缀标识。
# 前端据此前缀渲染「补选择器可自动化」标签(见 CaseLibrary.vue),故改此串须同步前端。
_SELECTOR_FIX_MARK = "[选择器待补]"


def _unregistered_keys(script, valid_keys) -> list[str]:
    """收集 script 里引用了、但不在 valid_keys 注册表内的 target.key(去重保序)。

    供"选择器待补"标识:知道补哪几个 key 就能把该用例救成可执行 gui/e2e。
    valid_keys 为空(注册表读不到)时返回 []——此时 _validate_script 本就跳过 key 校验,
    不会因 key 降级,故不构成"选择器待补"。
    """
    if not valid_keys or not isinstance(script, list):
        return []
    missing: list[str] = []
    for st in script:
        if not isinstance(st, dict):
            continue
        tgt = st.get("target")
        k = tgt.get("key") if isinstance(tgt, dict) else None
        if k and k not in valid_keys and k not in missing:
            missing.append(k)
    return missing


# 从 kind_reason 抽"缺哪些 key"/"原意图类型"的正则(与 parse_testcases 写入格式配套)。
_SEL_FIX_KEYS_RE = re.compile(r"key[:：]\s*(.*?)(?:后即可|[（(]|$)")
_SEL_FIX_KIND_RE = re.compile(r"(?:执行|目标)\s*(gui|e2e)")


def selector_fix_info(kind_reason: str | None) -> tuple[bool, list[str], str | None]:
    """解析 kind_reason 是否为「选择器待补」降级 + 缺的 key 列表 + 原意图类型(gui/e2e)。

    与 parse_testcases 写入的 _SELECTOR_FIX_MARK 格式配套(单一事实来源,改格式两处同步)。
    非该标识 → (False, [], None)。intended 供 gen-script 一键按原意图重生。
    """
    if not kind_reason or not str(kind_reason).startswith(_SELECTOR_FIX_MARK):
        return False, [], None
    text = str(kind_reason)
    m = _SEL_FIX_KEYS_RE.search(text)
    keys = [k.strip() for k in re.split(r"[,，、]", m.group(1))] if m else []
    km = _SEL_FIX_KIND_RE.search(text)
    return True, [k for k in keys if k], (km.group(1) if km else None)


def _validate_script(script, valid_keys: set[str] | None = None) -> tuple[list, str | None]:
    """校验 gui/e2e 的 script。返回 (规范化步骤列表, 错误说明)。

    规则:必须是非空数组;每步 action 合法;定位类步骤要有 target.key 或 target.selector;
    至少含一个 assert_text/assert_visible(否则无判定依据)。任一不满足 → 返回错误(调用方降级 manual)。

    valid_keys:注册表里的合法 key 集合。传入且非空时,校验每个 target.key 必须在其中
    (拦截模型瞎编的 key,把问题挡在生成阶段,而非等下发到设备执行才 fail)。
    传 None 或空集则跳过 key 校验(注册表读不到时不误伤)。
    """
    if not isinstance(script, list) or not script:
        return [], "script 缺失或非数组"
    has_assert = False
    norm = []
    for st in script:
        if not isinstance(st, dict):
            return [], "step 非对象"
        action = str(st.get("action") or "").strip()
        if action not in _VALID_ACTIONS:
            return [], f"非法 action「{action}」"
        target = st.get("target") or {}
        if action in ("click", "hover", "fill", "wait_for", "get_text", "assert_text", "assert_visible"):
            if not (isinstance(target, dict) and (target.get("key") or target.get("selector"))):
                return [], f"step「{action}」缺 target.key/selector"
            # key 必须在注册表内(仅当提供了 valid_keys 且非空);selector(裸 CSS)不校验
            k = isinstance(target, dict) and target.get("key")
            if k and valid_keys and k not in valid_keys:
                return [], f"step「{action}」用了未注册的 key「{k}」(不在 selectors.json 注册表内)"
        if action == "assert_text" and not (st.get("args") or {}).get("expected"):
            return [], "assert_text 缺 args.expected"
        if action in ("assert_text", "assert_visible"):
            has_assert = True
        norm.append({"action": action, "target": target, "args": st.get("args") or {}, "desc": str(st.get("desc") or "")[:200]})
    if not has_assert:
        return [], "无任何断言步骤(assert_text/assert_visible)"
    return norm, None


# e2e 应是"多步端到端":足够长 + 含实质交互动作(click/fill/wait_response),而非只有 connect+断言。
_INTERACTION_ACTIONS = {"click", "fill", "wait_response"}


def _looks_like_e2e(script: list) -> bool:
    """判断 script 是否够格叫 e2e。不够则调用方改判 gui。

    门槛:≥5 步 且 至少 2 个实质交互动作(click/fill/wait_response)。
    只有 connect+wait_for+assert 这种"看一眼某元素"的,再长也算单点 gui。
    """
    if len(script) < 5:
        return False
    interactions = sum(1 for s in script if s.get("action") in _INTERACTION_ACTIONS)
    return interactions >= 2


# ---- api script 校验(请求-断言-提取原子;镜像 api-executor.mjs 的执行契约)----
_API_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_API_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_API_ASSERT_TYPES = {"status", "jsonpath"}
_API_OPS = {"eq", "neq", "exists", "contains", "gt", "lt", "regex", "type"}
# 除 exists 外都需要 value(exists 只判字段在不在,无参照值)
_API_OPS_NEED_VALUE = _API_OPS - {"exists"}
_VAR_REF_RE = re.compile(r"\{\{(\w+)\}\}")


def _collect_var_refs(value) -> set[str]:
    """递归收集一个值(str/dict/list)里所有 {{var}} 引用名(用于变量闭环校验)。"""
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(_VAR_REF_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            refs |= _collect_var_refs(v)
    elif isinstance(value, list):
        for v in value:
            refs |= _collect_var_refs(v)
    return refs


def _validate_api_script(script, auth_vars: set[str] | None = None) -> tuple[list, str | None]:
    """校验 api 用例的 script(请求-断言-提取原子)。返回 (规范化步骤, 错误说明)。

    规则(设计稿 §7.2):
    - 非空数组;每步是对象;request.method 合法、request.path 非空;
    - 每步 asserts 非空;每断言 type∈{status,jsonpath}、op 合法;jsonpath 必须有 path;
      需值的 op(除 exists)必须有 value;
    - **变量引用闭环**:request 里任何 {{var}} 必须在**之前某步的 extract** 里定义过
      (或来自 auth_vars 固定注入);引用未定义变量 → 非法(把 api 版"未注册 key"挡在生成阶段);
    - **写操作清理**:含写方法(POST/PUT/PATCH/DELETE)的非清理步,但整段无任何 cleanup:true 步 → 非法。
    任一不满足返回错误,调用方降级 manual(不派坏 script 到执行机)。

    auth_vars:执行器在开跑前已可用的变量名(如登录预置);当前 fixed 鉴权靠预置 header
    而非变量,login 鉴权靠用例内登录步 extract,故默认空集即可。
    """
    if not isinstance(script, list) or not script:
        return [], "script 缺失或非数组"
    defined: set[str] = set(auth_vars or set())   # 已定义变量(先 extract / 鉴权注入)
    has_write = False
    has_cleanup = False
    norm = []
    for idx, st in enumerate(script):
        pos = idx + 1
        if not isinstance(st, dict):
            return [], f"第 {pos} 步非对象"
        req = st.get("request")
        if not isinstance(req, dict):
            return [], f"第 {pos} 步缺 request 对象"
        method = str(req.get("method") or "").strip().upper()
        if method not in _API_METHODS:
            return [], f"第 {pos} 步非法 method「{req.get('method')}」"
        path = str(req.get("path") or "").strip()
        if not path:
            return [], f"第 {pos} 步 request.path 为空"
        is_cleanup = bool(st.get("cleanup"))
        if is_cleanup:
            has_cleanup = True
        elif method in _API_WRITE_METHODS:
            has_write = True
        # 变量引用闭环:本步 request 引用的 {{var}} 必须已定义(extract 在发请求后,故检查在登记前)
        undefined = _collect_var_refs(req) - defined
        if undefined:
            return [], f"第 {pos} 步引用未定义变量 {sorted(undefined)}(须在之前步骤 extract 或鉴权注入)"
        # asserts 校验
        asserts = st.get("asserts")
        if not isinstance(asserts, list) or not asserts:
            return [], f"第 {pos} 步 asserts 为空(无判定依据)"
        norm_asserts = []
        for a in asserts:
            if not isinstance(a, dict):
                return [], f"第 {pos} 步 assert 非对象"
            atype = str(a.get("type") or "").strip()
            if atype not in _API_ASSERT_TYPES:
                return [], f"第 {pos} 步非法断言 type「{atype}」"
            op = str(a.get("op") or "").strip()
            if op not in _API_OPS:
                return [], f"第 {pos} 步非法断言 op「{op}」"
            if atype == "jsonpath" and not str(a.get("path") or "").strip():
                return [], f"第 {pos} 步 jsonpath 断言缺 path"
            if op in _API_OPS_NEED_VALUE and a.get("value") is None:
                return [], f"第 {pos} 步 op「{op}」缺 value"
            na = {"type": atype, "op": op}
            if atype == "jsonpath":
                na["path"] = str(a.get("path")).strip()
            if "value" in a:
                na["value"] = a.get("value")
            norm_asserts.append(na)
        # extract:登记新变量(供后续步骤引用)
        norm_extract = None
        extract = st.get("extract")
        if extract is not None:
            if not isinstance(extract, dict):
                return [], f"第 {pos} 步 extract 非对象"
            norm_extract = {}
            for k, p in extract.items():
                if not str(p or "").strip():
                    return [], f"第 {pos} 步 extract「{k}」路径为空"
                defined.add(str(k))
                norm_extract[str(k)] = str(p).strip()
        # 规范化步骤(只保留执行器认得的字段)
        nreq = {"method": method, "path": path}
        for opt in ("headers", "query", "body"):
            if req.get(opt) is not None:
                nreq[opt] = req.get(opt)
        step = {"name": str(st.get("name") or f"step{pos}")[:200], "request": nreq, "asserts": norm_asserts}
        if norm_extract:
            step["extract"] = norm_extract
        if is_cleanup:
            step["cleanup"] = True
        norm.append(step)
    if has_write and not has_cleanup:
        return [], "含写操作(POST/PUT/PATCH/DELETE)但无 cleanup 清理步骤(避免残留脏数据,应补末尾删除步)"
    return norm, None
