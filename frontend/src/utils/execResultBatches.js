// 重试链保留明细，汇总只计最后一次执行。
export function execResultBatches(rows) {
  const map = new Map()
  const supersededIds = new Set(rows.map((r) => r.retry_of).filter(Boolean))
  for (const row of rows) {
    const r = { ...row, _superseded: supersededIds.has(row.run_id ?? row.id) }
    const key = r.batch_id || '__none__'
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(r)
  }
  const out = []
  for (const [key, list] of map) {
    const eff = list.filter((r) => !r._superseded)
    const passed = eff.filter((r) => r.verdict === 'pass').length
    const failed = eff.filter((r) => r.verdict === 'fail').length
    const blocked = eff.filter((r) => r.verdict === 'blocked' || r.status === 'blocked').length
    const flaky = eff.filter((r) => r.flaky).length
    const total = eff.length
    const durSum = list.reduce((n, r) => n + (r.duration_ms || 0), 0)
    const time = list.reduce((t, r) => {
      const s = r.updated_at || r.created_at || ''
      return s > t ? s : t
    }, '')
    const fnDenom = passed + failed
    out.push({
      id: key,
      label: key === '__none__' ? '(未分批 · 历史记录)' : `批次 ${key}`,
      rows: list,
      total, passed, failed, blocked, flaky,
      rate: fnDenom ? Math.round((passed / fnDenom) * 100) : 0,
      runner: [...new Set(list.map((r) => r.runner).filter(Boolean))].join(', ') || '—',
      durationText: durSum ? (durSum / 1000).toFixed(1) + 's' : '—',
      time,
    })
  }
  out.sort((a, b) => (a.time < b.time ? 1 : -1))
  return out
}
