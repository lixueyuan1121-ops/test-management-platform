import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../../package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const { transformSync } = require('esbuild')
const filename = new URL('./Recorder.vue', import.meta.url)
const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename: filename.pathname })
const { code } = transformSync(compileScript(descriptor, { id: 'recorder-test' }).content, { format: 'cjs' })
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

function recorder(overrides = {}) {
  let nextSession = 0, nextTimer = 0
  const timers = new Map()
  let unmount
  const api = {
    startRecord: async () => ({ id: ++nextSession }),
    stopRecord: async () => ({ status: 'stopped', events: [{ text: 'old' }] }),
    getRecord: async () => ({ status: 'recording', events: [] }),
    ...overrides,
  }
  const module = { exports: {} }
  const imports = {
    vue: { ref: value => ({ value }), watch() {}, onMounted() {}, onUnmounted: fn => { unmount = fn } },
    'element-plus': { ElMessage: { success() {}, warning() {} } },
    '@/store/app': { useAppStore: () => ({}) },
    '@/api': api,
    '@/utils/lastProject': { pickDefaultProjectId: () => null },
    '@/components/TaskPicker.vue': { default: {} },
  }
  vm.runInNewContext(code, {
    module, exports: module.exports, require: name => imports[name],
    setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id },
    clearTimeout: id => timers.delete(id),
  })
  const state = module.exports.default.setup({}, { expose() {} })
  state.pid.value = 1; state.runner.value = 'win-01'
  return { state, timers, unmount: () => unmount(), poll() {
    const [id, fn] = timers.entries().next().value
    timers.delete(id)
    return fn()
  } }
}

test('新录制立即清空步骤、标题、前置条件和关联任务，重复点击只建一个会话', async () => {
  const pending = deferred(); let starts = 0
  const { state: s } = recorder({ startRecord: () => { starts++; return pending.promise } })
  s.steps.value = [{ text: 'old' }]; s.title.value = 'old title'
  s.precondition.value = 'old precondition'; s.taskId.value = 9; s.sessionId.value = 1
  const start = s.onStart()
  assert.equal(s.steps.value.length, 0)
  assert.equal(s.title.value, '')
  assert.equal(s.precondition.value, '')
  assert.equal(s.taskId.value, null)
  assert.equal(s.sessionId.value, null)
  await s.onStart()
  assert.equal(starts, 1)
  pending.resolve({ id: 2 }); await start
  assert.equal(s.sessionId.value, 2)
})

test('上一轮延迟返回的轮询不能覆盖新录制或停掉新轮询', async () => {
  const old = deferred()
  const h = recorder({ getRecord: id => id === 1 ? old.promise : Promise.resolve({ events: [{ text: 'new' }], status: 'recording' }) })
  const s = h.state
  await s.onStart()
  const oldPoll = h.poll()
  await s.onStop()
  await s.onStart()
  await h.poll()
  old.resolve({ events: [{ text: 'old' }], status: 'stopped' }); await oldPoll
  assert.equal(s.sessionId.value, 2)
  assert.equal(s.steps.value[0].text, 'new')
  assert.equal(s.recording.value, true)
  assert.equal(h.timers.size, 1)
})

test('停止后正在返回的轮询不能覆盖用户编辑', async () => {
  const pending = deferred(); const h = recorder({ getRecord: () => pending.promise })
  await h.state.onStart(); const poll = h.poll(); await h.state.onStop()
  h.state.steps.value = [{ text: 'edited' }]
  pending.resolve({ events: [{ text: 'stale' }], status: 'recording' }); await poll
  assert.equal(h.state.steps.value[0].text, 'edited')
  assert.equal(h.timers.size, 0)
})

test('离开页面后返回的开始请求不能重新启动轮询', async () => {
  const pending = deferred(); const h = recorder({ startRecord: () => pending.promise })
  const start = h.state.onStart(); h.unmount()
  pending.resolve({ id: 1 }); await start
  assert.equal(h.timers.size, 0)
  assert.equal(h.state.sessionId.value, null)
})
