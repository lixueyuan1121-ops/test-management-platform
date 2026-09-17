import test from 'node:test'
import assert from 'node:assert/strict'
import { parseModelNames, dialogCombinationCount, fmtDialogOptions } from '../src/utils/dialogOptions.js'
import { groupEvalRuns } from '../src/utils/evalRunGroups.js'

test('模型支持换行和中英文分隔符，大小写去重，保留模型内的空格及斜杠', () => {
  assert.deepEqual(parseModelNames(' GLM-5.3\nglm-5.3， 豆包（seed-2.1）, GPT 5.4; Claude/Sonnet；\n'),
    ['GLM-5.3', '豆包（seed-2.1）', 'GPT 5.4', 'Claude/Sonnet'])
  assert.deepEqual(parseModelNames(' \n，;'), [])
})

test('计数与回填摘要保留含逗号的模式；空维度仍执行一次', () => {
  const matrix = { chatMode: ['边想边做', '先规划，再执行'], model: ['M1', 'M2', 'M3'], thinkingDepth: ['高', '标准'] }
  assert.equal(dialogCombinationCount(matrix), 12)
  assert.equal(dialogCombinationCount({}), 1)
  assert.match(fmtDialogOptions({ matrix, model: 'WB' }), /12 种组合.*先规划，再执行.*WorkBuddy：WB/)
  assert.equal(fmtDialogOptions({ model: 'M1', compareB: { model: 'M2' } }), 'M1 vs M2')
})

test('多轮结果按配置和独立执行次数隔离', () => {
  const runs = []
  for (const config of ['c1', 'c2']) for (const trial of [1, 2]) for (const turn of [0, 1]) {
    runs.push({ run_id: runs.length + 1, batch_id: 'b', target_engine: 'namiwork', payload: {
      conversation_group: 'g', configuration_id: config, configuration_label: config,
      trial_index: trial, turn_index: turn,
    } })
  }
  const groups = groupEvalRuns(runs)
  assert.equal(groups.length, 4)
  assert.equal(new Set(groups.map(g => g.run_id)).size, 4)
  for (const group of groups) {
    assert.equal(group.target_engine, 'namiwork')
    assert.equal(group.children.length, 2)
    assert.equal(new Set(group.children.map(c => c.payload.configuration_id)).size, 1)
    assert.equal(new Set(group.children.map(c => c.payload.trial_index)).size, 1)
  }
})
