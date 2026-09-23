"""Offline GUI target shape checks; no DOM lookup, key registry or script rewriting.
Keep the standalone skill copy and backend copy behavior aligned by contract tests.
"""
TARGET_ACTIONS = frozenset(("click", "hover", "fill", "type", "set_checked", "select_option",
                            "wait_for", "get_text", "assert_text", "assert_visible", "assert_absent"))

VALID_ACTIONS = TARGET_ACTIONS | {"connect", "press", "wait_response", "screenshot", "mock_route", "unmock_route"}

def validate_targets(script, title=""):
    if not isinstance(script, list) or not script:
        raise ValueError(f"{title}: script 必须是非空数组")
    for index, step in enumerate(script, 1):
        prefix = f"{title}: 第 {index} 步"
        if not isinstance(step, dict):
            raise ValueError(f"{prefix} 必须是对象")
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError(f"{prefix} 缺 action")
        if action.strip() not in VALID_ACTIONS:
            hint = "；请使用 wait_for + target" if action.strip() == "wait_for_element" else ""
            raise ValueError(f"{prefix} 非平台 GUI/E2E action「{action}」{hint}")
        target = step.get("target")
        if target is not None and not isinstance(target, dict):
            raise ValueError(f"{prefix}「{action}」target 必须是对象，定位写入 target.selector 或 target.key")
        if step.get("args") is not None and not isinstance(step["args"], dict):
            raise ValueError(f"{prefix}「{action}」args 必须是对象")
        if action.strip() not in TARGET_ACTIONS and not (action.strip() == "press" and target):
            continue
        current, depth = target, 0
        while True:
            if not isinstance(current, dict) or not any(isinstance(current.get(k), str) and current[k].strip() for k in ("key", "selector")):
                location = "target" if depth == 0 else "target.within"
                raise ValueError(f"{prefix}「{action}」缺 {location}.key/selector；须填写真实 DOM 定位，不能仅写操作描述或顶层 selector；修复后重跑完整脚本再导入")
            for key in ("key", "selector"):
                if key in current and (not isinstance(current[key], str) or not current[key].strip()):
                    raise ValueError(f"{prefix}「{action}」{key} 必须为非空字符串")
            current = current.get("within")
            if current is None:
                break
            depth += 1
            if depth > 3:
                raise ValueError(f"{prefix} within 最多嵌套三层")
