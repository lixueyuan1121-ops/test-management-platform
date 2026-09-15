import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { DISCOVER_SCRIPT } from './probe-discover.mjs';
test('message actions retain separate icon buttons and precise stable candidates', async () => {
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  await page.setContent(`<style>button{width:28px;height:28px} .chat-actions{display:flex} .chat-user-query-shell{width:180px}</style>
   <div class="chat-user-query-shell chat-user-query-shell--actions-visible"><div>你好</div><div class="chat-actions"><span>21小时前</span><div class="icon-actions"><button type="button" aria-label="编辑" class="chat-user-query__edit-button"></button><button type="button" aria-label="复制" class="chat-copy-button"></button></div></div></div><h2>最近任务</h2><span trigger="hover" content="复制"><div class="chat-group-actions__item" data-testid="chat-msg-copy"><img width="28" height="28" alt=""></div></span>`);
  for(const relax of [false,true]) {
   const elements=await page.evaluate(DISCOVER_SCRIPT,{relax});
   for(const name of ['编辑','复制']) {
    const matches=elements.filter(e=>e.tag==='button' && e.accessibleName===name);
    assert.equal(matches.length,1);
    assert.equal(matches[0].best.by,'role');assert.equal(matches[0].best.value,'button');assert.equal(matches[0].best.name,name);assert.equal(matches[0].best.exact,true);
    assert.equal(await page.getByRole('button',{name,exact:true}).count(),1);
    assert.equal(matches[0].rect.w,28);
   }
   const copy=elements.find(e=>e.candidates.some(c=>c.by==='testid' && c.value==='chat-msg-copy'));
   assert(copy);assert.equal(copy.tooltipText,'复制');assert.equal(copy.accessibleName,'');assert.equal(copy.text,'');assert(!copy.candidates.some(c=>c.by==='text'||c.by==='role'));
   const wrapper=elements.find(e=>e.candidates.some(c=>c.value==='.chat-user-query-shell'));
   assert(wrapper);assert(!wrapper.candidates.some(c=>c.by==='text'));
   assert(!elements.flatMap(e=>e.candidates).some(c=>c.by==='css' && c.value.includes('--actions-visible')));
   assert(elements.some(e=>e.tag==='h2' && e.text==='最近任务'));
  }
 } finally {await browser.close();}
});
