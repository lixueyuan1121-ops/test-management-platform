"""Nested target and explicit response-key validation, without database access."""
from app.services.claude_runner import _validate_script, _unregistered_keys, _pages_for_script
from app.services.script_keys import referenced_keys


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
    print("OK nested targets, key references, press, empty text assertions")


if __name__ == '__main__':
    main()
