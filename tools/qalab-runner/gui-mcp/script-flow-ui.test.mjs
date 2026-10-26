import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { createAutomationRuntime } from './runtime-loader.mjs';
import { runScript } from '../step-executor.mjs';
let browser;
before(async()=>{ browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_TEST_EXECUTABLE}); });
after(async()=>{await browser?.close();});
const begin={action:'flow_start',args:{version:1}};
const row={selector:'.answer.complete',frame:'shell',nth:0};
const identity={selector:'.answer',frame:'shell',attribute:{name:'data-id',value:{$var:'message'}}};
const script=[begin,
  {action:'probe',target:row,args:{ready:{selector:'#loaded',frame:'shell'},timeout_ms:100,settle_ms:30,save_as:'found'}},
  {action:'click',target:{selector:'#create',frame:'shell'},when:{var:'found',equals:false}},
  {action:'get_attribute',target:row,args:{attribute_name:'data-id',save_as:'message'}},
  {action:'get_text',target:row,args:{save_as:'answer'}},
  {action:'click',target:{selector:'.like',within:identity}},
  {action:'assert_visible',target:{selector:'.liked',within:identity}},
  {action:'assert_text',target:{selector:'.body',within:identity},args:{expected:'回答内容'}},
  {action:'click',target:{selector:'.like',within:identity},phase:'cleanup'},
];
test('real browser: existing and empty lists, dynamic IDs and duplicate labels, cleanup',async()=>{
  for(const existing of [true,false]) {
    const p=await browser.newPage();
    try {
      await p.setContent(`<div id="loaded">Loaded</div><main></main><button id="create">创建</button><script>
        window.creates=0;window.add=()=>{const a=document.createElement('article');a.className='answer complete';a.dataset.id='message-1';a.innerHTML='<span class="body">回答内容</span><button class="like">赞</button>';a.querySelector('button').onclick=e=>e.target.classList.toggle('liked');document.querySelector('main').append(a)};
        document.querySelector('#create').onclick=()=>{creates++;add()};
      </script>`);
      if(existing)await p.evaluate(()=>add());
      // Windows headless rendering may deliver animation frames slowly. Keep the
      // normal actionability checks, with the same wait budget as GUI execution.
      const runtime=createAutomationRuntime({page:p,registry:{},timeout:15000,pollMs:5});
      const r=await runScript(runtime,script);
      assert.equal(r.verdict,'pass',r.reason);assert.equal(await p.evaluate(()=>creates),existing?0:1);
      assert.equal(await p.locator('.liked').count(),0);
      assert.equal(r.report[2].skipped,existing?true:undefined);
    } finally {await p.close();}
  }
});
test('probe never treats missing readiness, invalid selector, missing parent or ambiguity as no data',async()=>{
  const p=await browser.newPage();
  try {
    await p.setContent('<div id="loaded">Loaded</div><button>A</button><button>B</button>');
    const rt=createAutomationRuntime({page:p,registry:{},timeout:50,pollMs:5});
    const args={ready:{selector:'#loaded',frame:'shell'},timeout_ms:30,settle_ms:10,frame:'shell'};
    for(const target of [{selector:'['},{selector:'button'},{selector:'button',within:{selector:'#missing'}}])await assert.rejects(rt.probe({...args,...target}));
    await assert.rejects(rt.probe({...args,selector:'#absent',ready:{selector:'#not-loaded',frame:'shell'}}));
    assert.equal((await rt.probe({...args,selector:'#absent'})).value,false);
  } finally {await p.close();}
});
test('bounded absent probe waits for late rendering; malformed attributes cannot change locator syntax',async()=>{
  const p=await browser.newPage();
  try {
    await p.setContent('<div id="loaded">Loaded</div>');
    const rt=createAutomationRuntime({page:p,registry:{},timeout:100,pollMs:5});
    await p.evaluate(()=>setTimeout(()=>{const b=document.createElement('button');b.id='late';b.textContent='Ready';document.body.append(b)},20));
    assert.equal((await rt.probe({selector:'#late',frame:'shell',ready:{selector:'#loaded',frame:'shell'},timeout_ms:100,settle_ms:60})).value,true);
    await assert.rejects(rt.getAttribute({selector:'#late',attribute_name:'onclick'}));
    await assert.rejects(rt.getAttribute({selector:'#late',attribute_name:'data-missing'}));
  }finally{await p.close();}
});
test('captured IDs with quotes, backslashes and selector-like text remain literal',async()=>{
  const p=await browser.newPage();
  try {
    await p.setContent('<button id="chosen" onclick="this.textContent=\'clicked\'">target</button><button id="other">other</button>');
    const id='回答"\\特殊],button';
    await p.locator('#chosen').evaluate((el,v)=>el.setAttribute('data-id',v),id);
    const rt=createAutomationRuntime({page:p,registry:{},timeout:2000,pollMs:5});
    await rt.click({selector:'button',frame:'shell',attribute:{name:'data-id',value:id}});
    assert.equal(await p.locator('#chosen').textContent(),'clicked');
    assert.equal(await p.locator('#other').textContent(),'other');
  }finally{await p.close();}
});
