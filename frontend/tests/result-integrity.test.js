import test from 'node:test'
import assert from 'node:assert/strict'
import { execResultBatches } from '../src/utils/execResultBatches.js'
import { compareEvalRuns, groupEvalRuns } from '../src/utils/evalRunGroups.js'

test('失败后重试通过只计一次，保留旧明细及全部耗时', () => {
  const rows = [
    { run_id: 2, retry_of: 1, batch_id: 'b', verdict: 'pass', flaky: true, duration_ms: 2000 },
    { run_id: 1, batch_id: 'b', verdict: 'fail', duration_ms: 1000 },
  ]
  const [batch] = execResultBatches(rows)
  assert.deepEqual([batch.total, batch.passed, batch.failed, batch.flaky, batch.rate], [1, 1, 0, 1, 100])
  assert.equal(batch.rows.length, 2)
  assert.equal(batch.rows[1]._superseded, true)
  assert.equal(batch.durationText, '3.0s')
  assert.equal(rows[1]._superseded, undefined)
})

test('连续重试只统计最后一次失败，兼容旧 id 字段', () => {
  const [batch] = execResultBatches([
    { id: 1, verdict: 'fail' }, { id: 2, retry_of: 1, verdict: 'fail' },
    { id: 3, retry_of: 2, verdict: 'fail' },
  ])
  assert.equal(batch.total, 1)
  assert.equal(batch.failed, 1)
})

const run = (engine, group, verdict, score, batch = 'b') => ({
  batch_id: batch, target_engine: engine, eval_query_id: 1,
  payload: { compare_group: group }, verdict, score,
})

test('两产品同题 A/B 独立配对，不覆盖胜负', () => {
  const result = compareEvalRuns([
    run('namiwork', 'A', 'pass', 5), run('namiwork', 'B', 'fail', 1),
    run('workbuddy', 'A', 'fail', 2), run('workbuddy', 'B', 'pass', 4),
  ])
  assert.deepEqual(result, { aWin: 1, bWin: 1, tie: 0, undecided: 0, total: 2, aAvg: '3.5', bAvg: '2.5' })
})

test('不同批次不配对，缺侧或判定 error 计未决，零分参与平均', () => {
  const result = compareEvalRuns([
    run('namiwork', 'A', 'pass', 0, 'old'), run('namiwork', 'B', 'pass', 0),
    run('workbuddy', 'A', 'error', null), run('workbuddy', 'B', 'fail', 3),
  ])
  assert.equal(result.total, 3)
  assert.equal(result.undecided, 3)
  assert.equal(result.aAvg, '0.0')
  assert.equal(compareEvalRuns([]), null)
})


test('独立执行按 trial 配对，不覆盖同题前几次结果', () => {
  const rows = []
  for (const trial of [1, 2, 3]) {
    rows.push({ ...run('namiwork', 'A', trial === 1 ? 'fail' : 'pass', 4), payload: { compare_group: 'A', trial_index: trial } })
    rows.push({ ...run('namiwork', 'B', 'fail', 2), payload: { compare_group: 'B', trial_index: trial } })
  }
  const result = compareEvalRuns(rows)
  assert.deepEqual([result.total, result.aWin, result.tie], [3, 2, 1])
})

test('同名多轮会话在不同 trial 中保持隔离', () => {
  const rows = [1, 2].flatMap(trial => [0, 1].map(turn => ({ run_id: trial * 10 + turn,
    batch_id: 'b', target_engine: 'namiwork', status: 'done',
    payload: { conversation_group: 'g', turn_index: turn, trial_index: trial } })))
  const groups = groupEvalRuns(rows)
  assert.equal(groups.length, 2)
  assert(groups.every(group => group.children.length === 2))
})
