"""纳米 Work 执行配置的笛卡尔积；runner 始终收到单套字符串选项。"""
import hashlib
import itertools
import json

CHAT_MODES = {"边想边做", "先规划，再执行", "盯住目标做到底"}
THINKING_DEPTHS = {"低", "中", "标准", "高", "超高"}
MAX_COMBINATIONS = 300
MAX_RUNS = 10000
KEYS = ("chatMode", "model", "thinkingDepth")


def clean_matrix(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - set(KEYS):
        raise ValueError("纳米Work组合配置仅支持对话模式、模型和思考深度")
    out = {}
    for key in KEYS:
        values = raw.get(key, [])
        if not isinstance(values, list) or len(values) > MAX_COMBINATIONS:
            raise ValueError(f"组合配置 {key} 必须是数组，最多 {MAX_COMBINATIONS} 项")
        seen, cleaned = set(), []
        for value in values:
            if not isinstance(value, str) or len(value.strip()) > 64:
                raise ValueError(f"组合配置 {key} 的值必须是长度不超过64的文本")
            value = value.strip()
            if not value:
                continue
            choices = CHAT_MODES if key == "chatMode" else THINKING_DEPTHS if key == "thinkingDepth" else None
            if choices and value not in choices:
                raise ValueError(f"不支持的 {key}：{value}")
            identity = value.lower() if key == "model" else value
            if identity not in seen:
                seen.add(identity)
                cleaned.append(value)
        out[key] = cleaned
    count = 1
    for values in out.values():
        count *= max(1, len(values))
    if count > MAX_COMBINATIONS:
        raise ValueError(f"纳米Work配置组合共 {count} 种，单批最多 {MAX_COMBINATIONS} 种，请减少选项")
    return out


def expand_matrix(matrix):
    """空维度保留一次默认执行，不能将整个笛卡尔积变为空。"""
    return [dict((key, value) for key, value in zip(KEYS, values) if value)
            for values in itertools.product(*(matrix[key] or [None] for key in KEYS))]


def configuration_meta(options, index, count):
    identity = dict(options)
    if identity.get("model"):
        identity["model"] = identity["model"].lower()
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    label = " · ".join(f"{name}:{options.get(key) or '沿用配置'}" for key, name in
                       (("chatMode", "模式"), ("model", "模型"), ("thinkingDepth", "深度")))
    return {"configuration_id": digest, "configuration_index": index,
            "configuration_count": count, "configuration_label": label}
