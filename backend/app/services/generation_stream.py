"""Common case-generation collection: errors are diagnostics, never case text."""
from app.services import generation_trace as trace


def consume(events, on_progress=None):
    from app.services.requirement_analysis import _event_text
    raw, meta, error, completed = '', None, None, False
    stream = iter(events)
    try:
        for event in stream:
            kind = event.get('type')
            if kind == 'error' or event.get('is_error'):
                error = str(event.get('msg') or event.get('error') or _event_text(event.get('text'), '', kind) or '模型服务返回错误')
                if kind == 'result':
                    meta = event
                if raw.lstrip().startswith(('API Error:', 'API returned an empty or malformed')):
                    raw = ''
                    if on_progress:
                        on_progress('')
                trace.emit('generation_stream_failed', error_code=trace.error_code(error), output_chars=len(raw))
                break
            if kind == 'delta':
                raw = ('' if event.get('reset') else raw) + _event_text(event.get('text'), raw, kind)
            elif kind == 'result':
                meta = event
                raw = _event_text(event.get('text'), raw, kind) or raw
                completed = True
                if event.get('complete') is False or event.get('finish_reason') == 'length':
                    error = '模型响应未完整结束，已保留返回内容，请重试失败部分'
                    break
            if on_progress:
                on_progress(raw if kind in ('delta', 'result') and raw else None)
        if not completed and not error:
            error = '模型连接中断，未收到完成标记，已保留返回内容'
    except Exception as exc:
        error = str(exc)
        trace.emit('generation_stream_exception', error_code=trace.error_code(exc), error_type=type(exc).__name__, output_chars=len(raw))
    finally:
        if hasattr(stream, 'close'):
            stream.close()
    trace.emit('generation_stream_end', status='failed' if error else 'done', output_chars=len(raw), got_result=completed)
    return raw, meta, error
