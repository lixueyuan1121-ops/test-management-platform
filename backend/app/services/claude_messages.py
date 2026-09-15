"""Text/image generation through an explicitly configured Anthropic Messages endpoint.

No CLI session or tool loop. One bounded retry is allowed before text only.
A response is usable only after a
valid message_stop. The owning coroutine closes HTTP resources on cancellation.
"""
import asyncio
import json
from queue import Queue, Empty, Full
import threading
import time
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services import generation_trace as trace
from app.services.model_text import event_text


class ResponseError(ValueError):
    """A diagnostic composed locally, safe to show without the upstream body."""


class TransientResponse(ResponseError):
    pass


def is_available():
    return bool(settings.AI_ENABLED and settings.CLAUDE_MESSAGES_BASE_URL
                and settings.CLAUDE_MESSAGES_API_KEY and settings.CLAUDE_MESSAGES_MODEL)


def _request(prompt, system_prompt, images=None, output_schema=None):
    if output_schema:
        prompt += "\n响应结构(JSON Schema)：\n" + json.dumps(output_schema, ensure_ascii=False)
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image", "source": {"type": "base64",
        "media_type": im["mime_type"], "data": im["data"]}} for im in images or [])
    return {"model": settings.CLAUDE_MESSAGES_MODEL,
            "max_tokens": settings.CLAUDE_MESSAGES_MAX_TOKENS,
            "stream": True, "system": system_prompt,
            "messages": [{"role": "user", "content": content}]}


def _endpoint():
    base = settings.CLAUDE_MESSAGES_BASE_URL.rstrip('/')
    url = urlsplit(base)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ResponseError('模型服务地址配置无效')
    return base + ('/messages' if url.path.endswith('/v1') else '/v1/messages')


class MessageStream:
    def __init__(self):
        self.raw = ''
        self.started = False
        self.done = False
        self.reason = None
        self.tokens = None

    def feed(self, event):
        kind = event.get('type')
        if kind == 'error':
            # Do not echo an upstream body: it can contain request data or secrets.
            error = event.get('error') or {}
            cls = TransientResponse if error.get('type') in ('overloaded_error', 'rate_limit_error') else ResponseError
            raise cls('模型服务返回流式错误，请按任务编号查看调用日志')
        if kind == 'message_start':
            if self.started:
                trace.emit('model_response_restarted', output_chars=len(self.raw))
                raise ResponseError('上游在返回正文后重新开始响应，已停止当前批次并保留返回内容，可继续失败部分')
            self.started = True
        elif kind in ('content_block_start', 'content_block_delta'):
            if not self.started:
                raise ResponseError('模型流缺少开始标记')
            block = event.get('content_block') if kind == 'content_block_start' else event.get('delta')
            block = block or {}
            if block.get('type') in ('text', 'text_delta'):
                text = event_text(block.get('text'), '', 'protocol_delta')
                if text:
                    if len(self.raw) + len(text) > 2_000_000:
                        raise ResponseError('模型返回内容超过保存上限，请拆分需求')
                    self.raw += text
                    return {'type': 'delta', 'text': text}
        elif kind == 'message_delta':
            self.reason = (event.get('delta') or {}).get('stop_reason') or self.reason
            self.tokens = (event.get('usage') or {}).get('output_tokens', self.tokens)
        elif kind == 'message_stop':
            if not self.started or self.reason not in ('end_turn', 'stop_sequence', 'max_tokens'):
                raise ResponseError('模型响应未正常完成，已保留收到的内容')
            if not self.raw.strip():
                raise ResponseError('模型没有返回正文')
            self.done = True
            return {'type': 'result', 'text': self.raw, 'complete': True,
                    'finish_reason': 'length' if self.reason == 'max_tokens' else 'stop',
                    'output_tokens': self.tokens, 'cost_usd': None}
        return None


async def _receive(body, publish, timeout):
    state = MessageStream()
    started = time.monotonic()
    trace.emit('model_http_start', transport='messages', model=settings.CLAUDE_MESSAGES_MODEL,
               prompt_chars=len(body['messages'][0]['content'][0]['text']),
               max_tokens=body['max_tokens'], timeout_seconds=timeout)
    headers = {'x-api-key': settings.CLAUDE_MESSAGES_API_KEY, 'anthropic-version': '2023-06-01'}
    limits = httpx.Timeout(min(timeout, settings.CLAUDE_MESSAGES_IDLE_SECONDS), connect=min(10, timeout))
    try:
        async with httpx.AsyncClient(timeout=limits, trust_env=False, follow_redirects=False) as client:
            async with client.stream('POST', _endpoint(), headers=headers, json=body) as response:
                request_id = response.headers.get('request-id') or response.headers.get('x-request-id')
                trace.emit('model_http_headers', http_status=response.status_code,
                           request_id=request_id, elapsed_ms=int((time.monotonic()-started)*1000))
                if response.status_code != 200:
                    cls = TransientResponse if response.status_code in (429, 502, 503, 504) else ResponseError
                    raise cls(f'模型接口返回 HTTP {response.status_code}，请按任务编号查看调用日志')
                if 'text/event-stream' not in response.headers.get('content-type', '').lower():
                    raise ResponseError('模型接口未返回有效的流式响应（HTTP 200）')
                data = []
                frame_size = 0
                first = True
                async for line in response.aiter_lines():
                    if line.startswith('data:'):
                        frame_size += len(line)
                        if frame_size > 2_000_000:
                            raise ResponseError('模型单条响应超过保存上限')
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        event = json.loads('\n'.join(data))
                        data, frame_size = [], 0
                        if not isinstance(event, dict):
                            raise ResponseError('模型流式响应格式错误')
                        output = state.feed(event)
                        if output:
                            if first and output['type'] == 'delta':
                                first = False
                                trace.emit('model_first_text', first_output_ms=int((time.monotonic()-started)*1000))
                            publish(output)
                        if state.done:
                            return
                raise ResponseError('模型流在完成前断开，已保留收到的内容')
    finally:
        trace.emit('model_http_end', output_chars=len(state.raw), got_result=state.done,
                   elapsed_ms=int((time.monotonic()-started)*1000))


def stream_generate(prompt, system_prompt, *, timeout, images=None, output_schema=None):
    from app.services.claude_runner import _acquire_slot, _slots
    if not is_available():
        yield {'type': 'error', 'msg': '尚未配置 Messages 模型连接'}
        return
    if not _acquire_slot(_slots):
        yield {'type': 'error', 'msg': 'AI 生成繁忙（等待超时），请稍后重试'}
        return
    queue, stopped, ready = Queue(maxsize=256), threading.Event(), {}
    def publish(event):
        while not stopped.is_set():
            try:
                queue.put(event, timeout=0.1)
                return
            except Full:
                pass
    async def run():
        ready.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
        if stopped.is_set():
            return
        deadline = time.monotonic() + timeout
        for attempt in (1, 2):
            received = False
            def forward(event):
                nonlocal received
                received = received or (event['type'] == 'delta' and bool(event.get('text')))
                publish(event)
            try:
                remaining = max(0, deadline - time.monotonic())
                with trace.scope(attempt=attempt):
                    await asyncio.wait_for(
                        _receive(_request(prompt, system_prompt, images, output_schema), forward, remaining), remaining)
                return
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, httpx.HTTPError, TransientResponse) as error:
                # Only one reconnect, before any visible output, within the original deadline.
                if attempt == 1 and not received and deadline - time.monotonic() > 1:
                    trace.emit('model_retry_before_output', attempt=2, error_type=type(error).__name__)
                    await asyncio.sleep(1)
                    continue
                if isinstance(error, (asyncio.TimeoutError, httpx.TimeoutException)):
                    message = '模型响应超时，已保留收到的内容，可继续失败部分'
                elif isinstance(error, ResponseError):
                    message = str(error)
                else:
                    message = '模型连接中断，已保留收到的内容，可继续失败部分'
                publish({'type': 'error', 'msg': message})
                return
            except ResponseError as error:
                publish({'type': 'error', 'msg': str(error)})
                return
            except (ValueError, TypeError, KeyError) as error:
                trace.emit('model_protocol_error', error_type=type(error).__name__)
                publish({'type': 'error', 'msg': '模型响应无效或未完整结束，请按任务编号查看调用日志'})
                return
    def worker():
        try:
            asyncio.run(run())
        except asyncio.CancelledError:
            pass
        finally:
            _slots.release()
            publish(None)
    thread = threading.Thread(target=trace.bind(worker), daemon=True)
    try:
        thread.start()
    except BaseException:
        _slots.release()
        raise
    try:
        while True:
            try:
                event = queue.get(timeout=3)
            except Empty:
                yield {'type': 'heartbeat'}
                continue
            if event is None:
                return
            yield event
            if event['type'] in ('result', 'error'):
                return
    finally:
        stopped.set()
        if ready:
            try:
                ready['loop'].call_soon_threadsafe(ready['task'].cancel)
            except RuntimeError:
                pass
