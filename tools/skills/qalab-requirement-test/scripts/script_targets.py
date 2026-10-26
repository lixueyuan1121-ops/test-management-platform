"""Offline GUI target shape checks; no DOM lookup, key registry or script rewriting.
Keep the standalone skill copy and backend copy behavior aligned by contract tests.
"""
from script_flow import validate_flow, is_ref, validate_attribute
TARGET_ACTIONS = frozenset(("click", "hover", "fill", "type", "set_checked", "select_option",
                            "wait_for", "get_text", "get_attribute", "probe", "assert_text", "assert_visible", "assert_absent", "assert_list_from_response"))

VALID_ACTIONS = TARGET_ACTIONS | {"connect", "press", "wait_response", "screenshot", "mock_route", "unmock_route"}
NETWORK_ACTIONS = {"watch_network", "fault_route", "release_fault", "wait_network", "assert_network_count", "assert_fault_hits", "assert_list_from_response"}
VALID_ACTIONS |= NETWORK_ACTIONS
VALID_ACTIONS |= {"flow_start", "set_var"}

def validate_network_script(script):
    import re
    watches, faults = {}, {}
    def integer(v, lo, hi):
        return type(v) is int and lo <= v <= hi
    def field(v):
        return isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", v) and not {"__proto__", "prototype", "constructor"}.intersection(v.split('.'))
    for step in script:
        action, a = step.get('action'), step.get('args') or {}
        if action not in NETWORK_ACTIONS:
            continue
        if not isinstance(a, dict) or not isinstance(a.get('id'), str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', a['id']):
            raise ValueError('网络步骤缺少有效 args.id')
        for key in ('timeout_ms', 'settle_ms'):
            if key in a and not integer(a[key], 0, 30000):
                raise ValueError(f'{key} 必须为 0–30000 的整数')
        if action in ('watch_network', 'fault_route'):
            if not isinstance(a.get('path'), str) or not re.fullmatch(r'/(?!/)[^?#*\s]+', a['path']):
                raise ValueError('path 必须是精确接口路径')
            if a.get('method') not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
                raise ValueError('method 必须显式指定 HTTP 方法')
            if a.get('frame') != 'shell' and not (isinstance(a.get('frame'), str) and a['frame'].startswith('url:') and len(a['frame']) > 4):
                raise ValueError('frame 必须是 shell 或唯一 url: frame')
            if 'query' in a and (not isinstance(a['query'], dict) or not all(isinstance(v, str) for v in a['query'].values())):
                raise ValueError('query 必须是字符串字典')
        if action == 'watch_network':
            if a['id'] in watches:
                raise ValueError('watch id 重复')
            if ('response_path' in a or 'item_field' in a) and not (field(a.get('response_path')) and field(a.get('item_field'))):
                raise ValueError('列表捕获需要 response_path 和 item_field')
            watches[a['id']] = a
        elif action == 'fault_route':
            if a['id'] in faults or a.get('mode') not in ('response', 'network_error', 'hold_response'):
                raise ValueError('fault id 重复或 mode 无效')
            if a['mode'] == 'response' and (not integer(a.get('status'), 200, 599) or a['status'] in (204, 205, 304) or 'body' not in a):
                raise ValueError('response 需要有效 status 和 JSON body')
            if a['mode'] == 'hold_response' and not integer(a.get('timeout_ms'), 100, 30000):
                raise ValueError('hold_response 需要 100–30000ms timeout_ms')
            faults[a['id']] = True
        elif action in ('release_fault', 'assert_fault_hits'):
            if a['id'] not in faults:
                raise ValueError('fault id 尚未注册')
            if action == 'release_fault':
                if not faults[a['id']]:
                    raise ValueError('fault 已释放')
                faults[a['id']] = False
        elif a['id'] not in watches:
            raise ValueError('watch id 尚未注册')
        if action in ('assert_network_count', 'assert_fault_hits') and not integer(a.get('expected'), 0, 9007199254740991):
            raise ValueError('expected 必须是非负整数')
        if action == 'wait_network' and (a.get('phase') not in ('requested', 'received', 'completed') or not integer(a.get('count'), 1, 9007199254740991)):
            raise ValueError('wait_network 需要 phase 和正整数 count')
        if action == 'assert_list_from_response':
            if not watches[a['id']].get('response_path'):
                raise ValueError('watch 未配置列表字段')
            if 'allow_empty' in a and type(a['allow_empty']) is not bool:
                raise ValueError('allow_empty 必须为布尔值')

def validate_targets(script, title=""):
    if not isinstance(script, list) or not script:
        raise ValueError(f"{title}: script 必须是非空数组")
    if all(isinstance(s, dict) for s in script):
        validate_flow(script)
        validate_network_script(script)
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
        args = step.get('args') or {}
        if action == 'probe':
            ready, ready_depth = args.get('ready'), 0
            while ready is not None:
                if not isinstance(ready, dict) or not any(isinstance(ready.get(k), str) and ready[k].strip() for k in ('key','selector')) or ready_depth > 3:
                    raise ValueError(f'{prefix} ready 缺少有效定位')
                if 'attribute' in ready: validate_attribute(ready['attribute'])
                if 'has_text' in ready and not (isinstance(ready['has_text'], str) or is_ref(ready['has_text'])):
                    raise ValueError(f'{prefix} ready.has_text 无效')
                ready, ready_depth = ready.get('within'), ready_depth + 1
        if action in ('fill', 'type') and not (isinstance(args.get('text'), str) or is_ref(args.get('text'))):
            raise ValueError(f'{prefix} 缺 args.text')
        if action == 'assert_text' and 'expected_from' not in args and not (isinstance(args.get('expected'), str) or is_ref(args.get('expected'))):
            raise ValueError(f'{prefix} 缺 args.expected')
        if action == 'set_checked' and not (type(args.get('checked')) is bool or is_ref(args.get('checked'))):
            raise ValueError(f'{prefix} 缺 args.checked')
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
            if "has_text" in current and not isinstance(current["has_text"], str) and not is_ref(current["has_text"]):
                raise ValueError(f"{prefix} has_text 必须是字符串")
            if "has_text_exact" in current and (type(current["has_text_exact"]) is not bool or not (is_ref(current.get("has_text")) or isinstance(current.get("has_text"), str) and current["has_text"].strip())):
                raise ValueError(f"{prefix} has_text_exact 必须为布尔值，且同时填写非空 has_text")
            if "attribute" in current:
                validate_attribute(current["attribute"])
            current = current.get("within")
            if current is None:
                break
            depth += 1
            if depth > 3:
                raise ValueError(f"{prefix} within 最多嵌套三层")
