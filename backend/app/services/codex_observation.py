"""Consistency of Codex tool observations, not Runner replay certification."""
from app.services.execution_evidence import fingerprint, normalize_script


def validate_codex_observation(case):
    if case.precondition.strip():
        raise ValueError('请将前置导航写入完整 script，不依赖生成电脑的页面状态')
    if case.execution_evidence is not None or case.selector_registry is not None:
        raise ValueError('Codex 实测不能携带或冒充 Runner 验证证据')
    if not any(str(s.get('action', '')).startswith('assert') for s in case.script):
        raise ValueError('完整用例必须包含业务断言')
    evidence = case.codex_evidence
    if evidence.script_sha256 != fingerprint(normalize_script(case.script)) or evidence.report_sha256 != fingerprint(case.report):
        raise ValueError('脚本或报告与 Codex 实测包不一致；请核对真实操作并重新验证改动后的流程')
    refs = {a.id for a in evidence.artifacts}
    if len(refs) != len(evidence.artifacts):
        raise ValueError('证据 id 必须唯一')
    from app.services.script_flow import uses_flow, validate_flow_report
    if uses_flow(case.script):
        validate_flow_report(case, refs)
        return
    if len(case.report) > len(case.script) or (case.verdict == 'pass' and len(case.report) != len(case.script)):
        raise ValueError('通过须覆盖全部步骤；失败仅记录实际执行到失败处的前缀')
    captures = {}
    for i, (step, result) in enumerate(zip(case.script, case.report)):
        action, args = step.get('action'), step.get('args') or {}
        failed = case.verdict == 'fail' and i == len(case.report) - 1
        if result.get('no') != i + 1 or result.get('action') != action or result.get('ok') is not (not failed):
            raise ValueError('实测报告须按脚本顺序对应，且业务失败只能位于报告最后一步')
        if not isinstance(result.get('observation'), str) or not result['observation'].strip() or result.get('evidence_ref') not in refs:
            raise ValueError('每步须保留真实观察与可追溯工具日志/截图引用')
        if failed and (not str(action).startswith('assert') or result.get('fail_kind') != 'business' or not str(result.get('error') or '').strip()):
            raise ValueError('仅允许有业务断言证据的失败；连接、定位和工具阻塞不能冒充业务失败')
        if action == 'get_text' and args.get('save_as'):
            if not isinstance(result.get('text'), str):
                raise ValueError('动态文本捕获须保存实际读取值')
            captures[args['save_as']] = result['text']
        if not str(action).startswith('assert'):
            continue
        check = result.get('check')
        if not isinstance(check, dict) or 'actual' not in check or 'expected' not in check or check.get('pass') is not (not failed):
            raise ValueError('业务断言须包含实际值、预期值与一致的 pass 状态')
        actual, expected = check['actual'], check['expected']
        matched = check['pass']
        if action == 'assert_text':
            wanted = captures.get(args.get('expected_from')) if 'expected_from' in args else args.get('expected')
            if not isinstance(actual, str) or not isinstance(expected, str) or wanted != expected:
                raise ValueError('文本断言预期须对应脚本或本次实际捕获值')
            matched = expected in actual if args.get('contains') else actual == expected
            if args.get('negate'):
                matched = not matched
        elif action in ('assert_visible', 'assert_absent'):
            if type(actual) is not bool or expected is not (action == 'assert_visible'):
                raise ValueError('可见性断言须提供布尔实际值及正确预期')
            matched = actual == expected
        elif action in ('assert_network_count', 'assert_fault_hits'):
            if type(actual) is not int or actual < 0 or expected != args.get('expected'):
                raise ValueError('网络计数实际值/预期无效')
            matched = actual == expected
        if matched != check['pass']:
            raise ValueError('断言实际值与通过/失败结论矛盾')
