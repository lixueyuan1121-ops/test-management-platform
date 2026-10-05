export const EXECUTION_MODES = { script: '脚本执行', claude_precondition: 'Claude 前置导航 + 脚本', claude_judge: '脚本 + Claude 判定', claude_fallback: 'Claude 兜底执行', codex_precondition: 'Codex 前置导航 + 脚本', codex_judge: '脚本 + Codex 判定', codex_fallback: 'Codex 兜底执行', not_started: '尚未执行脚本' }
export function timingRows(row) {
  const timings = row.execution_timings || row.payload?.execution_timings || {}
  const labels = { queue_ms: '排队', prepare_ms: '客户端准备', reset_ms: '恢复起始状态', navigation_ms: 'AI 前置导航', script_ms: '脚本步骤（含截图与判定）', agent_ms: 'AI 执行', trace_ms: '追踪记录', upload_ms: '证据上传', evidence_ms: '本地证据保存', runner_total_ms: '设备总耗时', server_wall_ms: '平台执行墙钟时间（含回传）' }
  return Object.entries(labels).filter(([key]) => Number.isFinite(timings[key])).map(([key, label]) => ({ key, label, seconds: (timings[key] / 1000).toFixed(1) }))
}
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
