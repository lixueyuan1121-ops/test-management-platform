// Query-first offline details. Keep grouping semantics aligned with eval_report_details.py.
const ENGINES = { namiwork: '纳米Work', workbuddy: 'WorkBuddy', qwork: 'QWork' }
const STATUS = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败', cancelled: '已取消', missing: '记录缺失' }
const VERDICT = { pass: '通过', fail: '不通过', error: '判定出错' }
const REVIEW = { confirmed: '人工确认', false_positive: '人工标记：误报', false_negative: '人工标记：漏报' }
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(k => [k, stable(value[k])])) : value
const engineOf = row => row.target_engine || 'namiwork'
const payloadOf = row => {
  try {
    const p = typeof row.payload === 'string' ? JSON.parse(row.payload) : row.payload
    return p && !Array.isArray(p) && typeof p === 'object' ? p : {}
  } catch { return {} }
}

export function groupReportQueries(groupedRuns, manifest = null) {
  const rows = (Array.isArray(groupedRuns) ? groupedRuns : []).flatMap(r => r.isGroup ? r.children || [] : [r])
    .map(r => ({ ...r, payload: payloadOf(r) }))
  const ids = new Set(rows.map(r => String(r.run_id)))
  for (const planned of manifest?.planned_runs || []) {
    if (!ids.has(String(planned.run_id))) rows.push({ ...planned, payload: payloadOf(planned),
      status: 'missing', reason: '计划执行记录缺失' })
  }
  const groups = new Map()
  for (const row of rows.filter(r => r.status !== 'cancelled')) {
    const p = row.payload, qid = p.eval_query_id || row.eval_query_id, prompt = p.prompt || ''
    const identity = qid ? String(qid) : prompt ? 'prompt' : `run:${row.run_id}`
    const context = p.source_conversation_group ?? p.conversation_group
    const key = JSON.stringify(stable([row.batch_id ?? null, identity, prompt, p.attachments || [], context || '', p.turn_index || 0, p.expected ?? null]))
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(row)
  }
  return [...groups.values()]
}

const duration = ms => {
  if (!ms) return '—'
  const seconds = Math.round(ms / 1000), m = Math.floor(seconds / 60), s = seconds % 60
  return m ? `${m}m${s ? `${s}s` : ''}` : `${seconds}s`
}
const link = (url, label) => /^https?:\/\//i.test(String(url || ''))
  ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label}</a>` : ''

function rowHtml(row, opts) {
  const p = row.payload, engine = engineOf(row), d = p.dialog_options || {}
  const config = p.configuration_label || [d.model, d.chatMode, d.thinkingDepth && `思考深度：${d.thinkingDepth}`].filter(Boolean).join(' · ') || '沿用客户端配置'
  const badges = [p.compare_group && `${p.compare_group} 组`, p.configuration_index && `配置 ${p.configuration_index}`,
    (p.trial_count > 1 || p.trial_index > 1) && `第 ${p.trial_index || 1} 次执行`].filter(Boolean)
  const verdicts = { ...VERDICT, ...opts.verdictLabel }, statuses = { ...STATUS, ...opts.statusLabel }
  const verdict = row.verdict, status = row.status
  const label = ['failed', 'missing'].includes(status) ? statuses[status] : verdicts[verdict] || verdict || statuses[status] || status || '—'
  const tone = status === 'failed' || verdict === 'fail' ? 'bad' : verdict === 'pass' ? 'good' : 'neutral'
  const review = ({ ...REVIEW, ...opts.reviewLabel })[row.review_mark]
  const state = `<span class="qd-result qd-${tone}">${esc(label)}</span>`
    + (verdict && status !== 'judged' ? `<small>${esc(statuses[status] || status || '—')}</small>` : '')
    + (review ? `<small>${esc(review)}</small>` : '')
  let cost = row.bean_cost == null || String(row.bean_cost).trim() === '' ? '—' : String(row.bean_cost)
  const unit = { namiwork: '算力豆', workbuddy: '积分', qwork: '积分' }[engine]
  if (/^[+-]?\d+(?:\.\d+)?$/.test(cost.trim()) && unit) cost += ` ${unit}`
  const reason = ['failed', 'missing'].includes(status) ? row.reason : row.verdict_reason
  return `<tr data-run-id="${esc(row.run_id)}"><td><strong>${esc(ENGINES[engine] || engine)}</strong><small>RUN-${esc(row.run_id)}</small></td>
    <td class="qd-config">${esc(config)}${badges.length ? `<small>${esc(badges.join(' · '))}</small>` : ''}</td><td>${state}</td>
    <td class="qd-number"><strong>${esc(row.score ?? '—')}</strong></td><td class="qd-number">${esc(duration(row.duration_ms))}</td><td>${esc(cost)}</td>
    <td>${[link(row.share_link, '对话'), link(row.artifact_share_link, '产物')].filter(Boolean).join('<br>') || '—'}</td>
    <td>${reason ? `<details><summary>查看原因</summary><div class="qd-reason">${esc(reason)}</div></details>` : '—'}</td></tr>`
}

export function reportDetails(groupedRuns, opts = {}) {
  // Planned records have no batch_id; use the selected batch, not an arbitrary sibling batch.
  const manifest = opts.manifest ? { ...opts.manifest, planned_runs: (opts.manifest.planned_runs || []).map(r => ({ ...r, batch_id: opts.batchId })) } : null
  const groups = groupReportQueries(groupedRuns, manifest), rows = groups.flat()
  const products = [...new Set(rows.map(engineOf))]
  const articles = groups.map((group, index) => {
    const first = group[0], p = first.payload, qid = p.eval_query_id || first.eval_query_id
    const title = p.title || first.title || `Query ${qid || index + 1}`
    const dim = p.dimension ?? first.dimension
    const meta = [dim ? (opts.dimLabel?.(dim) || dim) : '未标注', `${group.length} 条执行`]
    if (p.source_conversation_group || p.conversation_group) meta.push(`会话第 ${(p.turn_index || 0) + 1} 轮`)
    if (qid) meta.push(`Query #${qid}`)
    const prompt = p.prompt ? `<details class="qd-prompt"><summary>查看完整 query</summary><div>${esc(p.prompt)}</div></details>` : ''
    const files = (p.attachments || []).map((a, i) => typeof a === 'object' && a ? a.name || a.filename || `附件 ${i + 1}` : String(a))
    const ordered = [...group].sort((a, b) => products.indexOf(engineOf(a)) - products.indexOf(engineOf(b))
      || String(a.payload.compare_group || '').localeCompare(String(b.payload.compare_group || ''))
      || (a.payload.configuration_index || 0) - (b.payload.configuration_index || 0)
      || (a.payload.trial_index || 1) - (b.payload.trial_index || 1) || a.run_id - b.run_id)
    return `<article class="qd-query"><header><h3><span class="qd-index">${String(index + 1).padStart(2, '0')}</span>${esc(title)}</h3>
      <div class="qd-note">${esc(meta.join(' · '))}</div>${prompt}${files.length ? `<div class="qd-note">附件：${esc(files.join('、'))}</div>` : ''}</header>
      <div class="qd-scroll" tabindex="0" role="region" aria-label="各产品执行结果对比"><table class="qd-table">
      <thead><tr><th>产品 / 执行</th><th>模型 / 配置</th><th>结果</th><th>评分 / 5</th><th>执行耗时</th><th>消耗</th><th>链接</th><th>判定 / 异常原因</th></tr></thead>
      <tbody>${ordered.map(r => rowHtml(r, opts)).join('')}</tbody></table></div></article>`
  }).join('')
  return `<section class="query-details" data-layout="query-v1"><h2>逐条执行明细</h2>
    <p class="qd-note">${groups.length} 个 query · ${rows.length} 条执行 · ${products.length} 个产品。相同 query 的产品结果相邻展示。</p>
    <p class="qd-note">耗时为执行总耗时；消耗按各产品单位展示，不跨产品换算。配置为下发时的设置；未采集到的数据记为 —。</p>
    ${articles || '<p class="empty">暂无执行记录</p>'}</section>`
}

export const REPORT_DETAIL_STYLE = `
.query-details { margin-top:28px; min-width:0; }
.query-details .qd-note { font-size:12px; color:#728196; overflow-wrap:anywhere; margin:6px 0; }
.query-details .qd-query { border:1px solid #dce5ed; border-radius:10px; margin:18px 0; overflow:hidden; }
.query-details .qd-query header { padding:16px 18px; background:#f6f9fc; border:0; }
.query-details h3 { display:flex; gap:10px; align-items:baseline; margin:0 0 6px; font-size:15px; color:#20334a; overflow-wrap:anywhere; }
.query-details .qd-index { color:#178875; font-size:13px; font-variant-numeric:tabular-nums; flex-shrink:0; }
.query-details .qd-scroll { overflow-x:auto; }
.query-details .qd-table { width:100%; min-width:960px; table-layout:fixed; border-collapse:collapse; margin:0; font-size:13px; }
.query-details .qd-table th, .query-details .qd-table td { text-align:left; padding:12px 10px; border:0; border-bottom:1px solid #e8edf3; vertical-align:top; overflow-wrap:anywhere; }
.query-details .qd-table th { background:#fff; color:#728196; font-size:12px; font-weight:500; }
.query-details .qd-table th:nth-child(1) { width:12%; }
.query-details .qd-table th:nth-child(2) { width:24%; }
.query-details .qd-table th:nth-child(3) { width:12%; }
.query-details .qd-table th:nth-child(4) { width:7%; }
.query-details .qd-table th:nth-child(5) { width:9%; }
.query-details .qd-table th:nth-child(6) { width:9%; }
.query-details .qd-table th:nth-child(7) { width:6%; }
.query-details .qd-table th:nth-child(8) { width:21%; }
.query-details .qd-table tbody tr:nth-child(even) { background:#fbfcfe; }
.query-details .qd-table tr:last-child td { border-bottom:0; }
.query-details small { display:block; margin-top:4px; font-size:11px; color:#728196; }
.query-details .qd-number { font-variant-numeric:tabular-nums; white-space:nowrap; }
.query-details .qd-result { display:inline-block; border-radius:4px; padding:1px 7px; font-size:12px; }
.query-details .qd-good { color:#087952; background:#e7f6ee; }
.query-details .qd-bad { color:#bc3543; background:#fcecee; }
.query-details .qd-neutral { color:#75632e; background:#f7f2e6; }
.query-details summary { cursor:pointer; color:#247567; font-size:12px; }
.query-details .qd-reason, .query-details .qd-prompt div { white-space:pre-wrap; overflow-wrap:anywhere; margin-top:8px; }
.query-details .qd-prompt { margin-top:8px; }
.query-details a { color:#247567; text-decoration:none; }
.query-details a:hover { text-decoration:underline; }
@media(max-width:640px) {
.query-details .qd-scroll::before { content:"左右滑动查看完整对比"; display:block; position:sticky; left:0; padding:8px 10px; font-size:11px; color:#728196; background:#fff; }
.query-details .qd-table th:first-child, .query-details .qd-table td:first-child { position:sticky; left:0; background:#fff; box-shadow:1px 0 #e8edf3; }
.query-details .qd-table tbody tr:nth-child(even) td:first-child { background:#fbfcfe; }
}
@media print { .query-details .qd-scroll { overflow:visible; } .query-details .qd-table { min-width:0; } }
`
