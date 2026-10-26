import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runScript } from './step-executor.mjs';
import { validateFlow, createFlow, validateFlowReport } from './script-flow.mjs';
import { normalizeScript, fingerprint } from './execution-evidence.mjs';

const begin = {action:'flow_start',args:{version:1}};
const ref = name => ({$var:name});
function fixture() { return [begin,
  {action:'probe',target:{selector:'.completed',nth:0},args:{ready:{selector:'#loaded'},timeout_ms:50,settle_ms:10,save_as:'found'}},
  {action:'set_var',args:{name:'created',value:false}},
  {action:'click',target:{selector:'#create'},when:{var:'found',equals:false}},
  {action:'set_var',args:{name:'created',value:true},when:{var:'found',equals:false}},
  {action:'get_attribute',target:{selector:'.completed',nth:0},args:{attribute_name:'data-id',save_as:'message'}},
  {action:'get_text',target:{selector:'.completed',attribute:{name:'data-id',value:ref('message')}},args:{save_as:'before'}},
  {action:'click',target:{selector:'.like',within:{selector:'.completed',attribute:{name:'data-id',value:ref('message')}}}},
  {action:'assert_visible',target:{selector:'.liked'}},
  {action:'click',target:{selector:'#restore'},phase:'cleanup'},
  {action:'click',target:{selector:'#remove-created'},phase:'cleanup',when:{var:'created',equals:true}},
]; }
function gui(found=true, fail=false, cleanupFail=false) {
  const clicks=[];
  return {clicks,probe:async()=>({value:found}),getAttribute:async()=>({value:'message-1'}),getText:async()=>({text:'Existing answer'}),
    click:async a=>{ clicks.push(a.selector); if(cleanupFail&&a.selector==='#restore')throw Error('restore failure');return {}; },
    assertVisible:async()=>({pass:!fail,locatable:true})};
}
test('reuse existing data or create missing data; preserve scope and clean only created objects',async()=>{
  for(const found of [true,false]) {
    const g=gui(found), r=await runScript(g,fixture());
    assert.equal(r.verdict,'pass',r.reason);
    assert.equal(g.clicks.includes('#create'),!found);
    assert.equal(g.clicks.includes('#remove-created'),!found);
    assert.ok(g.clicks.includes('#restore'));
    assert.equal(r.report.length,fixture().length);
    assert.equal(r.report[5].value,'message-1');
    assert.equal(r.report[6].text,'Existing answer');
    const observed=r.report.map(x=>({...x,observation:'fixture UI execution',evidence_ref:'log',...(x.check?{check:{...x.check,pass:x.ok}}:{})}));
    validateFlowReport({script:fixture(),report:observed,verdict:'pass'},new Set(['log']));
  }
});
test('business failure remains the primary result even if cleanup also fails',async()=>{
  const g=gui(false,true,true), r=await runScript(g,fixture());
  assert.equal(r.verdict,'fail'); assert.equal(r.fail_kind,'business');
  assert.match(r.reason,/断言可见/);assert.equal(r.cleanup_errors.length,1);
  assert.ok(g.clicks.includes('#remove-created'));
});

test('automatic cleanup errors retain the original flow business failure',async()=>{
  const g=gui(true,true);
  g.restoreViewport=async()=>{throw Error('restore viewport failure')};
  g.cleanupNetworkScenarios=async()=>{throw Error('network cleanup failure')};
  const r=await runScript(g,fixture());
  assert.equal(r.fail_kind,'business');assert.match(r.reason,/断言可见/);
  assert.equal(r.cleanup_errors.length,2);
});
test('no executed assertions cannot pass and still runs cleanup',async()=>{
  const s=[begin,{action:'set_var',args:{name:'execute',value:false}},
    {action:'assert_visible',target:{selector:'#check'},when:{var:'execute',equals:true}},
    {action:'click',target:{selector:'#restore'},phase:'cleanup'}];
  const g=gui(),r=await runScript(g,s);
  assert.equal(r.verdict,'fail');assert.match(r.reason,/没有执行/);assert.ok(g.clicks.includes('#restore'));
});
test('unassigned conditional values block instead of skipping silently',async()=>{
  const s=[begin,{action:'set_var',args:{name:'no',value:false}},
    {action:'set_var',args:{name:'x',value:'answer'},when:{var:'no',equals:true}},
    {action:'assert_text',target:{selector:'#answer'},args:{expected:ref('x')}}];
  const r=await runScript(gui(),s);assert.equal(r.verdict,'fail');assert.match(r.reason,/尚未赋值/);
});
test('invalid references and control flow rejected before mutations',()=>{
  for(const s of [fixture().slice(1),[begin,{action:'fill',target:{selector:'#input'},args:{text:ref('missing')}}],
    [begin,{action:'set_var',args:{name:'x',value:'name'}},{action:'assert_visible',target:{selector:ref('x')}}]])assert.throws(()=>validateFlow(s));
  const s=fixture();s[1].args.ready=undefined;assert.throws(()=>validateFlow(s),/ready/);
});
test('values are per-run, unique IDs differ and guards affect script fingerprint',()=>{
  const a=createFlow(fixture()),b=createFlow(fixture());a.assign({name:'x',value:'a'});assert.throws(()=>b.resolve(ref('x')));
  const s=fixture(),other=structuredClone(s);other[3].when.equals=true;
  assert.notEqual(fingerprint(normalizeScript(s)),fingerprint(normalizeScript(other)));
});
test('failed main prefix skips remaining business steps and executes cleanup',async()=>{
  const s=fixture();s.splice(9,0,{action:'click',target:{selector:'#must-not-run'}});
  const g=gui(true,true),r=await runScript(g,s);
  assert.equal(r.report[9].skip_reason,'previous_failure');assert.ok(!g.clicks.includes('#must-not-run'));assert.ok(g.clicks.includes('#restore'));
  const report=r.report.map(x=>({...x,observation:'recorded fixture',evidence_ref:'log',...(x.check?{check:{...x.check,pass:x.ok}}:{}),...(!x.ok?{fail_kind:'business',check:{actual:false,expected:true,pass:false}}:{})}));
  validateFlowReport({script:s,report,verdict:'fail'},new Set(['log']));
  const forged=structuredClone(report);forged[3].skipped=false;
  assert.throws(()=>validateFlowReport({script:s,report:forged,verdict:'fail'},new Set(['log'])));
});
