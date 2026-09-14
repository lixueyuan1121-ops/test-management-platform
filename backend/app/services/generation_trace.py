"""Bounded metadata-only diagnostics for AI generation; never log prompt/body/credentials."""
import contextvars
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import time

_context = contextvars.ContextVar('generation_trace', default={})
logger = logging.getLogger('test_platform.generation')
_FIELDS = set('job_id kind provider project_id ref_id ref_kind analysis_id ai_task_id baseline_id batch_id call_id stage status attempt error_code error_type elapsed_ms wait_ms prompt_chars prompt_sha256 output_chars first_output_ms idle_ms transport_lines parsed_events heartbeats got_result exit_code process_id timeout_seconds via_stdin case_count criterion_count missing_count warning_count unit_count reused event_type value_type bytes_count commit_requested'.split())


def configure(directory):
    path = str(Path(directory) / 'generation.jsonl')
    if any(getattr(handler, 'baseFilename', None) == str(Path(path).resolve()) for handler in logger.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    emit("diagnostics_ready")


def emit(event, **fields):
    try:
        data = {'time': datetime.now(timezone.utc).isoformat(), 'event': event}
        for key, value in {**_context.get(), **fields}.items():
            if key in _FIELDS and (value is None or isinstance(value, (str, int, float, bool))):
                data[key] = value[:160] if isinstance(value, str) else value
        logger.info(json.dumps(data, ensure_ascii=False))
    except Exception:
        # Diagnostics must never stop a generation job.
        pass


@contextmanager
def scope(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def bind(fn):
    context = contextvars.copy_context()
    return lambda *args, **kwargs: context.copy().run(fn, *args, **kwargs)


def error_code(error):
    text = str(error).lower()
    if '无法识别的正文格式' in text or 'unsupported_text' in text: return 'unsupported_text'
    if 'stream idle timeout' in text: return 'upstream_stream_idle_timeout'
    if any(word in text for word in ('empty or malformed', 'no_events')): return 'upstream_empty_response'
    if any(word in text for word in ('timeout', 'timed out', '超时')): return 'timeout'
    return getattr(error, 'code', None) or type(error).__name__


def fingerprint(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def run_stage(fn, **fields):
    with scope(**fields):
        started = time.monotonic()
        emit('stage_start')
        try:
            result = fn()
        except Exception as error:
            emit('stage_failed', error_code=error_code(error), error_type=type(error).__name__, elapsed_ms=int((time.monotonic()-started)*1000))
            raise
        emit('stage_done', elapsed_ms=int((time.monotonic()-started)*1000))
        return result


def model_call(fn):
    from functools import wraps
    from uuid import uuid4
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with scope(call_id=uuid4().hex[:12]):
            started = time.monotonic()
            outcome = 'interrupted'
            emit('model_call_start')
            try:
                for event in fn(*args, **kwargs):
                    if event.get('type') == 'error' or event.get('is_error'):
                        outcome = 'failed'
                        emit('model_call_error', error_code=error_code(event.get('msg') or event.get('error') or event.get('text') or 'provider_error'))
                    elif event.get('type') == 'result' and outcome != 'failed':
                        outcome = 'done'
                    yield event
            except Exception as error:
                outcome = 'failed'
                emit('model_call_exception', error_code=error_code(error), error_type=type(error).__name__)
                raise
            finally:
                emit('model_call_end', status=outcome, elapsed_ms=int((time.monotonic()-started)*1000))
    return wrapped
