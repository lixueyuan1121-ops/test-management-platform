import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { validatePayload } from '../../tools/skills/qalab-requirement-test/scripts/validate-contract.mjs';
import { init,record,ready,submitted,report } from '../../tools/skills/qalab-requirement-test/scripts/delivery.mjs';
import { sealFixture } from './skill_contract_fixture.mjs';
function packet(){return sealFixture({project_id:1,runner_device_id:2,external_id:'offline-test-fixture',cases:[{title:'main',exec_kind:'gui',verdict:'pass',script:[{action:'assert_text',target:{selector:'#fixture'},args:{expected:'ready'}}],report:[{action:'assert_text',ok:true,check:{actual:'ready',expected:'ready',mode:'equals'}}]}]});}
test('valid strict fixture and unexecuted draft use separate paths',()=>{const p=packet();validatePayload(p);delete p.cases[0].execution_evidence;delete p.cases[0].report;delete p.cases[0].verdict;validatePayload(p,{draft:true});assert.throws(()=>validatePayload(p));});
const bad=[
 ['missing expected',p=>delete p.cases[0].script[0].args],
 ['undefined variable',p=>p.cases[0].script[0].args={expected_from:'missing'}],
 ['target missing',p=>delete p.cases[0].script[0].target],
 ['wrong exact type',p=>Object.assign(p.cases[0].script[0].target,{has_text:'x',has_text_exact:'true'})],
 ['wrong report actual',p=>p.cases[0].report[0].check.actual='wrong'],
 ['missing IDs',p=>delete p.project_id],
 ['invalid kind',p=>p.cases[0].exec_kind='typo'],
 ['fill missing text',p=>{p.cases[0].script.unshift({action:'fill',target:{selector:'#input'}});p.cases[0].report.unshift({action:'fill',ok:true});}],
 ['press wrong key',p=>{p.cases[0].script.unshift({action:'press',args:{key:'Enter'}});p.cases[0].report.unshift({action:'press',ok:true});}],
 ['missing evidence',p=>delete p.cases[0].execution_evidence],
 ['only one run',p=>p.cases[0].execution_evidence.repeat_count=1],
 ['missing repeat hash',p=>p.cases[0].execution_evidence.repeat_report_sha256=[]],
 ['AI fallback',p=>p.cases[0].execution_evidence.mode='codex_fallback'],
 ['tampered script',p=>p.cases[0].script[0].desc='changed'],
 ['tampered report',p=>p.cases[0].report[0].duration_ms=12],
 ['failed verdict',p=>p.cases[0].verdict='fail'],
 ['partial report',p=>p.cases[0].report=[]],
 ['false check',p=>p.cases[0].report[0].check.pass=false],
 ['wrong precondition',p=>p.cases[0].precondition='use AI'],
];
for(const [name,mutate] of bad)test('rejects '+name,()=>{const p=packet();mutate(p);assert.throws(()=>validatePayload(p));});
test('dynamic capture, empty fill and exact match accepted in draft',()=>{const p=packet();p.cases[0].script=[{action:'fill',target:{selector:'#input'},args:{text:''}},{action:'get_text',target:{selector:'.card',has_text:'A',has_text_exact:true},args:{save_as:'name'}},{action:'assert_text',target:{selector:'.title'},args:{expected_from:'name'}}];validatePayload(p,{draft:true});});
test('long text reports from existing Runner retain valid prefix semantics',()=>{const p=packet(),c=p.cases[0];c.script[0].args.expected='a'.repeat(250);c.report[0].check={actual:'a'.repeat(200),expected:'a'.repeat(250),mode:'equals'};validatePayload(sealFixture(p));});
test('short contradictory result rejected even with matching fixture fingerprints',()=>{const p=packet();p.cases[0].report[0].check.actual='wrong';assert.throws(()=>validatePayload(sealFixture(p)),/不满足文本断言/);});
test('ledger prevents silent omission, changed packet, wrong destination/receipt and records all outcomes',()=>{
 const dir=mkdtempSync(join(tmpdir(),'qalab-ledger-'));try{
 const m=join(dir,'delivery.json'),f=join(dir,'verified.json'),r=join(dir,'receipt.json'),p=packet();writeFileSync(f,JSON.stringify(p));
 init({platform:'https://example.invalid',application:'fixture',requirement:'fixture',project_id:1,runner_device_id:2,cases:[{id:'a',title:'main',acceptance:['result']},{id:'b',title:'boundary',acceptance:['retry']}]},m);
 assert.throws(()=>init({},m));assert.throws(()=>record(m,'missing','blocked',{reason:'x'}));assert.throws(()=>record(m,'b','blocked',{}));
 record(m,'a','verified',{packet:f});assert.throws(()=>ready(m,f),/planned/);record(m,'b','blocked',{reason:'No fixture permissions'});
 assert.throws(()=>ready(m,f,'https://other.invalid'),/平台/);ready(m,f,'https://example.invalid');
 const receipt={accepted:true,job_id:7,project_id:1,external_id:p.external_id,case_count:1};writeFileSync(r,JSON.stringify({...receipt,external_id:'wrong'}));assert.throws(()=>submitted(m,f,r));
 writeFileSync(r,JSON.stringify(receipt));submitted(m,f,r);submitted(m,f,r);assert.deepEqual(report(m).counts,{submitted:1,blocked:1});assert.throws(()=>record(m,'a','failed',{reason:'overwrite'}));
 p.cases[0].title='changed';writeFileSync(f,JSON.stringify(p));assert.throws(()=>ready(m,f));
 }finally{rmSync(dir,{recursive:true,force:true});}
});
test('read-only preflight inspects mock local CDP and redacts URL query',async()=>{
 const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/json/version'?{Browser:'Offline fixture'}:[{type:'page',title:'Fixture',url:'https://example.invalid/app?token=never-display'}]));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try {
   const {stdout}=await promisify(execFile)(process.execPath,['tools/skills/qalab-requirement-test/scripts/preflight.mjs','--runner','tools/qalab-runner','--cdp',`http://127.0.0.1:${server.address().port}`,'--application','https://example.invalid/app']);
   assert.equal(JSON.parse(stdout).browser,'Offline fixture');assert.ok(!stdout.includes('never-display'));
 }finally{await new Promise(r=>server.close(r));}
});
