"""Pure export contract checks. Browser-level execution lives in runtime.test.mjs."""
import json
import subprocess
from pathlib import Path

from app.services.playwright_exporter import export_case_to_playwright

REGISTRY = {"target": {"frame": "vm", "candidates": [{"by": "testid", "value": "target"}]}}


def main():
    text = "line1\nline2\r\n'\\\u2028\u2029"
    case = {"exec_kind": "gui", "title": "name\n'); throw new Error('injected') //", "script": [
        {"action": "fill", "target": {"key": "target"}, "args": {"text": text}},
        {"action": "assert_text", "target": {"key": "target"}, "args": {"expected": "错误", "negate": True}},
        {"action": "assert_absent", "target": {"key": "target"}},
    ]}
    output = export_case_to_playwright(case, REGISTRY, "iframe#vm")
    config_line = next(line for line in output.splitlines() if line.startswith("const config = "))
    data = json.loads(config_line.removeprefix("const config = ").removesuffix(";"))
    assert data["script"] == case["script"]
    assert data["title"] == case["title"]
    assert data["script"][0]["args"]["text"] == text
    assert "runtime.assertAbsent(a)" in output
    assert "runtime.waitResponse(args)" in output
    assert "TODO" not in output
    source = Path("app/services/playwright_runtime.mjs").read_text().replace("export function ", "function ")
    assert source in output, "export must embed the canonical runtime verbatim"
    subprocess.run(["node", "--input-type=module", "--check"], input=output, text=True, check=True, capture_output=True)

    # Unsupported work cannot turn into a passing test after export.
    for script in [
        [{"action": "judge", "args": {"question": "looks good?"}}],
        [{"action": "assert_visible", "target": {"key": "missing"}}],
        [{"action": "click", "target": {"key": "target"}}],
        [{"action": "assert_visible", "target": {"key": "target", "nth": -1}}],
        [{"action": "assert_visible", "target": {"key": "target", "within": {"key": "missing"}}}],
    ]:
        try:
            export_case_to_playwright({"script": script}, REGISTRY, "iframe")
            raise AssertionError("incomplete export was accepted")
        except ValueError:
            pass
    print("OK export: shared semantics, negation/multiline fidelity, incomplete-case rejection, JS syntax")


if __name__ == "__main__":
    main()
