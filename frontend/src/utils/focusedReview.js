// Keep in sync with the server: only copy answers attached to this rule, never invent outcomes.
export function completedRule(draft, original) {
  const rule = JSON.parse(JSON.stringify(original))
  if (rule.status === 'excluded') return rule
  const old = rule.review_completion || {}
  if (old.expected === rule.expected) rule.expected = ''
  if (old.criteria === JSON.stringify(rule.criteria)) rule.criteria = []
  rule.review_completion = {}
  const answers = (draft.questions || []).filter(q => q.answer?.trim() && q.rule_ids?.includes(rule.id))
  if (!rule.expected && answers.length) {
    const text = '操作：' + rule.action + '。验收按已填写的处理结论：\n' + answers.map(q => q.id + '：' + q.answer).join('\n')
    if (text.length <= 2000) { rule.expected = text; rule.review_completion.expected = text }
  }
  if (rule.expected && !rule.criteria?.length && rule.expected.length <= 2000) {
    rule.criteria = [{id: rule.id + '-AC', text: rule.expected}]
    rule.review_completion.criteria = JSON.stringify(rule.criteria)
  }
  return rule
}
const filled = value => typeof value === 'string' && !!value.trim()
export function ruleIssues(draft, rule, visuals = []) {
  if (rule.status === 'excluded') return []
  const issues = []
  if ((draft.questions || []).some(q => q.blocking && !filled(q.answer) && (!q.rule_ids.length || q.rule_ids.includes(rule.id)))) issues.push('还有影响这条规则的问题没说清楚，请先填写处理结论。')
  if (![rule.condition, rule.action, rule.expected].every(filled) || !rule.criteria?.length || rule.criteria.some(c => !filled(c.text))) issues.push('还没说清楚什么时候操作、做什么，或应该看到什么结果，请补齐规则。')
  if (rule.source_type !== 'explicit' && !filled(rule.review_note)) issues.push('这条是根据资料推测的。是否按这个理解测试？请填写依据，或选择本期不测。')
  if (visuals.some(v => rule.source_material_ids?.includes(v.id) && v.status !== 'read') && !filled(rule.review_note)) issues.push('相关图片没有读清楚，请对照原图说明正确内容。')
  if (draft.scenario_review_required || draft.scenarios?.length) {
    const scenes = (draft.scenarios || []).filter(s => s.rule_id === rule.id)
    const covered = new Set(scenes.flatMap(s => s.criterion_ids))
    if (rule.criteria?.some(c => !covered.has(c.id))) issues.push('有测试条件还没有对应的操作示例，请补齐场景。')
    if (scenes.some(s => ![s.actor, s.given, s.when, s.then].every(filled))) issues.push('操作示例还缺少使用者、开始状态、操作或结果，请补齐场景。')
  }
  return issues
}
export function reviewRows(draft, visuals = []) {
  return (draft?.rules || []).map(original => {
    const rule = completedRule(draft, original)
    let scenes = (draft.scenarios || []).filter(s => JSON.stringify(s) !== original.review_completion?.scene)
    if (Object.keys(rule.review_completion || {}).length && rule.condition && rule.action && rule.expected && !scenes.some(s => s.rule_id === rule.id)) {
      scenes = [...scenes, {id:rule.id+'-AS',rule_id:rule.id,criterion_ids:rule.criteria.map(c=>c.id),actor:'执行该操作的用户',given:rule.condition,when:rule.action,then:rule.expected}]
    }
    const issues = ruleIssues({...draft,scenarios:scenes}, rule, visuals)
    return {rule, issues, status: rule.status === 'excluded' ? 'excluded' : issues.length ? 'pending' : 'confirmed'}
  })
}
export function filterRules(rows, filter) {
  return rows.filter(row => filter === 'all' || row.status === filter)
}
