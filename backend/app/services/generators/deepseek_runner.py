"""DeepSeek 引擎（OpenAI 兼容端点直调形态）。

与 claude_runner 对外接口一致（is_available / stream_generate / generate_script），
可被 generators 抽象层平等调度。**复用** claude_runner 的 prompt 构造与解析逻辑，
保证 claude / deepseek 两引擎用同一 prompt、同一解析降级，产出可比。

【为什么直调而非 dsh SDK】目标只是"用 DeepSeek 生成测试点文本"，端点
（DEEPSEEK_BASE_URL，如内网 360 网关）是标准 OpenAI 兼容接口。用 requests
直接 POST /chat/completions 即可，零平台依赖（不挑 OS/glibc）、无额外安装、
无子进程/独立环境的复杂度。dsh 那套 agent 框架对纯文本生成是过剩且平台受限的。

端点行为（实测 api.360.cn/v1）：
- 标准 OpenAI SSE：`data: {json}` 逐帧；[DONE] 收尾。
- **推理与正文分离**：思维链在 delta.reasoning_content，正文在 delta.content。
  本模块只取 content 累积为 raw（丢弃 reasoning），正文才是要解析的 JSON 数组。
- 有 token 级 delta → 恢复前端逐字打字机体验（不像 dsh 的块级）。
- max_tokens 需配大（DEEPSEEK_MAX_TOKENS，默认 49152）：推理模型 reasoning 占用多，
  过小会在正文产出前被 finish_reason=length 截断。
"""
import json
import logging
import threading
import time
from typing import Iterator

import requests

from app.core.config import settings
# 复用 claude_runner 的 prompt 与解析（单一事实来源），保证两引擎产出可比。
from app.services.claude_runner import (  # noqa: F401
    build_testcase_prompt,
    build_script_prompt,
    parse_testcases,
    _validate_script,
    _validate_api_script,
    _registered_keys,
    _FENCE_RE,
)

logger = logging.getLogger("test_platform")

# 全局并发闸：与 claude 侧独立计数（两引擎成本/负载分开控）
_slots = threading.BoundedSemaphore(max(1, settings.AI_MAX_CONCURRENCY))


def is_available() -> bool:
    """DeepSeek 引擎是否可用：开关开 + 配了端点/凭据。"""
    if not settings.DEEPSEEK_ENABLED:
        return False
    # 需要 base_url（自建/网关）或直接走官方（此时至少要 key）；两者有其一即认为已配置
    return bool(settings.DEEPSEEK_BASE_URL or settings.DEEPSEEK_API_KEY)


def _endpoint() -> str:
    base = (settings.DEEPSEEK_BASE_URL or "https://api.deepseek.com/v1").rstrip("/")
    return f"{base}/chat/completions"


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.DEEPSEEK_API_KEY:
        h["Authorization"] = f"Bearer {settings.DEEPSEEK_API_KEY}"
    return h


def _body(prompt: str, stream: bool) -> dict:
    return {
        "model": settings.DEEPSEEK_MODEL or "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content":
             "你是一名资深测试工程师，擅长把需求快速拆解为高覆盖率、可执行、可落地的测试点。"
             "只按用户要求的格式输出，不寒暄、不解释。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": settings.DEEPSEEK_MAX_TOKENS or 49152,
        "stream": stream,
    }


def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None) -> Iterator[dict]:
    """流式生成测试点。yield delta/result/error/heartbeat，契约与 claude_runner 对齐。

    只累积 delta.content（正文）为 raw；delta.reasoning_content（思维链）丢弃。
    project_id 透传给 build_testcase_prompt,决定注入哪个项目的 key 清单。
    """
    if not is_available():
        yield {"type": "error", "msg": "DeepSeek 引擎未启用或未配置（检查 DEEPSEEK_ENABLED / BASE_URL / KEY）"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS

    if not _slots.acquire(blocking=False):
        yield {"type": "error", "msg": "DeepSeek 生成繁忙（已达并发上限），请稍后重试"}
        return

    resp = None
    raw = ""
    out_tokens = None
    finish_reason = None
    last_beat = time.monotonic()
    try:
        resp = requests.post(
            _endpoint(), headers=_headers(), json=_body(build_testcase_prompt(requirement, project_id), stream=True),
            stream=True, timeout=(10, timeout),
        )
        if resp.status_code != 200:
            detail = (resp.text or "")[:200]
            yield {"type": "error", "msg": f"DeepSeek 端点返回 {resp.status_code}：{detail}"}
            return
        for line in resp.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if not line:
                # 空行（SSE 帧分隔）：借机发心跳，防网关空闲切断长连接
                if time.monotonic() - last_beat > 3:
                    last_beat = time.monotonic()
                    yield {"type": "heartbeat"}
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                continue
            choices = evt.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    raw += content
                    yield {"type": "delta", "text": content}
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr
            usage = evt.get("usage")
            if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                out_tokens = usage.get("completion_tokens")
            last_beat = time.monotonic()
    except requests.Timeout:
        yield {"type": "error", "msg": f"DeepSeek 生成超时（>{timeout}s）"}
        return
    except requests.RequestException as e:
        logger.exception("DeepSeek 请求失败")
        yield {"type": "error", "msg": f"DeepSeek 请求失败：{e}"}
        return
    finally:
        if resp is not None:
            resp.close()
        _slots.release()

    if finish_reason == "length" and not raw.strip():
        yield {"type": "error", "msg": "DeepSeek 生成被 max_tokens 截断且无正文，请调大 DEEPSEEK_MAX_TOKENS"}
        return
    yield {"type": "result", "text": raw, "output_tokens": out_tokens,
           "cost_usd": None, "duration_ms": None, "finish_reason": finish_reason}


def generate_script(kind: str, title: str, steps: str, expected: str,
                    project_id: int | None = None, timeout: int | None = None) -> tuple[list, str | None]:
    """同步为单条 gui/e2e/api 用例生成结构化 script。返回 (script列表, 错误)。"""
    if not is_available():
        return [], "DeepSeek 引擎未启用或未配置"
    if kind not in ("gui", "e2e", "api"):
        return [], "仅 gui/e2e/api 用例支持生成 script"
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    if not _slots.acquire(blocking=False):
        return [], "DeepSeek 生成繁忙（已达并发上限），请稍后重试"

    prompt = build_script_prompt(kind, title, steps or "", expected or "", project_id)
    try:
        resp = requests.post(_endpoint(), headers=_headers(),
                             json=_body(prompt, stream=False), timeout=(10, timeout))
    except requests.Timeout:
        return [], f"生成超时（>{timeout}s）"
    except requests.RequestException as e:
        return [], f"DeepSeek 请求失败：{e}"
    finally:
        _slots.release()

    if resp.status_code != 200:
        return [], f"DeepSeek 端点返回 {resp.status_code}：{(resp.text or '')[:200]}"
    try:
        data = resp.json()
        text = (data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, ValueError, TypeError):
        return [], "DeepSeek 响应解析失败"

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
        return [], f"生成的 script 不合法：{err}"
    return script, None
