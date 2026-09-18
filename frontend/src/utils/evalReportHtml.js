// 测评任务报告导出:把「综合评价 + A/B 胜率 + 逐条结果明细」拼成一个自包含单 HTML 文件,
// 可离线双击打开、直接发给他人。纯函数、零外部依赖——所有 label 映射 / 维度名 /
// 对话选项文本 / 均分 / 导出时间都由调用方(EvalTasks.vue)传入,既便于独立测试,也避免
// 与页面的枚举口径产生第二份真相(与 utils/evalRunGroups 的分组结果同源)。
//
// 安全:run/task 的所有文本字段一律 HTML 转义(esc);唯一例外是 task.summary_html——
// 它是 AI 产出、已在服务端按白名单消毒的 HTML 片段
// (backend/app/api/eval_task.py::_sanitize_html),原样嵌入以保留标题/表格排版。
// 会话链接仅放行 http(s),其余(javascript: 等)一律降级为纯文本「—」。

import { reportDetails, REPORT_DETAIL_STYLE } from './evalReportDetails.js'

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ))
}

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
  experiment = null,
  dimLabel = (k) => k,
  statusLabel = {},
  verdictLabel = {},
  taskStatusLabel = {},
  reviewLabel = {},
  dialogOptionsText = '',
  avgScore = '',
  exportedAt = '',
} = {}) {
  const batchId = task.summary_batch_id || task.last_batch_id
  const metaBits = [`<span class="badge">${esc(taskStatusLabel[task.status] || task.status || '—')}</span>`]
  if (batchId) metaBits.push(`<span class="mono">批次 ${esc(batchId)}</span>`)
  if (avgScore) metaBits.push(`<span class="avg-score">均分 ${esc(avgScore)}/5</span>`)
  if (dialogOptionsText) metaBits.push(`<span class="opts">${esc(dialogOptionsText)}</span>`)
  if (exportedAt) metaBits.push(`<span class="muted">导出于 ${esc(exportedAt)}</span>`)
  const metrics = experiment?.metrics
  const overview = metrics ? `<section><h2>覆盖率与稳定性</h2><p>有效样本通过率 ${esc(metrics.pass_rate ?? '—')}% · 判定覆盖率 ${esc(metrics.coverage_rate ?? '—')}% · 已确认成功占比 ${esc(metrics.confirmed_success_rate ?? '—')}%</p><p>计划 ${esc(metrics.total)} 条 · 配置错误 ${esc(metrics.config_errors)} · 执行错误 ${esc(metrics.execution_errors)} · 判定错误或证据不足 ${esc(metrics.judge_errors)}</p>${experiment.manifest ? `<p>每题独立执行 ${esc(experiment.manifest.trial_count)} 次 · 题库版本 ${esc(experiment.manifest.dataset_hash)}</p>` : ''}${experiment?.trial_metrics?.by_engine_variant?.map(m => `<p>${esc(m.engine)} / ${esc(m.configuration_label || m.variant)}：题目等权成功率 ${esc(m.success_rate)}%，判定覆盖率 ${esc(m.coverage_rate)}%，均分 ${esc(m.mean_score ?? '—')}</p>`).join('') || ''}</section>` : ''
  const desc = task.description ? `<p class="desc">${esc(task.description)}</p>` : ''

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>测评报告 · ${esc(task.name || '')}</title>
<style>${STYLE}
${REPORT_DETAIL_STYLE}</style>
</head>
<body>
<div class="report">
  <header>
    <h1>${esc(task.name || '测评报告')}</h1>
    <div class="meta">${metaBits.join('')}</div>
    ${desc}
  </header>
  ${compareSection(compareInfo)}
  ${overview}
${summarySection(task)}
  ${reportDetails(groupedRuns, { dimLabel, statusLabel, verdictLabel, reviewLabel, manifest: experiment?.manifest, batchId })}
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
.report { max-width: 1200px; margin: 24px auto; background: #fff; border-radius: 12px;
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
footer { margin-top: 28px; padding-top: 16px; border-top: 1px solid #f0f3f6; text-align: center; color: #b4bcc6; font-size: 12px; }
@media (max-width: 640px) { .report { margin: 8px; padding: 18px 12px; } h2 { flex-wrap: wrap; } }
@media print { body { background: #fff; } .report { box-shadow: none; margin: 0; max-width: none; } }
`
