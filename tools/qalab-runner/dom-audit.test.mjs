import test from 'node:test';
import assert from 'node:assert/strict';
import {auditTarget,runDomAudit} from './dom-audit.mjs';
test('navigation conforms to runtime target contract',()=>{assert.deepEqual(auditTarget({by:'testid',value:'nav-home'}),{selector:'[data-testid="nav-home"]'});assert.throws(()=>auditTarget({by:'js',value:'x'}));});
test('failed page stays uncovered and cancellation stops before clicks',async()=>{
 const calls=[];const core={pressEscapePage:async()=>{},click:async t=>{assert.equal(typeof t.selector,'string');assert(!('state' in t));calls.push(t);throw Error('missing')},waitFor:async()=>{},probe:async()=>{throw Error('should not probe')}};
 const params={version:1,pages:[{id:'home',label:'首页',nav:[{by:'testid',value:'nav-home'}],ready:{by:'testid',value:'home-main'}}]};
 await assert.rejects(()=>runDomAudit(core,params),/首页.*missing/);assert.equal(calls.length,1);
 await assert.rejects(()=>runDomAudit(core,params,async()=>({cancel_requested:true})),/终止/);assert.equal(calls.length,1);
});
