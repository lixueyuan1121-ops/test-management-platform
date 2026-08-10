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
    return f"""请基于以下需求，设计一份结构化测试点清单。

输出要求：
1. 覆盖多个维度：功能、边界、异常、兼容、性能（按需选取，不必每类都有）。
2. 每个测试点是一个对象，字段：
   - category：维度（功能/边界/异常/兼容/性能 之一）
   - title：一句话标题
   - steps：操作步骤（可多步，用换行分隔）
   - expected：预期结果
   - priority：优先级（P0/P1/P2/P3）
3. 只输出一个 JSON 数组，不要任何解释文字，不要 markdown 代码块标记。
4. 数量控制在 8-20 条，聚焦关键路径与高风险场景。

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
    for it in arr:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()[:512]
        if not title:
            continue
        out.append({
            "category": str(it.get("category") or "").strip()[:32],
            "title": title,
            "steps": str(it.get("steps") or "").strip(),
            "expected": str(it.get("expected") or "").strip(),
            "priority": str(it.get("priority") or "").strip()[:8],
        })
    return out
