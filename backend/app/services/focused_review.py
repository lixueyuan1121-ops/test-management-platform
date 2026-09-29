"""Deterministic review policy: ask for decisions, not routine acknowledgement."""
import json
from app.schemas.requirement_analysis import AcceptanceCriterion, AcceptanceScenario


def complete_answered_rules(draft):
    """Project scoped human answers into missing acceptance fields, retaining provenance.

    Recompute generated fields when answers change; preserve all manually edited fields.
    Unanswered questions and inferred/uncertain evidence remain subject to review.
    """
    for rule in draft.rules:
        if rule.status == 'excluded':
            continue
        old = rule.review_completion
        if old.get('expected') == rule.expected:
            rule.expected = ''
        if old.get('criteria') == json.dumps([c.model_dump() for c in rule.criteria], ensure_ascii=False, separators=(',', ':')):
            rule.criteria = []
        if old.get('scene'):
            draft.scenarios = [s for s in draft.scenarios if json.dumps(s.model_dump(), ensure_ascii=False, separators=(',', ':')) != old['scene']]
        rule.review_completion = {}
        answers = [q for q in draft.questions if q.answer.strip() and rule.id in q.rule_ids]
        if not rule.expected and answers:
            text = '操作：' + rule.action + '。验收按已填写的处理结论：\n' + '\n'.join(q.id + '：' + q.answer for q in answers)
            if len(text) <= 2000:
                rule.expected = text
                rule.review_completion['expected'] = text
        if rule.expected and not rule.criteria:
            text = rule.expected
            if len(text) <= 2000:
                rule.criteria = [AcceptanceCriterion(id=rule.id + '-AC', text=text)]
                rule.review_completion['criteria'] = json.dumps([c.model_dump() for c in rule.criteria], ensure_ascii=False, separators=(',', ':'))
        if rule.review_completion and rule.condition and rule.action and rule.expected and not any(s.rule_id == rule.id for s in draft.scenarios):
            scene = AcceptanceScenario(id=rule.id+'-AS', rule_id=rule.id, criterion_ids=[c.id for c in rule.criteria],
                actor='执行该操作的用户', given=rule.condition, when=rule.action, then=rule.expected)
            draft.scenarios.append(scene)
            rule.review_completion['scene'] = json.dumps(scene.model_dump(), ensure_ascii=False, separators=(',', ':'))
    return draft


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
    complete_answered_rules(draft)
    for rule in draft.rules:
        if rule.status == 'excluded':
            continue
        issues = rule_issues(draft, rule, visuals)
        rule.status = 'pending' if issues else 'confirmed'
    return draft
