"""Encode executable scripts without allowing MySQL TEXT truncation."""
import json


SCRIPT_TEXT_MAX_BYTES = 65535


def encode_script_for_storage(script: list) -> str:
    raw = json.dumps(script, ensure_ascii=False)
    size = len(raw.encode("utf-8"))
    if size > SCRIPT_TEXT_MAX_BYTES:
        raise ValueError(
            f"脚本为 {size} 字节，超过保存上限 {SCRIPT_TEXT_MAX_BYTES} 字节；"
            "请拆分用例或精简重复准备步骤。未保存，原脚本保持不变。"
        )
    return raw
