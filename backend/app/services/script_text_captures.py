"""Validate per-case text captures before any client actions are executed."""
import re

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def validate_text_captures(script):
    defined = set()
    for i, step in enumerate(script):
        if not isinstance(step, dict) or not isinstance(step.get("args", {}), dict):
            return f"第 {i + 1} 步必须提供对象形式的 step/args"
        action, args = step.get("action"), step.get("args", {})
        if "save_as" in args:
            name = args["save_as"]
            if action != "get_text" or not isinstance(name, str) or not _NAME.fullmatch(name):
                return f"第 {i + 1} 步 save_as 只能用于 get_text，名称须为字母或下划线开头的标识符"
            if name in defined:
                return f"第 {i + 1} 步重复保存文本「{name}」，请使用不同名称"
            defined.add(name)
        if "expected_from" in args:
            name = args["expected_from"]
            if action != "assert_text" or not isinstance(name, str) or name not in defined:
                return f"第 {i + 1} 步 expected_from 必须引用之前 get_text 保存的文本"
            if "expected" in args:
                return f"第 {i + 1} 步 expected 和 expected_from 不能同时提供"
        elif action == "assert_text" and not isinstance(args.get("expected"), str):
            return f"第 {i + 1} 步 assert_text 缺少字符串 expected 或 expected_from"
    return None
