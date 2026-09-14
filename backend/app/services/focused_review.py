"""Deterministic review policy: ask for decisions, not routine acknowledgement."""

def rule_issues(draft, rule, visuals):
    if rule.status == 'excluded':
        return []
    issues = []
    questions = [q for q in draft.questions if q.blocking and (not q.rule_ids or rule.id in q.rule_ids)]
    unanswered = [q for q in questions if not q.answer]
    if unanswered:
        issues.append('还有影响这条规则的问题没说清楚，请先填写处理结论。')
    if not all((rule.condition, rule.action, rule.expected, rule.criteria)):
        issues.append('还没说清楚什么时候操作、做什么，或应该看到什么结果，请补齐规则。')
    if rule.source_type != 'explicit' and not rule.review_note:
        issues.append('这条是根据资料推测的。是否按这个理解测试？请填写依据，或选择本期不测。')
    if any(v['id'] in rule.source_material_ids and v['status'] != 'read' for v in visuals) and not rule.review_note:
        issues.append('相关图片没有读清楚，请对照原图说明正确内容。')
    if draft.scenario_review_required or draft.scenarios:
        scenes = [s for s in draft.scenarios if s.rule_id == rule.id]
        covered = {c for s in scenes for c in s.criterion_ids}
        if {c.id for c in rule.criteria} - covered:
            issues.append('有测试条件还没有对应的操作示例，请补齐场景。')
        if any(not all((s.actor, s.given, s.when, s.then)) for s in scenes):
            issues.append('操作示例还缺少使用者、开始状态、操作或结果，请补齐场景。')
    return issues


def apply_review_policy(draft, visuals):
    for rule in draft.rules:
        if rule.status == 'excluded':
            continue
        issues = rule_issues(draft, rule, visuals)
        rule.status = 'pending' if issues else 'confirmed'
    return draft
