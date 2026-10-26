"""Selector references include record containers and response completion signals."""


def referenced_keys(script):
    found = []
    for step in script if isinstance(script, list) else []:
        if not isinstance(step, dict):
            continue
        target = step.get("target")
        for _ in range(4):
            if not isinstance(target, dict):
                break
            key = target.get("key")
            if isinstance(key, str) and key and key not in found:
                found.append(key)
            target = target.get("within")
        args = step.get("args")
        if step.get("action") == "probe" and isinstance(args, dict):
            for key in referenced_keys([{"target": args.get("ready")}]):
                if key not in found: found.append(key)
        if step.get("action") == "wait_response" and isinstance(args, dict):
            for name in ("stop_key", "complete_key"):
                key = args.get(name)
                if isinstance(key, str) and key and key not in found:
                    found.append(key)
    return found


def remap_key(script, source, destination):
    """只改结构化定位引用，不替换用户文本、参数或其它同名 JSON 字段。"""
    import copy
    result = copy.deepcopy(script)
    for step in result:
        if step.get("action") == "probe" and isinstance(step.get("args"), dict):
            step["args"]["ready"] = remap_key([{"target": step["args"].get("ready")}], source, destination)[0]["target"]
        target = step.get("target")
        while isinstance(target, dict):
            if target.get("key") == source:
                target["key"] = destination
            target = target.get("within")
        if step.get("action") == "wait_response":
            args = step.get("args") or {}
            for name in ("stop_key", "complete_key"):
                if args.get(name) == source:
                    args[name] = destination
    return result
