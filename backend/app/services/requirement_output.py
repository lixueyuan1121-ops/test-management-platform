"""Lossless JSON envelope normalization; never salvage incomplete business rules."""
import json


class OutputError(ValueError):
    def __init__(self, code, message, raw='', metadata=None):
        super().__init__(message)
        self.code, self.raw, self.metadata = code, raw, metadata or {}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise OutputError('duplicate_key', '模型返回包含重复字段，不能确定哪份内容有效')
        result[key] = value
    return result


def _constant(value):
    raise OutputError('invalid_json', '模型返回包含非 JSON 数值')


def parse_complete_object(raw):
    text = raw.lstrip('\ufeff').strip()
    # Decode an extra JSON string envelope, without interpreting escapes twice.
    if text.startswith('"'):
        try:
            outer = json.loads(text)
            if isinstance(outer, str):
                text = outer.strip()
        except ValueError:
            pass
    if text.startswith('['):
        raise OutputError('invalid_shape', '模型返回了数组，需要完整的分析对象', raw)
    start = text.find('{')
    if start < 0:
        raise OutputError('invalid_json', '模型未返回分析对象', raw)
    if text[:start].rstrip().endswith('['):
        raise OutputError('invalid_shape', '模型返回了数组，需要完整的分析对象', raw)
    objects = []
    while start >= 0:
        stack, quoted, escaped, end = [], False, False, None
        for index in range(start, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '{[':
                stack.append(char)
            elif char in '}]':
                if not stack or (stack.pop(), char) not in (('{', '}'), ('[', ']')):
                    raise OutputError('invalid_json', '模型返回的 JSON 括号不匹配', raw)
                if not stack:
                    end = index + 1
                    break
        if end is None:
            raise OutputError('truncated', '模型返回未结束，分析内容被截断', raw)
        blob = text[start:end]
        # Remove trailing delimiters only outside strings. Literal newlines inside
        # strings are preserved by strict=False. Neither operation invents values.
        normalized, quoted, escaped = [], False, False
        for index, char in enumerate(blob):
            if not quoted and char == ',' and blob[index + 1:].lstrip().startswith(('}', ']')):
                continue
            normalized.append(char)
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
        try:
            obj = json.loads(''.join(normalized), strict=False, object_pairs_hook=_pairs, parse_constant=_constant)
        except OutputError as exc:
            exc.raw = raw
            raise
        except ValueError as exc:
            raise OutputError('invalid_json', '模型返回的 JSON 语法有误', raw) from exc
        objects.append(obj)
        start = text.find('{', end)
    if any(obj != objects[0] for obj in objects[1:]):
        raise OutputError('ambiguous', '模型返回了多份不同的分析对象，不能自动选择', raw)
    return objects[0]
