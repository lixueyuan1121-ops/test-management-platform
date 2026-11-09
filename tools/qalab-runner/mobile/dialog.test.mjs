import test from 'node:test';
import assert from 'node:assert/strict';
import {runDialog} from './dialog.mjs';

function phone({lost=false, stale=false}={}) {
  let home=true, draft='', question='', answer='', turn=0, generating=false;
  let saved, reads=0; const questions=[];
  return {
    platform:'android',
    async findAll({value}) {
      if (value==='home') return home?['home']:[];
      if (value==='input') return ['input'];
      if (value==='send') return draft?['send']:[];
      if (value==='back') return home?[]:['back'];
      if (value==='stop') return generating?['stop']:[];
      if (value==='finished') return generating||!answer?[]:['finished'];
      if (value==='answer') return answer?['answer']:[];
      if (value==='question') return home?[]:questions.map((_,i)=>'q'+i);
      if (value==='historyItem') return home&&saved?['historyItem']:[];
      return [];
    },
    async click(el) {
      if (el==='send') {
        home=false; question=draft; questions.push(question); turn++; draft='';
        saved=true; generating=question.includes('逐条');
        if (!stale) answer='本轮回复 '+turn;
      } else if(el==='stop') generating=false;
      else if(el==='back') {home=true; question='';}
      else if(el==='historyItem') {home=false; question=this.last; if(lost) answer='错误的回复';}
    },
    async type(id,s) { this.last=draft=s; },
    async clear() { draft=''; }, async enabled(){return true;}, async displayed(){return true;},
    async text(id){ reads++; return /^q[0-9]+$/.test(id)?questions[+id.slice(1)]:id==='answer'?answer:draft; }
  };
}
const run=(scenario,d)=>runDialog({scenario,driver:d,marker:'QA-fixture',resolveLocator:async k=>({using:'id',value:k}),artifact:async()=>'/evidence/shot.png',timeoutMs:45,pollMs:1});
test('same script covers four native dialog scenarios with observed evidence',async()=>{
 for(const scenario of ['send','followup','stop','history']){
  const r=await run(scenario,phone()); assert.equal(r.verdict,'pass',r.reason);
  assert.ok(r.steps.some(s=>s.check&&s.shot));
 }
});
test('unchanged or missing answer cannot pass; history loss is reported',async()=>{
 assert.notEqual((await run('send',phone({stale:true}))).verdict,'pass');
 assert.notEqual((await run('history',phone({lost:true}))).verdict,'pass');
});

const executeScript = (script, driver = phone()) => runDialog({
  driver, scenario:'script', script, marker:'QA-variant',
  resolveLocator:async key=>({using:'id',value:key}), artifact:async()=>'/evidence/step.png',
  timeoutMs:80, pollMs:1,
});
test('AI steps preserve multiline text and emoji and can verify a draft without sending',async()=>{
  const input=' 版本 2.0\nhello 👋 ';
  const r=await executeScript([{action:'new_chat'},{action:'compose',text:input},{action:'assert_draft',text:input},
    {action:'clear_draft'},{action:'assert_send_unavailable'}]);
  assert.equal(r.verdict,'pass',r.reason);
  assert.equal(r.steps.find(s=>s.action==='assert_draft').check.actual,input);
});
test('a composed three-turn case is not reduced to the followup template',async()=>{
  const script=[{action:'new_chat'}];
  for (let i=1;i<=3;i++) script.push({action:'compose',text:'{{marker}} '+i},{action:'send'},
    {action:'assert_user_message',turn:i},{action:'wait_reply'});
  script.push({action:'assert_reply_contains',text:'回复 3'});
  const res=await executeScript(script);
  assert.equal(res.verdict,'pass',res.reason);
  assert.equal(res.steps.filter(s=>s.action==='send').length,3);
  assert.equal(res.steps.find(s=>s.action==='assert_user_message').check.actual,'QA-variant 1');
});
test('mismatching data fails the exact assertion and unsupported steps act on nothing',async()=>{
  const driver=phone();
  const type=driver.type.bind(driver);
  driver.type=(id,text)=>type(id,text.toUpperCase());
  const res=await executeScript([{action:'new_chat'},{action:'compose',text:'a'},{action:'assert_draft',text:'a'}],driver);
  assert.equal(res.verdict,'fail'); assert.equal(res.steps.at(-1).check.actual,'A');
  const bad=await executeScript([{action:'new_chat'},{action:'delete_history'},{action:'assert_send_unavailable'}]);
  assert.equal(bad.verdict,'blocked'); assert.equal(bad.steps.length,0);
});
test('an invalid draft action sequence is rejected before operating a device',async()=>{
  const res=await executeScript([{action:'new_chat'},{action:'compose',text:'a'},{action:'assert_send_unavailable'}]);
  assert.equal(res.verdict,'blocked'); assert.deepEqual(res.steps,[]);
});
