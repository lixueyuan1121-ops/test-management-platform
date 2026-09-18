import { test } from 'node:test'
import assert from 'node:assert/strict'
import { loadEvalQueryPages } from '../src/utils/evalQueryPages.js'

const rows = Array.from({ length: 618 }, (_, i) => ({ id: 618 - i, dimension: null }))

test('完整加载超过 500 条用例，较早勾选的 18 条未标注用例仍可见', async () => {
  const requests = []
  const loaded = await loadEvalQueryPages(async params => {
    requests.push(params)
    return rows.filter(q => !params.before_id || q.id < params.before_id).slice(0, params.limit)
  }, { project_id: 1, eval_task_id: 10 })
  assert.deepEqual(loaded, rows)
  assert.equal(loaded.filter(q => q.id <= 18).length, 18)
  assert.deepEqual(requests.map(p => p.before_id), [undefined, 419, 219, 19])
  assert(requests.every(p => p.project_id === 1 && p.eval_task_id === 10))
})

test('满页时继续检查下一页，空数据正常结束', async () => {
  let calls = 0
  assert.deepEqual(await loadEvalQueryPages(async () => ++calls === 1 ? rows.slice(0, 200) : [], {}), rows.slice(0, 200))
  assert.equal(calls, 2)
  assert.deepEqual(await loadEvalQueryPages(async () => [], {}), [])
})

test('中途请求失败不把部分列表当成全部，旧后端忽略游标不会无限请求', async () => {
  let calls = 0
  await assert.rejects(loadEvalQueryPages(async () => {
    if (++calls === 2) throw new Error('Network Error')
    return rows.slice(0, 200)
  }, {}), /Network Error/)
  calls = 0
  await assert.rejects(loadEvalQueryPages(async () => { calls++; return rows.slice(0, 200) }, {}), /分页未正确返回/)
  assert.equal(calls, 2)
  await assert.rejects(loadEvalQueryPages(async () => null, {}), /格式异常/)
})
