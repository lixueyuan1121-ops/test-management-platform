export function evalSummaryState(task = {}, submitting = false) {
  const progress = task.summary_progress || {}
  const status = submitting ? 'queued' : task.summary_status
  const busy = status === 'queued' || status === 'running'
  const stage = progress.stage
  const part = progress.phase === 'analyzing'
    ? `分段分析：已完成 ${progress.chunk_completed || 0}/${progress.chunk_total} 段，正在处理第 ${progress.current_chunk || 1} 段。`
    : progress.phase === 'merging'
      ? `分段分析已完成，正在合并第 ${progress.merge_level} 层摘要（${progress.merge_current}/${progress.merge_total}）。`
      : progress.phase === 'final' ? `已完成 ${progress.chunk_total} 段分析，正在生成最终综合评价。` : ''
  let label = '未生成', type = 'info', hint = '执行、判定完成后，可生成综合评价。'
  if (status === 'queued') {
    label = submitting ? '正在提交' : '排队中'
    hint = submitting ? '正在提交生成任务…' : '任务已提交，等待生成。可以关闭此窗口，稍后回来查看。'
  } else if (status === 'running') {
    label = stage === 'retry_wait' ? '自动重试中' : '生成中'
    type = 'warning'
    hint = stage === 'retry_wait'
      ? `模型返回 API Error，等待 ${progress.retry_delay_seconds || 5} 秒后进行第 ${progress.retry_count || 1} 次重试。`
      : progress.output_chars > 0 ? `正在整理综合评价，已收到 ${progress.output_chars.toLocaleString()} 字符。`
        : '正在等待模型返回内容，任务仍在后台执行。'
    if (part) hint = part + ' ' + hint
    if (progress.reused_chunks) hint += ` 已复用 ${progress.reused_chunks} 段摘要。`
  } else if (status === 'done') {
    label = '已生成'; type = 'success'; hint = '综合评价已生成，可以查看或导出报告。'
  } else if (status === 'failed') {
    label = '生成失败'; type = 'danger'; hint = '本次生成已结束，可点击「重新生成综合评价」重试。'
    if (progress.chunk_completed) hint += ` 已保存 ${progress.chunk_completed}/${progress.chunk_total} 段分析；资料与模型配置未变化时会复用。`
  }
  const seconds = progress.elapsed_seconds
  const elapsed = seconds == null ? '' : seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
  return { status, busy, label, type, hint, elapsed, retries: progress.retry_count || 0,
    error: status === 'failed' ? progress.error || '生成已中断或失败，请重新生成。' : '',
    lastError: busy ? progress.last_error : '' }
}
