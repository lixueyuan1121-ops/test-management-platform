export const TASK_STATUS = {
  unassigned: { label: '待分配', type: 'info' },
  pending: { label: '待跟进', type: 'info' },
  testing: { label: '测试中', type: 'warning' },
  waiting_fix: { label: '待开发修复', type: 'warning' },
  regressing: { label: '回归中', type: 'primary' },
  at_risk: { label: '有风险', type: 'danger' },
  paused: { label: '暂停', type: 'info' },
  ready_online: { label: '测试完成', type: 'primary' },
  blocked: { label: '阻塞', type: 'danger' },
  online: { label: '已上线', type: 'success' },
  closed: { label: '已关闭', type: 'info' },
}
export const RISK_META = {
  red: { label: '红 · 高风险', type: 'danger' },
  yellow: { label: '黄 · 需关注', type: 'warning' },
  green: { label: '绿 · 正常', type: 'success' },
}
export const LEDGER_DEFAULTS = {
  risk_level: null, target_version: '', progress_note: '',
  expected_completion: null, next_action: '', action_owner: '', feedback_due_at: null,
  affects_release: null, conclusion: '',
}
export const localDate = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
// A one-day selection is the daily follow-up view, including unfinished carryovers.
// Wider ranges deliberately filter by assignment date.
export function taskDateQuery(range) {
  if (!Array.isArray(range) || range.length !== 2 || !range[0] || !range[1]) return {}
  return range[0] === range[1]
    ? { date: range[0] }
    : { start_date: range[0], end_date: range[1] }
}
export const taskOwners = row => row.assigned_to_name || '—'
export const hasRisk = row => ['red', 'yellow'].includes(row.risk_level) || ['blocked', 'at_risk'].includes(row.status) || row.affects_release === true
export function filterLedger(tasks, filters) {
  const keyword = filters.keyword.trim().toLocaleLowerCase()
  return tasks.filter(row =>
    [row.title, row.description, row.assigned_to_name, row.developer, row.progress_note, row.next_action, row.action_owner]
      .filter(Boolean).join(' ').toLocaleLowerCase().includes(keyword) &&
    (!filters.status.length || filters.status.includes(row.status)) &&
    (!filters.owner.length || filters.owner.some(id => (row.assigned_to_ids || [row.assigned_to]).includes(id))) &&
    (!filters.priority.length || filters.priority.includes(row.priority)) &&
    (!filters.version.length || filters.version.some(version => version === '__unset' ? !row.target_version : row.target_version === version)) &&
    (!filters.risk || (filters.risk === '__unset' ? !row.risk_level : row.risk_level === filters.risk)) &&
    (filters.impact === '' || (filters.impact === '__unset' ? row.affects_release == null : row.affects_release === (filters.impact === 'yes'))) &&
    (!filters.riskOnly || hasRisk(row)))
}
export function downloadText(content, filename, type = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename.replace(/[\\/:*?"<>|]/g, '_')
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
