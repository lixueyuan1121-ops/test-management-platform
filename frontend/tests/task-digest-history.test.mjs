import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { test } from 'node:test'

const source = readFileSync(new URL('../src/components/TaskDigestDialog.vue', import.meta.url), 'utf8')
const script = source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*\n/gm, '')
function dialog(role = 'member') {
  const calls = []
  const ctx = {
    reactive: value => value, ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    onMounted() {}, onBeforeUnmount() {}, defineEmits: () => () => {},
    defineProps: () => ({ context: { project_id: 1, task_ids: [], report_date: '2026-11-12' } }),
    useAuthStore: () => ({ user: { id: 2 }, roleIn: () => role }),
    ElMessage: { success() {}, warning() {} }, ElMessageBox: { confirm: async () => {} },
    saveTaskDigest: async data => { calls.push('save'); return data },
    publishTaskDigest: async data => { calls.push('publish'); return data },
    downloadText: () => calls.push('export'),
  }
  runInNewContext(script + '\nthis.view = { draft, tab, restore, save, publish, exportDraft };', ctx)
  return { ...ctx.view, calls }
}
const report = { id: 10, project_id: 1, author_id: 1, author_name: '同事', task_ids: [],
  title: '日报', content: '历史正文', report_date: '2026-11-11' }

test('other authors reports open in preview and cannot be saved or published', async () => {
  const view = dialog()
  await view.restore(report)
  assert.equal(view.tab.value, 'preview')
  assert.equal(view.draft.content, '历史正文')
  await view.save(); await view.publish(); view.exportDraft('md')
  assert.deepEqual(view.calls, ['export'])
})

test('opening your own report after another author restores editing', async () => {
  const view = dialog()
  await view.restore(report)
  await view.restore({ ...report, id: 11, author_id: 2 })
  assert.equal(view.tab.value, 'edit')
  await view.save()
  assert.deepEqual(view.calls, ['save'])
})

test('project guests cannot save or publish even their earlier reports', async () => {
  const view = dialog('guest')
  await view.restore({ ...report, author_id: 2 })
  await view.save(); await view.publish()
  assert.deepEqual(view.calls, [])
})

test('administrators can edit and republish other authors reports without taking ownership', async () => {
  const view = dialog('admin')
  await view.restore(report)
  assert.equal(view.tab.value, 'edit')
  view.draft.title = '管理员修改标题'
  view.draft.content = '管理员补充正文'
  await view.save(); await view.publish()
  assert.deepEqual(view.calls, ['save', 'publish'])
  assert.equal(view.draft.author_id, 1)
})
