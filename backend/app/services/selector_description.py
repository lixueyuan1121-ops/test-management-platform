"""Selector descriptions contain semantics only, never audit metadata."""
import re

FOUR_PART = re.compile(r"^\[([^\[\]]*)\]-\[([^\[\]]*)\]-\[([^\[\]]*)\]-\[([^\[\]]*)\]$")

def split_description(desc):
    match = FOUR_PART.fullmatch((desc or "").strip())
    return list(match.groups()) if match else None

def compose_description(parts):
    # Each segment is plain text, so bracket punctuation cannot break the format.
    clean = [str(p or "").strip().replace("[", "（").replace("]", "）") for p in parts]
    result = "-".join(f"[{part}]" for part in clean)
    if len(result) > 255:
        raise ValueError("四段式说明不能超过 255 个字符")
    return result

def normalize_description(desc, page="", key="", *, navigation="", scene=""):
    parts = split_description(desc)
    if parts:
        return compose_description(parts)
    label = (desc or key or "未命名元素").strip()
    return compose_description([navigation or page or "未分类", page or "未分类", scene or "页面操作", label])

def replace_segment(desc, index, value, page="", key=""):
    parts = split_description(normalize_description(desc, page, key))
    parts[index] = value
    return compose_description(parts)
