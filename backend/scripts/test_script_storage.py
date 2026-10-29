"""Executable JSON must fit the production MySQL TEXT byte capacity."""
import json

from app.services.script_storage import SCRIPT_TEXT_MAX_BYTES, encode_script_for_storage


def test_exact_boundary_round_trips():
    overhead = len(json.dumps([{"desc": ""}], ensure_ascii=False).encode("utf-8"))
    script = [{"desc": "a" * (SCRIPT_TEXT_MAX_BYTES - overhead)}]
    raw = encode_script_for_storage(script)
    assert len(raw.encode("utf-8")) == SCRIPT_TEXT_MAX_BYTES
    assert json.loads(raw) == script


def test_one_byte_over_is_rejected():
    overhead = len(json.dumps([{"desc": ""}], ensure_ascii=False).encode("utf-8"))
    try:
        encode_script_for_storage([{"desc": "a" * (SCRIPT_TEXT_MAX_BYTES - overhead + 1)}])
    except ValueError as exc:
        assert "未保存" in str(exc)
    else:
        raise AssertionError("Oversized JSON was accepted")


def test_chinese_text_is_counted_as_utf8_bytes():
    script = [{"desc": "中" * 22000}]
    assert len(json.dumps(script, ensure_ascii=False)) < SCRIPT_TEXT_MAX_BYTES
    try:
        encode_script_for_storage(script)
    except ValueError as exc:
        assert "66014" in str(exc)
    else:
        raise AssertionError("Multibyte JSON was accepted beyond TEXT capacity")


if __name__ == "__main__":
    test_exact_boundary_round_trips()
    test_one_byte_over_is_rejected()
    test_chinese_text_is_counted_as_utf8_bytes()
    print("OK: 3 script storage boundary checks")
