import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { validatePayload } from '../../tools/skills/qalab-requirement-test/scripts/validate-contract.mjs';
import { normalizeScript } from '../../tools/skills/qalab-requirement-test/scripts/codex-observation.mjs';
import { fingerprint } from '../../tools/skills/qalab-requirement-test/scripts/validate-evidence.mjs';
import { init, record, ready, submitted, report } from '../../tools/skills/qalab-requirement-test/scripts/delivery.mjs';
const scripts=resolve('tools/skills/qalab-requirement-test/scripts');
function seal(p){for(const c of p.cases)Object.assign(c.codex_evidence,{script_sha256:fingerprint(normalizeScript(c.script)),report_sha256:fingerprint(c.report)});return p;}
function packet(fail=false){return seal({import_mode:'codex',project_id:1,external_id:'offline-codex-fixture',requirement:'首页导航',cases:[{
 title:'首页导航',steps:'打开并检查首页',expected:'首页可见',environment:'Offline fixture',scope:'navigation',executor:'codex',exec_kind:'gui',verdict:fail?'fail':'pass',finished_at:new Date().toISOString(),duration_ms:2,
 script:[{action:'assert_visible',target:{selector:'#home'}}],
 report:[{no:1,action:'assert_visible',ok:!fail,observation:'离线测试证据 中文🚀',evidence_ref:'log',...(fail?{fail_kind:'business',error:'首页未展示'}:{}),check:{actual:!fail,expected:true,pass:!fail}}],
 codex_evidence:{version:'qalab-codex-v1',tool:'offline fixture',artifacts:[{id:'log',kind:'log',reference:'fixture://log'}]}
}]});}
test('pass and business failure validate without Runner; failed prefix retains full script',()=>{
 validatePayload(packet());const p=packet(true);p.cases[0].script.push({action:'assert_text',target:{selector:'#title'},args:{expected:'首页'}});validatePayload(seal(p));
});
for(const [name,mutate] of [
 ['device binding',p=>p.runner_device_id=1],['false pass',p=>p.cases[0].report[0].check.actual=false],
 ['missing observation',p=>p.cases[0].report[0].observation=''],['missing reference',p=>p.cases[0].report[0].evidence_ref='unknown'],
 ['Runner evidence',p=>p.cases[0].execution_evidence={}],['partial pass',p=>p.cases[0].report=[]],
 ['selector block as business failure',p=>{const c=p.cases[0];c.verdict='fail';c.report[0].ok=false;c.report[0].fail_kind='selector';}],
])test('rejects '+name,()=>{const p=packet();mutate(p);assert.throws(()=>validatePayload(seal(p)));});
test('sealing and ledger preserve failed results and do not silently omit scenarios',()=>{
 const dir=mkdtempSync(join(tmpdir(),'codex-skill-'));try{
  const input=join(dir,'observed.json'),output=join(dir,'sealed.json'),m=join(dir,'manifest.json'),r=join(dir,'receipt.json');
  const p=packet(true);delete p.cases[0].codex_evidence.script_sha256;delete p.cases[0].codex_evidence.report_sha256;writeFileSync(input,JSON.stringify(p));
  const result=spawnSync(process.execPath,[join(scripts,'seal-codex.mjs'),'--input',input,'--output',output],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);const sealed=JSON.parse(readFileSync(output));assert.equal(sealed.cases[0].verdict,'fail');
  assert.notEqual(spawnSync(process.execPath,[join(scripts,'seal-codex.mjs'),'--input',output,'--output',join(dir,'resealed.json')]).status,0);
  init({import_mode:'codex',platform:'https://example.invalid',application:'fixture',requirement:'fixture',project_id:1,cases:[{id:'a',title:'首页导航',acceptance:['首页可见']},{id:'b',title:'权限',acceptance:['拒绝访问']}]},m);
  record(m,'a','tested',{packet:output});assert.throws(()=>ready(m,output));record(m,'b','blocked',{reason:'没有测试账号'});ready(m,output);
  writeFileSync(r,JSON.stringify({accepted:true,job_id:1,project_id:1,external_id:p.external_id,case_count:1}));submitted(m,output,r);
  assert.equal(report(m).cases[0].verdict,'fail');assert.equal(report(m).cases[0].status,'submitted');assert.equal(report(m).cases[1].status,'blocked');
 }finally{rmSync(dir,{recursive:true,force:true});}
});
test('Windows PowerShell 5 and 7 validate identical UTF-8 evidence', {skip:process.platform!=='win32'},()=>{
 const dir=mkdtempSync(join(tmpdir(),'codex-ps-'));try{
  const p=join(dir,'packet.json'),script=join(dir,'validate.ps1');writeFileSync(p,JSON.stringify(packet()));
  writeFileSync(script,`param($Helper,$Payload)\n. $Helper\n$p=Get-Content -LiteralPath $Payload -Raw -Encoding UTF8 | ConvertFrom-Json\nTest-ImportPayload $p\n`);
  for(const shell of ['powershell.exe','pwsh.exe']){
   const r=spawnSync(shell,['-NoProfile','-NonInteractive','-File',script,'-Helper',join(scripts,'validate-payload.ps1'),'-Payload',p],{encoding:'utf8'});assert.equal(r.status,0,r.stderr||String(r.error));
  }
 }finally{rmSync(dir,{recursive:true,force:true});}
});
