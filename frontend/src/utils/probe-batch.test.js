import { test } from 'node:test'
import assert from 'node:assert/strict'
import { elementStatus, suggestKey } from './probe-batch.js'
const stable = { by: 'testid', value: 'save' }
const row = { key: 'saveButton', frame: 'vm', candidates: [stable] }
const el = { element_ref: 'e1', _frameMatch: 'vm', best: stable, candidates: [stable] }
test('registered stable candidate matches only one element in the same frame', () => {
  assert.deepEqual(elementStatus(el, [row], [el]), { type: 'exists', key: 'saveButton' })
  assert.equal(elementStatus({ ...el, _frameMatch: 'shell' }, [row], [el]).type, 'new')
  assert.equal(elementStatus({ ...el, _frameMatch: 'shell' }, [row], [el], { vm: 'shell' }).type, 'exists')
  assert.equal(elementStatus(el, [row], [el, { ...el, element_ref: 'e2' }]).type, 'new')
  assert.equal(elementStatus(el, [row, { ...row, key: 'another' }], [el]).type, 'new')
  assert.equal(elementStatus(el, [{ ...row, candidates: [{ ...stable, disabled: true }] }], [el]).type, 'new')
})
test('new best candidate offers update; key suggestions are valid and reserve batch duplicates', () => {
  assert.equal(elementStatus({ ...el, best: { by: 'testid', value: 'new-save' } }, [row], [el]).type, 'update')
  const reserved = new Set(['page_save'])
  assert.equal(suggestKey(el, 'page', reserved), 'page_save_2')
  assert.equal(suggestKey(el, 'page', reserved), 'page_save_3')
  for (const text of ['保存', '123', 'x'.repeat(200)]) assert.match(suggestKey({ text }), /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/)
})
