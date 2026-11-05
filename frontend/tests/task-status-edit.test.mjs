import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { test } from 'node:test'

// Execute the actual view handlers with an API boundary double, without a browser.
const source = readFileSync(new URL('../src/views/Tasks.vue', import.meta.url), 'utf8')
const openEdit = source.slice(source.indexOf('function openEdit(row)'), source.indexOf('// ---- 任务详情'))
const submit = source.slice(source.indexOf('async function submit()'), source.indexOf('async function changeStatus('))
function editor() {
  const ctx = {
    form: {}, dialog: {}, LEDGER_DEFAULTS: { conclusion: '' }, pid: { value: 1 },
    ElMessage: { success() {}, warning() {} }, load: async () => {},
    updateTask: async (id, payload) => { ctx.saved = payload; return {} },
  }
  runInNewContext(`${openEdit}\n${submit}`, ctx)
  ctx.openEdit({ id: 1, title: '首次访问推荐弹窗', status: 'pending', assigned_to: 1, assigned_date: '2026-11-04' })
  return ctx
}

test('saving notes from an older editor does not resend the old task status', async () => {
  const ctx = editor()
  ctx.form.conclusion = '已上预发，等待素材'
  await ctx.submit()
  assert.equal(ctx.saved.conclusion, '已上预发，等待素材')
  assert.equal(Object.hasOwn(ctx.saved, 'status'), false)
})

test('an intentional status edit includes the original status for conflict detection', async () => {
  const ctx = editor()
  ctx.form.status = 'ready_online'
  await ctx.submit()
  assert.equal(ctx.saved.status, 'ready_online')
  assert.equal(ctx.saved.expected_status, 'pending')
})
