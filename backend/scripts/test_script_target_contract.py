"""Nested target and explicit response-key validation, without database access."""
from app.services.claude_runner import _validate_script, _unregistered_keys, _pages_for_script
from app.services.script_keys import referenced_keys
from app.services.script_targets import validate_targets


def main():
    script = [
        {"action": "press", "target": {"key": "input", "within": {"key": "row", "has_text": "Alice"}}, "args": {"key_name": "Enter"}},
        {"action": "wait_response", "args": {"stop_key": "stop", "complete_key": "done"}},
        {"action": "assert_text", "target": {"key": "value"}, "args": {"expected": "", "negate": False}},
    ]
    assert referenced_keys(script) == ["input", "row", "stop", "done", "value"]
    keys = set(referenced_keys(script))
    assert _validate_script(script, keys)[1] is None
    assert _unregistered_keys(script, keys - {"row", "done"}) == ["row", "done"]
    assert _pages_for_script(script, {"row": "records", "done": "chat"}) == "records,chat"
    assert "未注册" in _validate_script(script, keys - {"row"})[1]
    assert "未注册" in _validate_script(script, keys - {"done"})[1]
    for nth in [-1, 0.5, True]:
        invalid = [{"action": "assert_visible", "target": {"key": "value", "nth": nth}}]
        assert _validate_script(invalid, keys)[1]
    precise = [{"action": "click", "target": {"key": "value", "has_text": "视觉PPT制作", "has_text_exact": True,
               "within": {"key": "row", "has_text": "我的技能", "has_text_exact": True}}}]
    validate_targets(precise)
    precise.append({"action": "assert_visible", "target": {"key": "value"}})
    assert _validate_script(precise, keys)[1] is None
    for fields in ({"has_text_exact": True}, {"has_text": "x", "has_text_exact": "true"},
                   {"has_text": " ", "has_text_exact": False}, {"has_text": 12}):
        invalid = [{"action": "click", "target": {"key": "value", "within": {"key": "row", **fields}}}]
        try:
            validate_targets(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid exact text: {fields}")
        assert _validate_script(invalid, keys)[1]
    print("OK nested targets, key references, press, empty text assertions")


if __name__ == '__main__':
    main()
