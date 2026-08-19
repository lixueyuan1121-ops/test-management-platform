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
    """项目级共享 key 的合法集合(供生成侧校验 script.target.key)。

    project_id 为空或读不到 → 返回空集。空集时校验放行(见 _validate_script),
    避免"读不到注册表就把所有 gui/e2e 全降 manual"这种误伤生成结果。
    """
    if not project_id:
        return set()
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_set
    s = SessionLocal()
    try:
        return shared_key_set(s, project_id)
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
   - steps：操作步骤（可多步，用换行分隔；给人读）
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
   - action 只能取：connect（第一步必须，连接客户端）、click、fill、wait_for、wait_response（发消息后等 AI 回复生成完成，e2e 用）、get_text、assert_text、assert_visible、screenshot
   - target：定位元素，**优先用语义 key**：{{"key":"<下方清单里的 key>"}}；清单没有的元素才用 {{"selector":"<CSS>"}}
   - args：assert_text 用 {{"expected":"...","contains":true}}；fill 用 {{"text":"..."}}；wait_for 用 {{"timeout_ms":6000}}
   - desc：该步人读说明
   - **每条 gui/e2e 至少有一个 assert_text 或 assert_visible**（否则没有判定依据，应改判 manual）
   - 只能用下方 key 清单里的 key；**找不到合适 key 表达该测试点 → 改判 kind=manual、script=[]**（不要瞎编 selector）
6. 按 kind 的 script 编写偏重（**务必区分，别把 e2e 写成 gui**）：
   - **gui**：单点/局部验证，**2–4 步**即可——connect → (最多一两个 click/fill/wait_for) → assert_*。聚焦"某一个元素/文案对不对"，不要串联整条业务流程。
   - **e2e**：**端到端多步流程，通常 ≥5 步**，体现"从入口一路操作到结果"。必须串联多个界面动作（如 登录→导航→输入→提交），并在**关键节点分别断言**（不止最后断一次）。
     · 若流程中触发了 AI 生成/异步加载（发消息、提交后等结果），**必须插入 wait_response 或 wait_for** 再断言，不能立刻断。
     · 一条 e2e 的 script 明显比 gui 长、动作更丰富；若你发现某"e2e"只需 2–3 步就能验完，说明它其实是 gui，请改判 kind=gui。
   - **判定自检**：kind=e2e 但 script 少于 5 步或无跨界面串联 → 要么补足步骤，要么改判 gui。
   正例(gui,单点)：connect → wait_for(navTasks) → assert_visible(navTasks)
   正例(e2e,多步)：connect → click(loginAccountTab) → fill(loginUserName) → fill(loginPassword) → click(loginSubmit) → wait_for(homepageTitle) → assert_visible(homepageTitle) → assert_text(homepageTitle,"早上好",contains)
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
   - action 只能取:connect(第一步必须)、click、fill、wait_for、wait_response(发消息后等 AI 回复)、get_text、assert_text、assert_visible、screenshot
   - target:优先 {{"key":"<下方清单里的 key>"}};清单没有的元素才用 {{"selector":"<CSS>"}}
   - args:assert_text 用 {{"expected":"...","contains":true}};fill 用 {{"text":"..."}};wait_for 用 {{"timeout_ms":6000}}
   - desc:该步人读说明
   - **至少含一个 assert_text 或 assert_visible**(否则无判定依据)
   - {'e2e:多步端到端(≥5 步)、跨界面串联、异步处插 wait_response' if kind == 'e2e' else 'gui:单点聚焦,2-4 步即可'}
3. 只能用下方 key 清单里的 key,找不到合适的就用最接近的语义 key 或 selector:
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


def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None, pages: list[str] | None = None) -> Iterator[dict]:
    """流式生成测试点。yield 事件 dict：delta / result / error。

    调用方（api 层）负责累积文本、落库、转 SSE。生成器自然结束即代表流结束。
    project_id 透传给 prompt 构造,决定注入哪个项目的 key 清单。
    pages 非空则只注入这些页面的 key(收窄),减少噪声与降级。
    """
    if not is_available():
        yield {"type": "error", "msg": "AI 功能未启用或未找到 claude 可执行文件"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    cmd = _build_cmd(build_testcase_prompt(requirement, project_id, pages))

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
                    if _validate_script(it.get("script"), (valid_keys or set()) | set(missing))[1] is None:
                        kind_reason = f"{_SELECTOR_FIX_MARK} 补齐选择器 key:{keys_txt} 后即可执行 {intended}"[:500]
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


_VALID_ACTIONS = {"connect", "click", "fill", "wait_for", "wait_response", "get_text", "assert_text", "assert_visible", "screenshot"}

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
        if action in ("click", "fill", "wait_for", "get_text", "assert_text", "assert_visible"):
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
