"""DeepSeek 引擎（子进程隔离形态，B-1 方案）。

与 claude_runner 对外接口一致（is_available / stream_generate / generate_script），
可被 generators 抽象层平等调度。**复用** claude_runner 的 prompt 构造与解析逻辑，
保证 claude / deepseek 两引擎用同一 prompt、同一解析降级，产出可比。

【隔离设计】本模块运行在后端主环境，但**不 import deepseek_harness**。
dsh SDK（含内嵌 runtime、较新 pydantic）装在一个**独立 venv**里；本模块以
**子进程**方式调用 generators/dsh_worker.py（在该 venv 的 python 下运行），
通过 stdin 传参、读 stdout 的行分隔 JSON 事件。好处：
- 主环境依赖（fastapi/sqlalchemy/pydantic 等）完全不受 dsh 影响；
- dsh 崩溃/超时只杀子进程，不影响平台其余功能；
- dsh 未安装时 is_available() 返回 False，前端置灰，claude 照常。

工程要点（来自 POC 实测，见 /Users/lixueyuan/code/deepseek-harness-poc）：
- 块级流式：dsh 通知是"已提交的 assistant 文本块"，非 token 级 delta；worker
  逐块 emit，本模块转发为 delta 事件（前端体验为"分段冒出"）。
- max_tokens 需配大（默认 DEEPSEEK_MAX_TOKENS=49152），否则推理模型会截断。
- 并发闸 / 硬超时 / 心跳都在本模块（主环境）控制，worker 只管跑一次。
"""
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator

from app.core.config import settings
# 复用 claude_runner 的 prompt 与解析（单一事实来源），保证两引擎产出可比。
from app.services.claude_runner import (  # noqa: F401
    build_testcase_prompt,
    build_script_prompt,
    parse_testcases,
    _validate_script,
    _FENCE_RE,
)

logger = logging.getLogger("test_platform")

# 全局并发闸：与 claude 侧独立计数（两引擎成本/负载分开控）
_slots = threading.BoundedSemaphore(max(1, settings.AI_MAX_CONCURRENCY))

_WORKER = str(Path(__file__).with_name("dsh_worker.py"))
_DEFAULT_VENV_PY = os.path.expanduser("~/.dsh-venv/bin/python")


def _worker_python() -> str | None:
    """dsh 独立 venv 的 python 路径：优先 .env 配置，否则默认 ~/.dsh-venv/bin/python。
    路径不存在返回 None（据此判不可用）。"""
    cand = settings.DEEPSEEK_VENV_PYTHON or _DEFAULT_VENV_PY
    return cand if cand and os.path.isfile(cand) else None


def is_available() -> bool:
    """DeepSeek 引擎是否可用：开关开 + 配了凭据 + 独立 venv 的 python 存在 + worker 脚本在。"""
    if not settings.DEEPSEEK_ENABLED:
        return False
    if not (settings.DEEPSEEK_API_KEY or settings.DEEPSEEK_BASE_URL):
        return False
    if not _worker_python():
        return False
    return os.path.isfile(_WORKER)


def _build_worker_input(prompt: str) -> str:
    """构造 worker 的 stdin JSON（一行）。session_id 唯一，避免 dsh 回放旧会话。"""
    return json.dumps({
        "prompt": prompt,
        "model": settings.DEEPSEEK_MODEL or "deepseek-v4-flash",
        "max_tokens": settings.DEEPSEEK_MAX_TOKENS or 49152,
        "api_key": settings.DEEPSEEK_API_KEY or None,
        "base_url": settings.DEEPSEEK_BASE_URL or None,
        "session_root": settings.DEEPSEEK_SESSION_ROOT or None,
        "session_id": f"gen-{time.monotonic_ns()}",
    }, ensure_ascii=False)


def stream_generate(requirement: str, timeout: int | None = None) -> Iterator[dict]:
    """流式生成测试点。yield delta/result/error/heartbeat，契约与 claude_runner 对齐。

    子进程启 dsh_worker.py（独立 venv），读其 stdout 的行分隔 JSON 事件并转发。
    """
    if not is_available():
        yield {"type": "error", "msg": "DeepSeek 引擎未启用或未配置（检查 DEEPSEEK_ENABLED / KEY / 独立 venv）"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    py = _worker_python()

    if not _slots.acquire(blocking=False):
        yield {"type": "error", "msg": "DeepSeek 生成繁忙（已达并发上限），请稍后重试"}
        return

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            [py, _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # 单独收 worker 日志，失败时带进错误
            text=True,
            bufsize=1,
        )
    except OSError as e:
        _slots.release()
        logger.exception("启动 dsh worker 失败")
        yield {"type": "error", "msg": f"启动 DeepSeek worker 失败：{e}"}
        return

    # 写入参后关闭 stdin（worker 只读一行）
    try:
        proc.stdin.write(_build_worker_input(build_testcase_prompt(requirement)) + "\n")
        proc.stdin.flush()
        proc.stdin.close()
    except (BrokenPipeError, OSError) as e:
        proc.kill()
        _slots.release()
        yield {"type": "error", "msg": f"向 DeepSeek worker 写入失败：{e}"}
        return

    q: Queue = Queue()

    def _reader():
        try:
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_reader, daemon=True).start()

    start = time.monotonic()
    got_terminal = False   # 是否收到 result 或 error
    try:
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                proc.kill()
                yield {"type": "error", "msg": f"DeepSeek 生成超时（>{timeout}s）"}
                return
            try:
                line = q.get(timeout=min(remaining, 3))
            except Empty:
                if proc.poll() is not None and q.empty():
                    break
                # 空转发心跳，防网关按空闲切断（dsh 冷启动 + 推理耗时长）
                yield {"type": "heartbeat"}
                continue
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue  # 非协议行（worker 万一往 stdout 打了杂）忽略
            etype = evt.get("type")
            if etype in ("result", "error"):
                got_terminal = True
            yield evt
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        _slots.release()

    if not got_terminal:
        # 没拿到终态事件：worker 异常退出，附 stderr 尾部便于排查
        err_tail = ""
        try:
            err_tail = (proc.stderr.read() or "")[-300:] if proc and proc.stderr else ""
        except Exception:  # noqa: BLE001
            pass
        logger.warning("dsh worker 未返回终态，stderr=%s", err_tail)
        yield {"type": "error", "msg": f"DeepSeek 生成未完成：{err_tail or '无输出'}"}


def generate_script(kind: str, title: str, steps: str, expected: str,
                    timeout: int | None = None) -> tuple[list, str | None]:
    """同步为单条 gui/e2e 用例生成结构化 script。返回 (script列表, 错误)。

    走子进程一次性执行：读 worker 的 result 事件文本，抽取并校验 script 数组。
    """
    if not is_available():
        return [], "DeepSeek 引擎未启用或未配置"
    if kind not in ("gui", "e2e"):
        return [], "仅 gui/e2e 用例支持生成 script"
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    py = _worker_python()
    if not _slots.acquire(blocking=False):
        return [], "DeepSeek 生成繁忙（已达并发上限），请稍后重试"

    prompt = build_script_prompt(kind, title, steps or "", expected or "")
    text = ""
    try:
        proc = subprocess.run(
            [py, _WORKER],
            input=_build_worker_input(prompt) + "\n",
            capture_output=True, text=True, timeout=timeout,
        )
        # 从 stdout 行里取最后一个 result 事件的 text
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if evt.get("type") == "result":
                text = evt.get("text", "") or ""
            elif evt.get("type") == "error":
                return [], evt.get("msg") or "DeepSeek 生成 script 失败"
    except subprocess.TimeoutExpired:
        return [], f"生成超时（>{timeout}s）"
    except OSError as e:
        return [], f"启动 DeepSeek worker 失败：{e}"
    finally:
        _slots.release()

    if not text:
        return [], "未获得 script 输出"
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
    script, err = _validate_script(arr)
    if err:
        return [], f"生成的 script 不合法：{err}"
    return script, None
