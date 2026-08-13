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
import os
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

# 默认选择器注册表路径:相对本文件回到仓库根,再进 tools/qalab-runner/gui-mcp/selectors.json
_DEFAULT_SELECTORS = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "qalab-runner", "gui-mcp", "selectors.json")
)


def _load_selector_keys() -> list[dict]:
    """读注册表,返回 [{key, frame, desc}, ...];读不到返回空(prompt 里就不注入 key 清单)。"""
    path = settings.SELECTORS_PATH or _DEFAULT_SELECTORS
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f).get("registry", {})
        return [{"key": k, "frame": v.get("frame"), "desc": v.get("desc", "")} for k, v in reg.items()]
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("读取选择器注册表失败,gui script 生成将不注入 key 清单: %s", path)
        return []

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


def build_testcase_prompt(requirement: str) -> str:
    """把需求文本包装成「生成结构化测试点」的指令。

    用 <requirement> 标签包裹用户输入（而非引号），避免内容里的引号破坏边界。
    强约束只输出 JSON 数组；即便模型仍包了 markdown fence，解析层也能兜底剥离。
    """
    # 注入语义 key 清单(供 gui/e2e 的 script.target.key 取值);读不到就给空块、只说明无可用 key
    keys = _load_selector_keys()
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
   - priority：优先级（P0/P1/P2/P3）
   - kind：自动化执行类型，只能是 gui/api/cli/e2e/manual 之一（判定规则见下）
   - kind_reason：一句话说明为何判该 kind
   - script：**仅 gui/e2e 需要**，结构化可执行步骤数组（schema 见下）；api/cli/manual 一律给 []
3. kind 判定规则：
   - gui：在被测客户端界面上点击/输入/断言某元素或文案（单点、一两步）
   - api：调接口、校验响应码/响应体
   - cli：跑命令行、校验退出码/输出
   - e2e：跨多个界面步骤的端到端流程（如登录→进入某页→操作→验证结果），比单点 gui 长
   - manual：**无法用上述自动化方式表达**的——纯人工体验/探索性/主观判断（如"页面美观""交互流畅""某功能是否符合需求预期"这类描述性、无明确可断言元素的）。拿不准是否可自动化时，优先判 manual。
4. script（gui/e2e）——有序步骤数组，每步一个对象 {{action, target?, args?, desc}}：
   - action 只能取：connect（第一步必须，连接客户端）、click、fill、wait_for、get_text、assert_text、assert_visible、screenshot
   - target：定位元素，**优先用语义 key**：{{"key":"<下方清单里的 key>"}}；清单没有的元素才用 {{"selector":"<CSS>"}}
   - args：assert_text 用 {{"expected":"...","contains":true}}；fill 用 {{"text":"..."}}；wait_for 用 {{"timeout_ms":6000}}
   - desc：该步人读说明
   - **每条 gui/e2e 至少有一个 assert_text 或 assert_visible**（否则没有判定依据，应改判 manual）
   - 只能用下方 key 清单里的 key；**找不到合适 key 表达该测试点 → 改判 kind=manual、script=[]**（不要瞎编 selector）
{keys_block}
5. 只输出一个 JSON 数组，不要任何解释文字，不要 markdown 代码块标记。
6. 数量控制在 8-20 条，聚焦关键路径与高风险场景。

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


def stream_generate(requirement: str, timeout: int | None = None) -> Iterator[dict]:
    """流式生成测试点。yield 事件 dict：delta / result / error。

    调用方（api 层）负责累积文本、落库、转 SSE。生成器自然结束即代表流结束。
    """
    if not is_available():
        yield {"type": "error", "msg": "AI 功能未启用或未找到 claude 可执行文件"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    cmd = _build_cmd(build_testcase_prompt(requirement))

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


def parse_testcases(raw: str) -> list[dict]:
    """从模型输出全文中提取结构化测试点数组。

    容错顺序：markdown ```json fence → 裸 [ ... ]。字段缺失给空串，超长截断，
    丢弃无 title 的条目。解析失败返回空列表（api 层据此判定，但仍保留 output_raw）。
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
        # script:仅 gui/e2e 保留;校验结构,非法则该用例降级 manual(不派坏 script 给执行机)
        script_json = None
        if kind in ("gui", "e2e"):
            script, err = _validate_script(it.get("script"))
            if err:
                kind = "manual"  # script 不合法/缺失 → 保守降级,避免执行机拿到坏 script
            elif script:
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


_VALID_ACTIONS = {"connect", "click", "fill", "wait_for", "get_text", "assert_text", "assert_visible", "screenshot"}


def _validate_script(script) -> tuple[list, str | None]:
    """校验 gui/e2e 的 script。返回 (规范化步骤列表, 错误说明)。

    规则:必须是非空数组;每步 action 合法;定位类步骤要有 target.key 或 target.selector;
    至少含一个 assert_text/assert_visible(否则无判定依据)。任一不满足 → 返回错误(调用方降级 manual)。
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
        if action == "assert_text" and not (st.get("args") or {}).get("expected"):
            return [], "assert_text 缺 args.expected"
        if action in ("assert_text", "assert_visible"):
            has_assert = True
        norm.append({"action": action, "target": target, "args": st.get("args") or {}, "desc": str(st.get("desc") or "")[:200]})
    if not has_assert:
        return [], "无任何断言步骤(assert_text/assert_visible)"
    return norm, None
