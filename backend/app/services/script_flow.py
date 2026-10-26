"""Flat script-flow-v1 contract, mirrored by the standalone skill/Runner."""
import json
import math
import re

FLOW_ACTIONS = {'flow_start', 'set_var', 'probe', 'get_attribute'}
def scalar(v):
    return v is None or isinstance(v, (str, bool)) or type(v) in (int, float) and math.isfinite(v)
def name(v):
    return isinstance(v, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', v) and v not in ('__proto__', 'constructor', 'prototype')
def is_ref(v):
    return isinstance(v, dict) and '$var' in v
def uses_flow(script):
    return any(isinstance(s, dict) and (isinstance(s.get('action'), str) and s['action'] in FLOW_ACTIONS or 'when' in s or 'phase' in s or '"$var"' in json.dumps(s) or isinstance(s.get('target'), dict) and 'attribute' in s['target']) for s in script)
def need(v, msg):
    if not v: raise ValueError(msg)
def walk(v, defined, path=()):
    if is_ref(v):
        need(len(v) == 1 and name(v['$var']) and v['$var'] in defined, f"变量未定义或引用无效：{v.get('$var')}")
        need(path[-1] in ('text','expected','checked','values','has_text','value') or 'values' in path, '此字段不支持变量')
    elif isinstance(v, list):
        for i, x in enumerate(v): walk(x, defined, (*path, str(i)))
    elif isinstance(v, dict):
        for k, x in v.items():
            need(k not in ('__proto__','constructor','prototype'), '禁止的对象字段')
            walk(x, defined, (*path, k))
def validate_attribute(a):
    need(isinstance(a, dict) and isinstance(a.get('name'), str) and re.fullmatch(r'(data-[\w-]+|aria-[\w-]+|id|title|href|src|value)', a['name']) and (isinstance(a.get('value'), str) or is_ref(a.get('value'))), 'attribute 需要受支持的 name 和字符串 value')
def validate_flow_target(t, depth=0):
    need(isinstance(t, dict) and depth <= 3 and any(isinstance(t.get(k), str) and t[k].strip() for k in ('key','selector')), '目标需要真实 key/selector，within 最多三层')
    for k in ('key','selector','frame'):
        if k in t: need(isinstance(t[k], str) and t[k].strip(), f'{k} 必须为非空字符串')
    if 'has_text' in t: need(isinstance(t['has_text'], str) or is_ref(t['has_text']), 'has_text 类型错误')
    if 'has_text_exact' in t: need(type(t['has_text_exact']) is bool and (is_ref(t.get('has_text')) or isinstance(t.get('has_text'), str) and t['has_text'].strip()), 'has_text_exact 需要非空文本')
    if 'nth' in t: need(type(t['nth']) is int and t['nth'] >= 0, 'nth 必须是非负整数')
    if 'visible' in t: need(type(t['visible']) is bool, 'visible 必须是布尔值')
    if 'attribute' in t: validate_attribute(t['attribute'])
    if t.get('within') is not None: validate_flow_target(t['within'], depth+1)
def validate_flow(script):
    if not uses_flow(script): return False
    first = script[0]
    need(isinstance(first, dict) and isinstance(first.get('args'), dict), '条件脚本必须以 flow_start(version=1) 开始')
    need(first.get('action') == 'flow_start' and type((first.get('args') or {}).get('version')) is int and first['args']['version'] == 1 and not first.get('when') and not first.get('phase'), '条件脚本必须以 flow_start(version=1) 开始')
    defined, cleanup = set(), False
    for i, s in enumerate(script):
        need(isinstance(s, dict) and isinstance(s.get('action'), str), '步骤需要字符串 action')
        a, action = s.get('args') or {}, s.get('action')
        need(isinstance(a, dict) and (s.get('target') is None or isinstance(s['target'], dict)), 'args/target 必须是对象')
        need('phase' not in s or s['phase'] in ('setup','test','cleanup'), 'phase 只能是 setup/test/cleanup')
        if s.get('phase') == 'cleanup': cleanup = True
        else: need(not cleanup, 'cleanup 必须位于脚本末尾')
        if 'when' in s:
            w = s['when']
            need(isinstance(w, dict) and name(w.get('var')) and w['var'] in defined, 'when 必须引用之前声明的变量')
            need(len(w) == 2 and ('equals' in w and scalar(w['equals']) or w.get('is_set') is True), 'when 使用 {var, equals} 或 {var, is_set:true}')
        walk(s.get('target'), defined, ('target',)); walk(a, defined, ('args',))
        if s.get('target') or action in ('probe','get_attribute'): validate_flow_target(s.get('target'))
        if action == 'flow_start': need(i == 0 and len(a) == 1, 'flow_start 仅允许 version=1')
        if action == 'set_var':
            need(name(a.get('name')) and (('value' in a) != ('generate' in a)), 'set_var 需要 name 和 value 或 generate')
            need(scalar(a['value']) or is_ref(a['value']) if 'value' in a else a['generate'] == 'unique' and ('prefix' not in a or isinstance(a['prefix'], str) and len(a['prefix']) <= 80), 'set_var 仅支持标量或 unique')
            defined.add(a['name'])
        if action == 'probe':
            need(isinstance(a.get('ready'), dict) and (a['ready'].get('selector') or a['ready'].get('key')), 'probe 必须提供已加载完成的 args.ready 定位')
            validate_flow_target(a['ready'])
            need(type(a.get('timeout_ms')) is int and 0 <= a['timeout_ms'] <= 30000, 'probe timeout_ms 需要0–30000')
            need(type(a.get('settle_ms')) is int and 0 <= a['settle_ms'] <= a['timeout_ms'], 'probe settle_ms 需要0–timeout_ms')
        if action == 'get_attribute': need(isinstance(a.get('attribute_name'), str) and re.fullmatch(r'(data-[\w-]+|aria-[\w-]+|id|title|href|src|value)', a['attribute_name']), 'get_attribute 属性不在允许范围')
        if 'expected_from' in a: need(action == 'assert_text' and a['expected_from'] in defined and 'expected' not in a, 'expected_from 必须引用之前捕获的变量')
        if 'save_as' in a:
            need(action in ('get_text','get_attribute','probe') and name(a['save_as']), 'save_as 仅支持文本、属性和探测步骤')
            defined.add(a['save_as'])
        else: need(action not in ('probe','get_attribute'), f'{action} 缺少 save_as')
        if (s.get('target') or {}).get('attribute'): validate_attribute(s['target']['attribute'])
        if cleanup: need(action not in ('flow_start','probe','set_var','mock_route','fault_route','watch_network'), 'cleanup 仅恢复本条用例状态')
    need(any(s.get('phase') != 'cleanup' and s['action'].startswith('assert_') for s in script), '缺少业务断言')
    return True

class FlowValues:
    def __init__(self): self.values = {}
    def read(self, k):
        need(k in self.values, f'本次分支尚未赋值：{k}')
        return self.values[k]
    def resolve(self, v):
        if is_ref(v): return self.read(v['$var'])
        if isinstance(v, dict): return {k: self.resolve(x) for k, x in v.items()}
        if isinstance(v, list): return [self.resolve(x) for x in v]
        return v
    def enabled(self, s):
        w = s.get('when')
        if not w: return True
        if w.get('is_set'): return w['var'] in self.values
        v = self.read(w['var'])
        return type(v) is type(w['equals']) and v == w['equals'] or type(v) in (int,float) and type(w['equals']) in (int,float) and v == w['equals']

def validate_flow_report(case, refs):
    validate_flow(case.script)
    need(len(case.report) == len(case.script), '条件脚本报告须包含分支跳过和收尾记录')
    flow, failed, checks = FlowValues(), False, 0
    for i, (s, r) in enumerate(zip(case.script, case.report)):
        a, action, cleanup = s.get('args') or {}, s['action'], s.get('phase') == 'cleanup'
        need(r.get('no') == i + 1 and r.get('action') == action and isinstance(r.get('observation'), str) and r['observation'].strip() and r.get('evidence_ref') in refs, '步骤须对应脚本并保留真实证据')
        skip = 'previous_failure' if failed and not cleanup else 'condition_false' if not flow.enabled(s) else None
        if skip:
            need(r.get('ok') is True and r.get('skipped') is True and r.get('skip_reason') == skip and not r.get('check') and 'value' not in r and 'text' not in r, '未执行分支须明确标记，不能伪造断言/捕获值')
            continue
        flow.resolve(s.get('target'))
        args = flow.resolve(a)
        if r.get('skipped'):
            need(action == 'click' and a.get('if_visible') is True and r.get('ok') is True and not r.get('check'), '不能跳过必要业务步骤')
            continue
        if 'expected_from' in a: args['expected'] = flow.read(a['expected_from'])
        if action == 'set_var':
            if a.get('generate'):
                prefix = a.get('prefix') or 'qa-'
                need(isinstance(r.get('value'), str) and r['value'].startswith(prefix) and re.fullmatch(r'[0-9a-fA-F-]{36}', r['value'][len(prefix):]), 'unique 须记录本次实际唯一值')
            else:
                v = flow.resolve(a['value'])
                need(type(r.get('value')) is type(v) and r.get('value') == v, 'set_var 记录与脚本不一致')
            flow.values[a['name']] = r.get('value')
        elif action in ('get_text','get_attribute','probe'):
            value = r.get('text') if action == 'get_text' else r.get('value')
            need(type(value) is (bool if action == 'probe' else str), '缺少实际捕获值')
            if a.get('save_as'): flow.values[a['save_as']] = value.strip() if action == 'get_text' else value
        if not action.startswith('assert'):
            need(r.get('ok') is True, '执行阻塞不能作为业务失败导入')
            continue
        if not cleanup: checks += 1
        k = r.get('check')
        need(isinstance(k, dict) and 'actual' in k and 'expected' in k and type(k.get('pass')) is bool and r.get('ok') is k['pass'], '断言缺 actual/expected/pass')
        actual, expected = k['actual'], k['expected']
        if action == 'assert_text':
            need(isinstance(actual, str) and isinstance(expected, str) and expected == args.get('expected'), '文本断言预期与脚本/捕获不一致')
            matched = expected in actual if args.get('contains') else actual == expected
            if args.get('negate'): matched = not matched
        elif action in ('assert_visible','assert_absent'):
            need(type(actual) is bool and expected is (action == 'assert_visible'), '可见性预期无效')
            matched = actual == expected
        elif action in ('assert_network_count','assert_fault_hits'):
            need(type(actual) is int and actual >= 0 and expected == args.get('expected'), '网络计数无效')
            matched = actual == expected
        else: matched = actual == expected
        need(matched is k['pass'], '断言结论与实际值矛盾')
        if not matched:
            need(not cleanup and not failed and r.get('fail_kind') == 'business' and isinstance(r.get('error'), str) and r['error'].strip(), '收尾失败或执行阻塞不能冒充业务失败')
            failed = True
    need(checks > 0 and (case.verdict == 'fail') == failed, '没有执行业务断言或用例结论与报告不一致')
