"""Shared text envelope handling; reasoning/tool blocks are never user output."""
import logging


def event_text(value, raw, kind):
    """Accept text blocks without treating tool/thinking data as analysis text."""
    from app.services.requirement_output import OutputError
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    logging.getLogger('test_platform').warning(
        'Requirement model text envelope event=%s value_type=%s', kind, type(value).__name__)

    def extract(item):
        if isinstance(item, str):
            return item
        if isinstance(item, list):
            return ''.join(extract(block) for block in item)
        if isinstance(item, dict):
            block_type = item.get('type')
            if block_type in ('text', 'output_text') and isinstance(item.get('text'), str):
                return item['text']
            if block_type in ('thinking', 'reasoning', 'redacted_thinking', 'tool_use', 'tool_result', 'image'):
                return ''
        raise OutputError('unsupported_text', '模型返回了无法识别的正文格式，已保留此前内容，请重试', raw,
                          {'event_type': kind, 'value_type': type(item).__name__})

    return extract(value)
