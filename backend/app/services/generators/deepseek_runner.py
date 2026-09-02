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
    build_eval_query_prompt,
    parse_eval_queries,
    build_eval_judge_prompt,
    parse_eval_verdict,
    EVAL_JUDGE_SYSTEM_PROMPT,
    _validate_script,
    _validate_api_script,
    _registered_keys,
    _FENCE_RE,
)

logger = logging.getLogger("test_platform")

# 全局并发闸：DeepSeek 用独立且更小的并发——HTTP 直调易撞网关分钟级 token/请求配额,
# 与 claude(本地进程无网关限流)分开控。分片各占一槽,超限排队等待而非并发轰网关撞 429。
_slots = threading.BoundedSemaphore(max(1, settings.DEEPSEEK_MAX_CONCURRENCY))
# 超限「排队等待」而非拒绝(方案2 P3a);复用 claude_runner 的 _acquire_slot(同一策略/配置)
from app.services.claude_runner import _acquire_slot  # noqa: E402


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


def _body(prompt: str, stream: bool, system_prompt: str | None = None) -> dict:
    return {
        "model": settings.DEEPSEEK_MODEL or "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content":
             system_prompt or
             "你是一名资深测试工程师，擅长把需求快速拆解为高覆盖率、可执行、可落地的测试点。"
             "只按用户要求的格式输出，不寒暄、不解释。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": settings.DEEPSEEK_MAX_TOKENS or 49152,
        "stream": stream,
    }


# 429(限流)退避重试:多分片并发易撞网关分钟级 token/请求配额。撞到就等待重试而非直接失败。
_RETRY_STATUSES = {429, 503}
_MAX_RETRIES = 4          # 首次 + 最多 4 次重试
_BACKOFF_BASE = 5.0       # 退避基数(秒):5, 10, 20, 40 —— 跨过分钟级配额窗口


def _retry_after_seconds(resp, attempt: int) -> float:
    """优先用响应头 Retry-After(秒或 HTTP-date 忽略后回退);否则指数退避。上限 60s。"""
    ra = resp.headers.get("Retry-After") if resp is not None else None
    if ra:
        try:
            return min(60.0, max(1.0, float(ra)))
        except (TypeError, ValueError):
            pass
    return min(60.0, _BACKOFF_BASE * (2 ** attempt))


def _post_with_retry(*, stream: bool, json_body: dict, timeout, sleep=time.sleep):
    """POST 到 DeepSeek 端点,遇 429/503 按退避重试。返回 (resp, err_msg)。

    err_msg 非空表示重试用尽或不可重试错误(调用方据此 yield/return error)。
    sleep 参数供自测注入(免真等)。仍持有 _slots 槽期间调用——退避占槽是有意的:
    既是重试等待,也顺带压低对网关的并发压力。
    """
    last_detail = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.post(_endpoint(), headers=_headers(), json=json_body,
                                 stream=stream, timeout=(10, timeout))
        except requests.Timeout:
            return None, f"DeepSeek 生成超时（>{timeout}s）"
        except requests.RequestException as e:
            logger.exception("DeepSeek 请求失败")
            return None, f"DeepSeek 请求失败：{e}"
        if resp.status_code == 200:
            return resp, None
        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
            wait = _retry_after_seconds(resp, attempt)
            last_detail = (resp.text or "")[:200]
            resp.close()
            logger.warning("DeepSeek %s 限流,第 %d 次退避 %.0fs 后重试", resp.status_code, attempt + 1, wait)
            sleep(wait)
            continue
        detail = (resp.text or last_detail or "")[:200]
        code = resp.status_code
        resp.close()
        hint = "（限流,已重试多次仍失败,请降低并发或稍后再试）" if code in _RETRY_STATUSES else ""
        return None, f"DeepSeek 端点返回 {code}{hint}：{detail}"
    return None, "DeepSeek 请求失败：重试用尽"


def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None, pages: list[str] | None = None, prompt_builder=None, system_prompt: str | None = None) -> Iterator[dict]:
    """流式生成测试点。yield delta/result/error/heartbeat，契约与 claude_runner 对齐。

    只累积 delta.content（正文）为 raw；delta.reasoning_content（思维链）丢弃。
    project_id 透传给 build_testcase_prompt,决定注入哪个项目的 key 清单。
    pages 非空则只注入这些页面的 key(收窄),与 claude 引擎口径一致。
    prompt_builder 非空则用它(无参调用)构造 prompt,否则默认生成测试点 prompt。
    system_prompt 非空则覆盖 system 消息,否则回落测试工程师人设(与 claude 引擎口径一致)。
    """
    if not is_available():
        yield {"type": "error", "msg": "DeepSeek 引擎未启用或未配置（检查 DEEPSEEK_ENABLED / BASE_URL / KEY）"}
        return
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)

    if not _acquire_slot(_slots):
        yield {"type": "error", "msg": "DeepSeek 生成繁忙（等待超时），请稍后重试"}
        return

    resp = None
    raw = ""
    out_tokens = None
    finish_reason = None
    last_beat = time.monotonic()
    try:
        resp, err = _post_with_retry(
            stream=True, json_body=_body(prompt, stream=True, system_prompt=system_prompt),
            timeout=timeout)
        if err:
            yield {"type": "error", "msg": err}
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
    if not _acquire_slot(_slots):
        return [], "DeepSeek 生成繁忙（等待超时），请稍后重试"

    prompt = build_script_prompt(kind, title, steps or "", expected or "", project_id)
    try:
        resp, err = _post_with_retry(
            stream=False, json_body=_body(prompt, stream=False), timeout=timeout)
        if err:
            return [], err
    finally:
        _slots.release()

    try:
        data = resp.json()
        text = (data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, ValueError, TypeError):
        return [], "DeepSeek 响应解析失败"
    finally:
        resp.close()

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
