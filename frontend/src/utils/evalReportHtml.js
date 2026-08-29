// 测评任务报告导出:把「综合评价 + A/B 胜率 + 逐条结果明细」拼成一个自包含单 HTML 文件,
// 可离线双击打开、直接发给他人。纯函数、零外部依赖——所有 label 映射 / 维度名 /
// 对话选项文本 / 均分 / 导出时间都由调用方(EvalTasks.vue)传入,既便于独立测试,也避免
// 与页面的枚举口径产生第二份真相(与 utils/evalRunGroups 的分组结果同源)。
//
// 安全:run/task 的所有文本字段一律 HTML 转义(esc);唯一例外是 task.summary_html——
// 它是 AI 产出、已在服务端按白名单消毒的 HTML 片段
// (backend/app/api/eval_task.py::_sanitize_html),原样嵌入以保留标题/表格排版。
// 会话链接仅放行 http(s),其余(javascript: 等)一律降级为纯文本「—」。

const isHttp = (u) => /^https?:\/\//i.test(String(u || ''))
const REVIEW_ICON = { confirmed: '✓', false_positive: '误', false_negative: '漏' }

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ))
}

// 评分(1-5,判定引擎给):组行取各轮均分(1 位小数),真实行取 score;无评分 null。
// 口径与 EvalTasks.vue::rowScore 一致。
function rowScore(row) {
  if (!row.isGroup) return row.score ?? null
  const ss = (row.children || []).map((t) => t.score).filter((s) => s != null)
  return ss.length ? +(ss.reduce((a, b) => a + b, 0) / ss.length).toFixed(1) : null
}
const scoreCls = (s) => (s >= 4 ? 'score-hi' : s >= 3 ? 'score-mid' : 'score-lo')

// A/B 对比批次才有:配对胜率 + A/B 均分(数据来自页面 compareInfo computed)
function compareSection(c) {
  if (!c) return ''
  const avg = (c.aAvg || c.bAvg)
    ? `<span class="cmp-seg">均分 <span class="cmp-a">A ${esc(c.aAvg ?? '—')}</span> / <span class="cmp-b">B ${esc(c.bAvg ?? '—')}</span></span>`
    : ''
  return `
  <section class="cmp">
    <span class="cmp-seg cmp-a">A 胜 ${esc(c.aWin)}</span>
    <span class="cmp-seg cmp-b">B 胜 ${esc(c.bWin)}</span>
    <span class="cmp-seg">平 ${esc(c.tie)}</span>
    <span class="cmp-seg cmp-und">未决 ${esc(c.undecided)}</span>
    ${avg}
    <span class="cmp-total">共 ${esc(c.total)} 对</span>
  </section>`
}

// 单行:组行(isGroup)/其子轮行(isChild)/普通行,列口径与页面详情表一致
function rowTr(row, { compare, dimLabel, statusLabel, verdictLabel, reviewLabel, isChild }) {
  const cells = []
  cells.push(`<td class="c-num">${row.isGroup ? '会话组' : esc(row.run_id)}</td>`)
  if (compare) {
    const g = row.payload?.compare_group
    cells.push(`<td class="c-grp">${g ? `<span class="grp grp-${esc(g)}">${esc(g)}</span>` : ''}</td>`)
  }
  let prefix = ''
  if (row.isGroup) prefix = `<span class="turn">多轮 ×${esc(row.children?.length ?? 0)}</span> `
  else if (isChild) prefix = `<span class="turn">第 ${esc((row.payload?.turn_index ?? 0) + 1)} 轮</span> `
  const title = row.payload?.title || row.payload?.prompt || `query#${row.eval_query_id ?? ''}`
  cells.push(`<td class="c-title${isChild ? ' child' : ''}">${prefix}${esc(title)}</td>`)
  cells.push(`<td class="c-dim">${row.dimension ? esc(dimLabel(row.dimension)) : '—'}</td>`)
  cells.push(`<td class="c-st"><span class="st st-${esc(row.status)}">${esc(statusLabel[row.status] || row.status || '—')}</span></td>`)
  // 判定 + 人工复核标记(只读)
  const review = (row.review_mark && reviewLabel[row.review_mark])
    ? ` <span class="rf rf-${esc(row.review_mark)}" title="${esc(reviewLabel[row.review_mark])}">${REVIEW_ICON[row.review_mark] || ''}</span>`
    : ''
  cells.push(`<td class="c-vd">${row.verdict ? `<span class="vd vd-${esc(row.verdict)}">${esc(verdictLabel[row.verdict] || row.verdict)}</span>` : '—'}${review}</td>`)
  // 评分
  const sc = rowScore(row)
  cells.push(`<td class="c-score">${sc != null ? `<span class="score ${scoreCls(sc)}">${esc(sc)}</span>` : '—'}</td>`)
  // 判定理由(执行失败时显示失败原因)
  const reason = row.status === 'failed'
    ? `<span class="fail-reason">执行失败：${esc(row.reason || '（未回写原因）')}</span>`
    : (row.verdict_reason ? esc(row.verdict_reason) : '—')
  cells.push(`<td class="c-reason">${reason}</td>`)
  cells.push(`<td class="c-link">${isHttp(row.share_link) ? `<a href="${esc(row.share_link)}" target="_blank" rel="noopener noreferrer">打开</a>` : '—'}</td>`)
  const cls = row.isGroup ? 'r-group' : (isChild ? 'r-child' : '')
  return `<tr${cls ? ` class="${cls}"` : ''}>${cells.join('')}</tr>`
}

function detailSection(groupedRuns, opts) {
  const rows = Array.isArray(groupedRuns) ? groupedRuns : []
  const trs = []
  for (const row of rows) {
    trs.push(rowTr(row, { ...opts, isChild: false }))
    if (row.isGroup && Array.isArray(row.children)) {
      for (const child of row.children) trs.push(rowTr(child, { ...opts, isChild: true }))
    }
  }
  const heads = ['#']
  if (opts.compare) heads.push('组')
  heads.push('用例', '维度', '执行', '判定', '评分', '判定理由', '会话')
  const headHtml = heads.map((h) => `<th>${h}</th>`).join('')
  const bodyHtml = trs.length ? trs.join('\n') : `<tr><td class="empty" colspan="${heads.length}">暂无执行记录</td></tr>`
  return `
  <section>
    <h2>逐条结果明细<span class="count">共 ${esc(rows.length)} 项</span></h2>
    <table class="detail-table">
      <thead><tr>${headHtml}</tr></thead>
      <tbody>${bodyHtml}</tbody>
    </table>
  </section>`
}

function summarySection(task) {
  const at = task.summary_at
    ? `${esc(String(task.summary_at).replace('T', ' ').slice(0, 19))}${task.summary_provider ? ' · ' + esc(task.summary_provider) : ''}`
    : ''
  let body
  if (task.summary_html) {
    body = `<div class="summary-html">${task.summary_html}</div>` // 原样嵌入,已服务端消毒
  } else {
    const msg = task.summary_status === 'running' ? '综合评价生成中…'
      : task.summary_status === 'failed' ? '综合评价生成失败,可回平台重新生成'
        : '尚未生成综合评价'
    body = `<div class="empty">${esc(msg)}</div>`
  }
  return `
  <section>
    <h2>AI 综合评价${at ? `<span class="count">${at}</span>` : ''}</h2>
    ${body}
  </section>`
}

export function buildEvalReportHtml({
  task = {},
  groupedRuns = [],
  compareInfo = null,
  dimLabel = (k) => k,
  statusLabel = {},
  verdictLabel = {},
  taskStatusLabel = {},
  reviewLabel = {},
  dialogOptionsText = '',
  avgScore = '',
  exportedAt = '',
} = {}) {
  const compare = !!compareInfo
  const metaBits = [`<span class="badge">${esc(taskStatusLabel[task.status] || task.status || '—')}</span>`]
  if (task.last_batch_id) metaBits.push(`<span class="mono">批次 ${esc(task.last_batch_id)}</span>`)
  if (avgScore) metaBits.push(`<span class="avg-score">均分 ${esc(avgScore)}/5</span>`)
  if (dialogOptionsText) metaBits.push(`<span class="opts">${esc(dialogOptionsText)}</span>`)
  if (exportedAt) metaBits.push(`<span class="muted">导出于 ${esc(exportedAt)}</span>`)
  const desc = task.description ? `<p class="desc">${esc(task.description)}</p>` : ''

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>测评报告 · ${esc(task.name || '')}</title>
<style>${STYLE}</style>
</head>
<body>
<div class="report">
  <header>
    <h1>${esc(task.name || '测评报告')}</h1>
    <div class="meta">${metaBits.join('')}</div>
    ${desc}
  </header>
  ${compareSection(compareInfo)}
  ${summarySection(task)}
  ${detailSection(groupedRuns, { compare, dimLabel, statusLabel, verdictLabel, reviewLabel })}
  <footer>本报告由测评管理平台导出${exportedAt ? ' · ' + esc(exportedAt) : ''}</footer>
</div>
</body>
</html>`
}

const STYLE = `
* { box-sizing: border-box; }
body { margin: 0; background: #f5f7fa; color: #34495e;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px; line-height: 1.7; }
.report { max-width: 980px; margin: 24px auto; background: #fff; border-radius: 12px;
  box-shadow: 0 2px 16px rgba(31,45,61,.08); padding: 28px 32px; }
header { border-bottom: 2px solid #f0f3f6; padding-bottom: 16px; }
h1 { font-size: 22px; color: #1f2d3d; margin: 0 0 12px; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 13px; }
.badge { background: #00b386; color: #fff; border-radius: 4px; padding: 2px 10px; font-size: 12px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; color: #5a6b7b; font-size: 12px; }
.avg-score { font-weight: 700; color: #d98b00; font-size: 13px; }
.opts { color: #5a6b7b; font-size: 12px; }
.muted { color: #9aa5b1; font-size: 12px; }
.desc { margin: 12px 0 0; color: #5a6b7b; }
section { margin-top: 24px; }
h2 { font-size: 16px; color: #1f2d3d; border-left: 3px solid #00b386; padding-left: 10px;
  margin: 0 0 12px; display: flex; align-items: baseline; gap: 10px; }
h2 .count { font-size: 12px; color: #9aa5b1; font-weight: 400; }
.empty { color: #9aa5b1; padding: 20px; text-align: center; background: #fafbfc; border-radius: 8px; }
.cmp { display: flex; align-items: center; gap: 12px; padding: 12px 16px; margin-top: 20px;
  background: #f6f9fc; border: 1px solid #e4ecf4; border-radius: 8px; flex-wrap: wrap; }
.cmp-seg { font-weight: 700; font-size: 14px; color: #5a6b7b; }
.cmp-a { color: #2f7dd1; } .cmp-b { color: #d98b00; } .cmp-und { color: #a0a8b3; }
.cmp-total { margin-left: auto; font-size: 12px; color: #8a94a6; font-weight: 400; }
.summary-html { line-height: 1.8; color: #34495e; }
.summary-html h2 { font-size: 16px; margin: 16px 0 8px; color: #1f2d3d; border-left: 3px solid #00b386; padding-left: 8px; }
.summary-html h3 { font-size: 14px; margin: 12px 0 6px; color: #34495e; }
.summary-html table { border-collapse: collapse; width: 100%; margin: 10px 0; }
.summary-html th, .summary-html td { border: 1px solid #dfe6ec; padding: 7px 10px; text-align: left; font-size: 13px; }
.summary-html th { background: #f3f8f7; color: #1f2d3d; }
.summary-html ul, .summary-html ol { padding-left: 22px; margin: 8px 0; }
.summary-html blockquote { border-left: 3px solid #dfe6ec; margin: 8px 0; padding: 4px 12px; color: #7d8a9b; background: #f8fafc; }
.summary-html code { background: #eef2f6; border-radius: 3px; padding: 1px 5px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.detail-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.detail-table th, .detail-table td { border: 1px solid #e4e7ed; padding: 7px 9px; text-align: left; vertical-align: top; }
.detail-table th { background: #f3f8f7; color: #1f2d3d; white-space: nowrap; }
.detail-table tbody tr:nth-child(even) { background: #fafcfd; }
.c-num { text-align: center; color: #8a94a6; font-family: 'JetBrains Mono', monospace; font-size: 12px; white-space: nowrap; }
.c-score { text-align: center; }
.c-reason { white-space: pre-wrap; color: #5a6b7b; }
.c-title.child { padding-left: 22px; }
.r-group { background: #f2f8f6 !important; font-weight: 600; }
.turn { display: inline-block; background: #eef2f6; color: #5a6b7b; border-radius: 3px; padding: 0 6px; font-size: 12px; font-weight: 400; }
.grp { display: inline-block; border-radius: 3px; padding: 0 7px; color: #fff; font-size: 12px; font-weight: 700; }
.grp-A { background: #2f7dd1; } .grp-B { background: #d98b00; }
.st, .vd { display: inline-block; border-radius: 3px; padding: 1px 7px; font-size: 12px; white-space: nowrap; }
.st-judged { background: #e7f7ef; color: #12805c; }
.st-done { background: #eaf1fb; color: #2f6fd0; }
.st-failed { background: #fdeaea; color: #d64545; }
.st-pending { background: #eef0f3; color: #8a94a6; }
.st-running, .st-judging { background: #fdf3e6; color: #cc8a1a; }
.vd-pass { background: #e7f7ef; color: #12805c; }
.vd-fail { background: #fdeaea; color: #d64545; }
.vd-error { background: #eef0f3; color: #8a94a6; }
.score { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 14px; }
.score-hi { color: #00b386; } .score-mid { color: #d98b00; } .score-lo { color: #e5565f; }
.rf { display: inline-block; margin-left: 4px; font-size: 11px; font-weight: 700; min-width: 16px; height: 16px; line-height: 16px; text-align: center; border-radius: 8px; padding: 0 3px; }
.rf-confirmed { background: #e7f7f1; color: #00b386; }
.rf-false_positive { background: #fdf3e3; color: #d98b00; }
.rf-false_negative { background: #fdeaea; color: #e5565f; }
.fail-reason { color: #e5565f; }
.c-link a { color: #00926e; text-decoration: none; }
.c-link a:hover { text-decoration: underline; }
footer { margin-top: 28px; padding-top: 16px; border-top: 1px solid #f0f3f6; text-align: center; color: #b4bcc6; font-size: 12px; }
@media print { body { background: #fff; } .report { box-shadow: none; margin: 0; max-width: none; } }
`
