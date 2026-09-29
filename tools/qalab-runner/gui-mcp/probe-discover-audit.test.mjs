import test from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {DISCOVER_SCRIPT} from './probe-discover.mjs';
test('audit verifies CSS and XPath, groups list templates and isolates shadow roots',async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  await page.setContent(`<button class="unique-save">保存</button><button>没有属性</button><ul><li><button data-testid="row-open">甲</button></li><li><button data-testid="row-open">乙</button></li></ul><section><button class="same-icon">设置</button></section><footer><button class="same-icon">返回</button></footer><section><article class="result-card"><button>跨组甲</button></article></section><section><article class="result-card"><button>跨组乙</button></article></section><div id="shadow-a"></div><div id="shadow-b"></div>`);
  await page.evaluate(()=>{for(const id of ['shadow-a','shadow-b'])document.getElementById(id).attachShadow({mode:'open'}).innerHTML='<button class="shadow-save">保存</button>';});
  const rows=await page.evaluate(DISCOVER_SCRIPT,{audit:true});
  assert(rows.find(e=>e.text==='保存'&&e.verified.some(c=>c.value==='.unique-save')));
  assert(rows.find(e=>e.text==='没有属性').verified.some(c=>c.by==='xpath'));
  for(const text of ['甲','乙']){const e=rows.find(e=>e.text===text&&e.tag==='button');assert.equal(e.collections[0].value,'row-open');assert.equal(e.collections[0].count,2);assert.equal(e.verified.length,0);}
  for(const text of ['跨组甲','跨组乙']){const e=rows.find(e=>e.text===text && e.tag==='button');assert.equal(e.collections.length,1);assert.equal(e.collections[0].count,2);}
  for(const text of ['设置','返回'])assert.equal(rows.find(e=>e.text===text).collections.length,0);
  assert(rows.filter(e=>e.candidates.some(c=>c.value==='.shadow-save')).every(e=>e.verified.length===0));
  const scoped=await page.evaluate(DISCOVER_SCRIPT,{audit:true,auditRoot:'footer'});
  assert.equal(scoped.length,1);assert.equal(scoped[0].text,'返回');
 }finally{await browser.close();}
});
