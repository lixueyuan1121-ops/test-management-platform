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
        if step.get("action") == "wait_response" and isinstance(args, dict):
            for name in ("stop_key", "complete_key"):
                key = args.get(name)
                if isinstance(key, str) and key and key not in found:
                    found.append(key)
    return found
