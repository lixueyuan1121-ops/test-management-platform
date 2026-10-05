export const EXECUTION_MODES = { script: '脚本执行', claude_precondition: 'Claude 前置导航 + 脚本', claude_judge: '脚本 + Claude 判定', claude_fallback: 'Claude 兜底执行', not_started: '尚未执行脚本' }
export function executionMode(row) {
  const evidence = row.execution_evidence || row.payload?.execution_evidence
  return EXECUTION_MODES[evidence?.mode] || '旧记录未记录执行方式'
}
export function selectorDiagnosis(row) {
  const diagnostic = (Array.isArray(row.report) ? row.report : []).find(s => s.diagnostic)?.diagnostic
  // Old executions already contain this count in their reason. Do not mistake
  // ambiguity for a missing selector just because structured evidence is absent.
  const count = diagnostic?.code === 'AMBIGUOUS_TARGET' ? diagnostic.match_count : Number((row.reason || '').match(/匹配\s+(\d+)\s+个元素/)?.[1])
  return count > 1 ? { label: '定位不唯一', detail: `匹配到 ${count} 个元素，请限定所属容器或 frame 后重新验证。`, count } : null
}
