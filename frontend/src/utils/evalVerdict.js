export const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '待复核' }

const errorLabels = {
  empty_output: '判定服务异常',
  provider_error: '判定服务异常',
  missing_context: '判定输入缺失',
  insufficient_evidence: '证据不足',
}

export function evalVerdictLabel(row) {
  if (row?.verdict !== 'error') return VERDICT_LABEL[row?.verdict] || row?.verdict
  if (row.isGroup) {
    const labels = new Set(row.children.filter(turn => turn.verdict === 'error').map(evalVerdictLabel))
    if (labels.size === 1) return [...labels][0]
  }
  return errorLabels[row.verdict_dims?.judge_error] || VERDICT_LABEL.error
}
