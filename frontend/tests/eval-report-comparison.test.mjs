import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { groupReportQueries } from '../src/utils/evalReportDetails.js'
import { buildEvalReportHtml } from '../src/utils/evalReportHtml.js'
import { groupEvalRuns } from '../src/utils/evalRunGroups.js'

const runs = JSON.parse(readFileSync(new URL('./fixtures/eval-report-comparison.json', import.meta.url)))
const html = rows => buildEvalReportHtml({ groupedRuns: rows, task: { name: '多产品综合评价', last_batch_id: 'b1' } })

test('产品分段排列的输入按 query 配对，独立执行保留，多轮组行不重复计数', () => {
  const groups = groupReportQueries(groupEvalRuns(runs))
  assert.equal(groups.length, 4)
  assert.deepEqual(groups.map(g => g.map(r => r.run_id)), [[101, 201, 105, 301], [102, 202], [103, 203], [104, 204]])
  const out = html(groupEvalRuns(runs))
  assert.match(out, /4 个 query · 10 条执行 · 3 个产品/)
  assert.equal((out.match(/data-run-id=/g) || []).length, 10)
  assert(out.indexOf('data-run-id="201"') < out.indexOf('data-run-id="102"'))
  for (const text of ['纳米Work', 'WorkBuddy', 'QWork', '第 2 次执行', '第 2 轮', '8m8s', '0 算力豆', '0.89 积分', 'API Error', '执行失败', '待判定']) assert(out.includes(text), text)
})

test('标题相同但 query、正文、附件、上下文或批次不同不错误合并；缺失 id 的旧数据不按标题猜测', () => {
  for (const changed of [
    { payload: { ...runs[0].payload, eval_query_id: 99 } },
    { payload: { ...runs[0].payload, prompt: '不同输入' } },
    { payload: { ...runs[0].payload, attachments: [{ name: 'input.csv', url: '/different' }] } },
    { payload: { ...runs[0].payload, source_conversation_group: 'other' } },
    { payload: { ...runs[0].payload, turn_index: 1 } }, { batch_id: 'other' },
  ]) assert.equal(groupReportQueries([runs[0], { ...runs[0], run_id: 999, ...changed }]).length, 2)
  assert.equal(groupReportQueries([{ run_id: 1, payload: { title: '同名' } }, { run_id: 2, payload: { title: '同名' } }]).length, 2)
})

test('A/B、配置与重复试验完整显示；仅合并 query 不平均数据', () => {
  const rows = ['A', 'B'].flatMap(compare_group => [1, 2].map(i => ({ ...runs[0], run_id: `${compare_group}${i}`,
    payload: { ...runs[0].payload, compare_group, configuration_id: `c${i}`, configuration_index: i, configuration_label: `模型配置${i}` } })))
  const out = html(rows)
  assert.match(out, /1 个 query · 4 条执行/)
  assert.equal((out.match(/data-run-id=/g) || []).length, 4)
  for (const s of ['A 组', 'B 组', '配置 2', '模型配置2']) assert(out.includes(s))
})

test('缺失计划记录标记为缺失，不能被当作成功或者漏掉该产品', () => {
  const out = buildEvalReportHtml({ task: { last_batch_id: 'newest', summary_batch_id: 'b1' }, groupedRuns: [runs[0]],
    experiment: { manifest: { planned_runs: [runs[0], runs[4]] } } })
  assert.match(out, /1 个 query · 2 条执行 · 2 个产品/)
  assert.match(out, /记录缺失/)
  assert.match(out, /计划执行记录缺失/)
  assert.match(out, /批次 b1/)
  assert(!out.includes('批次 newest'))
})

test('所有明细文本转义，拒绝脚本链接，空 payload 兼容', () => {
  const out = html([{ ...runs[0], share_link: 'javascript:alert(1)', artifact_share_link: 'https://example.com/?a="x"',
    verdict_reason: '<img src=x onerror=alert(1)>', payload: { ...runs[0].payload, title: '<script>bad()</script>',
      dialog_options: { model: '<script>model()</script>' } } }, { run_id: 2, payload: '[]' }])
  assert(!out.includes('<script>'))
  assert(!out.includes('<img src=x'))
  assert(!out.includes('javascript:'))
  assert.match(out, /&lt;script&gt;/)
  assert.match(out, /rel="noopener noreferrer"/)
  assert.match(out, /暂无执行记录|Query 2/)
})
