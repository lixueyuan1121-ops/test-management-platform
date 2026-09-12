// 对话测评多轮会话分组:同批次内同 conversation_group 的各轮在同一对话内连续发送,是一次完整会话。
// EvalResults(结果页)与 EvalTasks(任务详情)共用本模块,保持两处 UI 口径一致。
//
// 规则:
// - 分组键 = batch_id + conversation_group:**必须带批次**——结果页混多批展示,同一道
//   多轮题反复执行时各批 run 的组名相同,不带批次会把 5 批 ×3 轮错并成「多轮×15」(实测踩过)。
// - 组内 ≥2 条才算多轮:聚成组行(children=各轮按轮次升序,树形展开)。
// - 单轮、以及「带组 ID 但组内实际只有一条」的 run 原样平铺,不做任何多轮标识
//   (生成侧常整组产 query,但可能只下发/执行了其中一轮——不能把它当多轮展示)。
// - 组行聚合:分享链接取任一轮抓到的(同一对话本就同一链接);执行状态取最落后轮;
//   判定任一 fail 即 fail、全 pass 才 pass。
// - matchFilter:行级筛选;多轮组任一轮命中即保留整组(展开可见明细)。
export function groupEvalRuns(rows, matchFilter = () => true) {
  const keyOf = (r) => {
    const g = r.payload?.conversation_group
    return g ? JSON.stringify([r.batch_id || '', r.target_engine || '', r.payload?.compare_group || '', r.payload?.trial_index || 1, g]) : null
  }
  const byGroup = new Map()
  for (const r of rows) {
    const k = keyOf(r)
    if (k) { if (!byGroup.has(k)) byGroup.set(k, []); byGroup.get(k).push(r) }
  }
  const out = []
  const seen = new Set()
  for (const r of rows) {
    const k = keyOf(r)
    if (!k || (byGroup.get(k) || []).length <= 1) {
      if (matchFilter(r)) out.push(r)
      continue
    }
    if (seen.has(k)) continue
    seen.add(k)
    const turns = [...byGroup.get(k)].sort((a, b) => (a.payload?.turn_index ?? 0) - (b.payload?.turn_index ?? 0))
    if (!turns.some(matchFilter)) continue
    out.push(makeGroupRow(k, turns))
  }
  return out
}

function makeGroupRow(key, turns) {
  const share = turns.map((t) => t.share_link).find((u) => /^https?:\/\//i.test(u || '')) || null
  const st = turns.some((t) => t.status === 'running') ? 'running'
    : turns.some((t) => t.status === 'pending') ? 'pending'
    : turns.some((t) => t.status === 'failed') ? 'failed'
    : turns.every((t) => t.status === 'judged') ? 'judged' : 'done'
  const vs = turns.map((t) => t.verdict)
  const verdict = vs.includes('fail') ? 'fail'
    : vs.includes('error') ? 'error'
    : (vs.length && vs.every((v) => v === 'pass')) ? 'pass' : null
  return {
    run_id: `grp-${key}`,
    isGroup: true,
    conversation_group: turns[0].payload?.conversation_group,
    batch_id: turns[0].batch_id,
    // _inGroup 标记「确实处于多轮组内」的轮:子行据此显示「第X轮」;
    // 单独一条带组 ID 的 run 没有该标记,不会被误展示成多轮(浅拷贝避免污染原始行)
    children: turns.map((t) => ({ ...t, _inGroup: true })),
    payload: turns[0].payload,
    dimension: turns[0].dimension,
    eval_query_id: turns[0].eval_query_id,
    status: st,
    verdict,
    is_abnormal: turns.some((t) => t.is_abnormal),
    share_link: share,
    verdict_dims: null,
    verdict_reason: null,
  }
}

// 同一批次、同一产品、同一题目内配对，避免多产品 A/B 相互覆盖。
export function compareEvalRuns(runs) {
  if (!runs.some((r) => r.payload?.compare_group)) return null
  const byQuery = new Map()
  const scores = { A: [], B: [] }
  for (const r of runs) {
    const g = r.payload?.compare_group
    if (g !== 'A' && g !== 'B') continue
    const k = JSON.stringify([r.batch_id || '', r.target_engine || '',
      r.eval_query_id ?? r.payload?.eval_query_id ?? r.run_id, r.payload?.trial_index || 1])
    if (!byQuery.has(k)) byQuery.set(k, {})
    byQuery.get(k)[g] = r
    if (r.score != null && scores[g]) scores[g].push(r.score)
  }
  let aWin = 0, bWin = 0, tie = 0, undecided = 0
  for (const pair of byQuery.values()) {
    const va = pair.A?.verdict, vb = pair.B?.verdict
    if (va === 'pass' && vb === 'fail') aWin++
    else if (va === 'fail' && vb === 'pass') bWin++
    else if ((va === 'pass' || va === 'fail') && va === vb) tie++
    else undecided++
  }
  const avg = (arr) => arr.length ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1) : null
  return { aWin, bWin, tie, undecided, total: byQuery.size, aAvg: avg(scores.A), bAvg: avg(scores.B) }
}
