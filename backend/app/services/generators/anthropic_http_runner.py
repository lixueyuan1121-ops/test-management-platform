"""Direct Anthropic HTTP provider using the existing configured model gateway."""
import json
import logging
import os
import re
import threading
import time
from typing import Iterator

import requests

from app.core.config import settings
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
    _validate_generated_gui_script,
    _acquire_slot,
    _validate_api_script,
    _registered_keys,
    _FENCE_RE,
    _SYSTEM_PROMPT,
)

logger = logging.getLogger("test_platform")

_slots = threading.BoundedSemaphore(max(1, settings.AI_MAX_CONCURRENCY))

_SYSTEM_MSG = (
    "你是一名资深测试工程师，擅长把需求快速拆解为高覆盖率、可执行、可落地的测试点。"
    "只按用户要求的格式输出，不寒暄、不解释。"
)


def _cc_switch_env() -> dict:
    """读 ~/.claude/settings.json 的 env 块（cc-switch 在此注入代理地址/凭据）。

    后端 uvicorn 是独立终端启动的进程，**不继承** cc-switch 注入给 Claude Code 的
    环境变量（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）。claude CLI 能连上代理是因为
    它自己会读这个 settings.json；本 HTTP 引擎同样直接读它，做到不依赖进程环境。
    读不到（文件缺失/无 env 块）返回空 dict。
    """
    try:
        path = os.path.expanduser("~/.claude/settings.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("env", {}) or {}
    except (OSError, ValueError):
        return {}


def _base_url() -> str:
    # 优先级：.env 显式配置 > 进程环境变量 > ~/.claude/settings.json（cc-switch）
    url = (settings.ANTHROPIC_HTTP_BASE_URL
           or os.environ.get("ANTHROPIC_BASE_URL")
           or _cc_switch_env().get("ANTHROPIC_BASE_URL")
           or "")
    return url.rstrip("/")


def _auth_token() -> str:
    return (settings.ANTHROPIC_HTTP_TOKEN
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or _cc_switch_env().get("ANTHROPIC_AUTH_TOKEN")
            or "")


def is_available() -> bool:
    """代理端点已配置且 AI 功能开启则可用。"""
    if not settings.AI_ENABLED:
        return False
    return bool(_base_url() and _auth_token())


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": _auth_token(),
        "anthropic-version": "2023-06-01",
    }


def _model() -> str:
    # 与 _base_url()/_auth_token() 同构的分层回落，取网关认的“干净真名”：
    # .env 显式 > 环境变量 ANTHROPIC_MODEL > cc-switch 的 *_MODEL_NAME（干净名，
    # 如 "anthropic/claude-opus-4.8"）> *_MODEL（可能带 [1M] 标记）> 裸名兜底。
    #
    # 关键：cc-switch 的 *_MODEL 值常带 "[1M]" 之类内部标记（供本地代理翻译用），
    # 后端直连原始网关（如 https://api.360.cn）不认这些标记且严格区分大小写，
    # 故统一剥掉尾部 "[...]" 后缀。curl 实测：anthropic/claude-opus-4.8 → 200，
    # anthropic/claude-opus-4.8[1M] → 400 code 1001。
    env = _cc_switch_env()
    raw = (settings.ANTHROPIC_HTTP_MODEL
           or os.environ.get("ANTHROPIC_MODEL")
           or env.get("ANTHROPIC_DEFAULT_OPUS_MODEL_NAME")
           or env.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
           or "claude-opus-4-8")
    return re.sub(r"\[[^\]]*\]\s*$", "", raw).strip()


def supports_images() -> bool:
    return is_available()


def stream_generate(
    requirement: str,
    project_id: int | None = None,
    timeout: int | None = None,
    pages: list[str] | None = None,
    prompt_builder=None,
    system_prompt: str | None = None,
    images: list[dict] | None = None,
) -> Iterator[dict]:
    """流式生成测试点，直接调 /v1/messages streaming，事件契约与 claude_runner 一致。

    - 跳过 thinking block（content_block.type == thinking），只取 text_delta 累积正文。
    - 正文 delta 实时 yield，与 claude_runner 相同的 delta/result/error/heartbeat 帧。
    """
    if not is_available():
        yield {"type": "error", "msg": "anthropic_http 引擎未配置（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN）"}
        return

    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)

    if not _acquire_slot(_slots):
        yield {"type": "error", "msg": "anthropic_http 生成繁忙（已达并发上限），请稍后重试"}
        return

    body = {
        "model": _model(),
        "max_tokens": max(1024, settings.ANTHROPIC_HTTP_MAX_TOKENS),
        "stream": True,
        "system": system_prompt or _SYSTEM_MSG,
        "messages": [{"role": "user", "content": ([{"type": "text", "text": prompt}] + [
            {"type": "image", "source": {"type": "base64", "media_type": image["mime_type"], "data": image["data"]}}
            for image in images
        ]) if images else prompt}],
    }

    resp = None
    raw = ""
    t0 = time.monotonic()
    last_beat = t0
    in_thinking_block = False
    completed = False
    stop_reason = None

    try:
        resp = requests.post(
            f"{_base_url()}/v1/messages",
            headers=_headers(),
            json=body,
            stream=True,
            timeout=(15, timeout),
        )
        if resp.status_code != 200:
            detail = (resp.text or "")[:300]
            yield {"type": "error", "msg": f"anthropic_http 端点返回 {resp.status_code}：{detail}"}
            return

        # 自己按字节做增量 UTF-8 解码 + 手动切 SSE 行。
        # 不用 resp.iter_lines(decode_unicode=True)：SSE 响应常无 charset，requests 会用
        # Latin-1 解码 UTF-8 中文→乱码；且多字节字符跨 chunk 边界会丢字节→JSON 结构损坏。
        # incremental decoder 会把不完整的多字节序列缓存到下个 chunk，杜绝丢字节。
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        buf = ""

        for chunk in resp.iter_content(chunk_size=8192):
            # 超时检查
            if time.monotonic() - t0 >= timeout:
                yield {"type": "error", "msg": f"生成超时（>{timeout}s）", "timeout": True}
                return
            if not chunk:
                if time.monotonic() - last_beat > 3:
                    last_beat = time.monotonic()
                    yield {"type": "heartbeat"}
                continue

            buf += decoder.decode(chunk)
            # 按行切分；最后一段可能不完整，留在 buf 里等下个 chunk
            while "\n" in buf:
                raw_line, buf = buf.split("\n", 1)
                raw_line = raw_line.rstrip("\r")
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                payload = raw_line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    continue

                etype = evt.get("type", "")

                if etype == "content_block_start":
                    cb = evt.get("content_block", {})
                    in_thinking_block = cb.get("type") == "thinking"

                elif etype == "content_block_stop":
                    in_thinking_block = False

                elif etype == "content_block_delta":
                    if in_thinking_block:
                        tc = len(evt.get("delta", {}).get("thinking", ""))
                        if tc:
                            yield {"type": "thinking", "tokens": tc}
                    else:
                        text = evt.get("delta", {}).get("text", "")
                        if text:
                            raw += text
                            yield {"type": "delta", "text": text}

                elif etype == "error":
                    yield {"type": "error", "msg": "模型流返回错误：" + str(evt.get("error", {}).get("message") or "未知错误")[:300]}
                    return
                elif etype == "message_delta":
                    stop_reason = evt.get("delta", {}).get("stop_reason")
                elif etype == "message_stop":
                    completed = True
                    break

                elif etype == "message_start":
                    logger.debug("anthropic_http message_start model=%s",
                                 evt.get("message", {}).get("model", ""))

            last_beat = time.monotonic()
            if completed:
                break

    except UnicodeDecodeError:
        yield {"type": "error", "msg": "模型返回包含损坏的 UTF-8 数据，未作为成功结果"}
        return
    except requests.Timeout:
        yield {"type": "error", "msg": f"生成超时（>{timeout}s）", "timeout": True}
        return
    except requests.RequestException as e:
        logger.exception("anthropic_http 请求失败")
        yield {"type": "error", "msg": f"anthropic_http 请求失败：{e}"}
        return
    finally:
        if resp is not None:
            resp.close()
        _slots.release()

    if not completed or stop_reason in ("max_tokens", "refusal"):
        # 区分"被输出上限截断"与"连接中断",给出可操作指引:截断→调大 ANTHROPIC_HTTP_MAX_TOKENS 或缩小范围;
        # 中断→重试。附带已收到的部分正文(partial),让上层能保存已完成部分、按阶段续跑,而非整批丢弃重来。
        if stop_reason == "max_tokens":
            msg = ("模型输出达到 token 上限被截断（本次上限 "
                   f"{max(1024, settings.ANTHROPIC_HTTP_MAX_TOKENS)}）。"
                   "请调大 ANTHROPIC_HTTP_MAX_TOKENS，或按模块拆分需求后分片生成")
        elif stop_reason == "refusal":
            msg = "模型拒绝完成本次生成（可能触发内容策略），请调整输入后重试"
        else:
            msg = "模型未完整完成生成（连接中断或输出被截断），请重试或缩小生成范围"
        yield {"type": "error", "msg": msg, "truncated": stop_reason == "max_tokens", "partial": raw}
        return
    dur_ms = int((time.monotonic() - t0) * 1000)
    yield {"type": "result", "text": raw, "duration_ms": dur_ms,
           "cost_usd": None, "output_tokens": None}


def generate_script(
    kind: str,
    title: str,
    steps: str,
    expected: str,
    project_id: int | None = None,
    timeout: int | None = None,
    sub_product: str = "",
) -> tuple[list, str | None]:
    """同步为单条 gui/e2e/api 用例生成结构化 script。返回 (script列表, 错误)。"""
    if not is_available():
        return [], "anthropic_http 引擎未配置"
    if kind not in ("gui", "e2e", "api"):
        return [], "仅 gui/e2e/api 用例支持生成 script"
    timeout = timeout or settings.AI_TIMEOUT_SECONDS
    if not _acquire_slot(_slots):
        return [], "anthropic_http 生成繁忙（已达并发上限），请稍后重试"

    prompt = build_script_prompt(kind, title, steps or "", expected or "", project_id, sub_product)
    body = {
        "model": _model(),
        "max_tokens": 8192,
        "stream": False,
        "system": _SYSTEM_MSG,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(
            f"{_base_url()}/v1/messages",
            headers=_headers(),
            json=body,
            timeout=(15, timeout),
        )
    except requests.Timeout:
        return [], f"生成超时（>{timeout}s）"
    except requests.RequestException as e:
        return [], f"anthropic_http 请求失败：{e}"
    finally:
        _slots.release()

    if resp.status_code != 200:
        return [], f"anthropic_http 端点返回 {resp.status_code}：{(resp.text or '')[:200]}"
    try:
        data = resp.json()
        # Anthropic /v1/messages 非流式：content 是 block 数组
        text = "".join(
            b.get("text", "") for b in data.get("content", [])
            if isinstance(b, dict) and b.get("type") == "text"
        )
    except (KeyError, ValueError, TypeError):
        return [], "anthropic_http 响应解析失败"

    m = _FENCE_RE.search(text)
    blob = m.group(1) if m else None
    if blob is None:
        import re
        m2 = re.search(r"\[\s*[\{\"]", text)
        s = m2.start() if m2 else text.find("[")
        e = text.rfind("]")
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
        # 仅结构校验,不卡 key 注册(未注册 key → 上层降级「选择器待补」保留 script,与 claude_runner 一致)。
        script, err = _validate_generated_gui_script(arr, None)
    if err:
        return [], f"生成的 script 不合法：{err}"
    return script, None
