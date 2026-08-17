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


def _load_selector_keys(project_id: int | None = None) -> list[dict]:
    """项目级共享 key 清单(供 prompt 注入),返回 [{key, frame, desc}, ...]。

    DB 是唯一事实来源:走服务层读项目共享 key(sub_product='')。生成器脱离请求 db,
    故内部自开 SessionLocal 并关闭。project_id 为空或读不到 → 空列表(prompt 不注入 key 清单)。
    """
    if not project_id:
        return []
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_dicts
    s = SessionLocal()
    try:
        return shared_key_dicts(s, project_id)
    except Exception:
        logger.warning("读注册表失败(project_id=%s),prompt 不注入 key 清单", project_id)
        return []
    finally:
        s.close()


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


def build_testcase_prompt(requirement: str, project_id: int | None = None) -> str:
    """把需求文本包装成「生成结构化测试点」的指令。

    用 <requirement> 标签包裹用户输入（而非引号），避免内容里的引号破坏边界。
    强约束只输出 JSON 数组；即便模型仍包了 markdown fence，解析层也能兜底剥离。
    project_id:注入该项目共享 key 清单;为空则不注入(见 _load_selector_keys)。
    """
    # 注入语义 key 清单(供 gui/e2e 的 script.target.key 取值);读不到就给空块、只说明无可用 key
    keys = _load_selector_keys(project_id)
    if keys:
        lines = "\n".join(f"   - {k['key']}（{k['frame']}）：{k['desc']}" for k in keys)
        keys_block = "\n   可用语义 key 清单（script.target.key 只能取这里的 key）：\n" + lines
    else:
        keys_block = "\n   （当前无可用语义 key 清单：gui/e2e 若无法用 key 表达，请改判 manual）"
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
   - script：**仅 gui/e2e 需要**，结构化可执行步骤数组（schema 见下）；api/cli/manual 一律给 []
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
   - manual：**无法用上述自动化方式表达**的——纯人工体验/探索性/主观判断（如"页面美观""交互流畅""某功能是否符合需求预期"这类描述性、无明确可断言元素的）。拿不准是否可自动化时，优先判 manual。
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
7. 只输出一个 JSON 数组，不要任何解释文字，不要 markdown 代码块标记。
8. 数量随需求复杂度伸缩：一般 8-20 条；简单需求可少于 8 条，复杂需求可到 30 条。聚焦关键路径与高风险场景，**不要为凑数写重复或无价值的用例**。
9. 各测试点应相互正交：不同用例覆盖不同的验证点，不要用不同措辞重复验证同一件事。
10. 边界/异常类用例的 steps 要给出**具体示例数据**（如手机号填 "13800138000"、金额填 "-1"、超长字符串给出长度），不要只写"输入无效值"这类空泛描述。

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
    """把单条(gui/e2e)用例转成"只产出该用例结构化 script"的指令(注入选择器 key 清单)。"""
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
    """同步调 claude 为单条用例生成 script。返回 (script列表, 错误)。校验复用 _validate_script。"""
    if not is_available():
        return [], "AI 功能未启用或未找到 claude 可执行文件"
    if kind not in ("gui", "e2e"):
        return [], "仅 gui/e2e 用例支持生成 script"
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


def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None) -> Iterator[dict]:
    """流式生成测试点。yield 事件 dict：delta / result / error。

    调用方（api 层）负责累积文本、落库、转 SSE。生成器自然结束即代表流结束。
    project_id 透传给 prompt 构造,决定注入哪个项目的 key 清单。
    """
    if not is_available():
        yield {"type": "error", "msg": "AI 功能未启用或未找到 claude 可执行文件"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    cmd = _build_cmd(build_testcase_prompt(requirement, project_id))

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


_FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.S)


def parse_testcases(raw: str, project_id: int | None = None) -> list[dict]:
    """从模型输出全文中提取结构化测试点数组。

    容错顺序：markdown ```json fence → 裸 [ ... ]。字段缺失给空串，超长截断，
    丢弃无 title 的条目。解析失败返回空列表（api 层据此判定，但仍保留 output_raw）。
    project_id:决定用哪个项目的注册表校验 script.target.key(空则不校验)。
    """
    if not raw:
        return []
    m = _FENCE_RE.search(raw)
    blob = m.group(1) if m else None
    if blob is None:
        s, e = raw.find("["), raw.rfind("]")
        blob = raw[s:e + 1] if (s != -1 and e > s) else None
    if not blob:
        return []
    try:
        arr = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(arr, list):
        return []
    out = []
    _VALID_KINDS = {"gui", "api", "cli", "e2e", "manual"}
    valid_keys = _registered_keys(project_id)   # 读一次注册表,供本批所有 gui/e2e 校验 target.key
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
        # script:仅 gui/e2e 保留;校验结构 + key 合法性,非法则该用例降级 manual(不派坏 script 给执行机)
        script_json = None
        if kind in ("gui", "e2e"):
            script, err = _validate_script(it.get("script"), valid_keys)
            if err:
                kind = "manual"  # script 不合法/缺失/含未注册 key → 保守降级,避免执行机拿到坏 script
            elif script:
                # e2e 名不副实纠偏:e2e 应是多步端到端。若步数太少或只有 connect+断言(无实质交互),
                # 说明它其实是单点 gui → 改判 gui,确保"勾选的用例按其真实类型执行"。
                if kind == "e2e" and not _looks_like_e2e(script):
                    kind = "gui"
                script_json = json.dumps(script, ensure_ascii=False)
        out.append({
            "category": str(it.get("category") or "").strip()[:32],
            "title": title,
            "steps": str(it.get("steps") or "").strip(),
            "expected": str(it.get("expected") or "").strip(),
            "priority": str(it.get("priority") or "").strip()[:8],
            "kind": kind,
            "kind_reason": str(it.get("kind_reason") or "").strip()[:500],
            "script": script_json,
        })
    return out


_VALID_ACTIONS = {"connect", "click", "fill", "wait_for", "wait_response", "get_text", "assert_text", "assert_visible", "screenshot"}


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
