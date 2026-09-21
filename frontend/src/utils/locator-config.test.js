import test from 'node:test'
import assert from 'node:assert/strict'
import { configuredCandidates } from './locator-config.js'
import { normalizeCandidate, candidateIdentity, orderCandidates } from './selector-ranking.js'
import { createElementStatusMatcher } from './probe-batch.js'

test('single and all three text combinations persist their conditions', () => {
  for (const by of ['testid','xpath','css']) {
    const base = {mode:'single', by, value:' target ', text:' Save ', nth:null, keepFallback:false}
    assert.deepEqual(configuredCandidates(base), [{by,value:'target',primary:true}])
    assert.deepEqual(configuredCandidates({...base, mode:'combined',nth:1}), [{by,value:'target',primary:true,has_text:'Save',nth:1}])
    assert.deepEqual(configuredCandidates({...base, mode:'combined',text:' '}), [])
  }
})
test('explicit primary is first, fallbacks are opt-in and lose prior primary', () => {
  const config={mode:'single',by:'xpath',value:'//button',nth:null,keepFallback:false}
  const old=[{by:'testid',value:'old',primary:true}]
  assert.equal(configuredCandidates(config, old).length,1)
  const cands=configuredCandidates({...config,keepFallback:true},old)
  assert.equal(orderCandidates(cands)[0].by,'xpath')
  assert.equal(cands[1].primary,false)
})
test('candidate identity and validation retain filters and occurrence', () => {
  const c={by:'css',value:'.button',has_text:'Save',nth:1,primary:true}
  assert.deepEqual(normalizeCandidate(c),c)
  assert.notEqual(candidateIdentity(c),candidateIdentity({...c,has_text:'Cancel'}))
  assert.notEqual(candidateIdentity(c),candidateIdentity({...c,nth:0}))
  assert.equal(normalizeCandidate({...c,nth:-1}),null)
  assert.equal(normalizeCandidate({...c,has_text:4}),null)
})
test('filtered CSS must not mark an unrelated snapshot element as already added', () => {
  const el={text:'Cancel',_frameMatch:'shell',candidates:[{by:'css',value:'.button.unique'}]}
  const rows=[{key:'save',frame:'shell',candidates:[{by:'css',value:'.button',has_text:'Save'}]}]
  assert.equal(createElementStatusMatcher(rows,[el])(el).type,'new')
})
