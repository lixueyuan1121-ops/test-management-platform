import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {selectDesktopOverlay,createDesktopOverlay} from './desktop-overlay.mjs';
import {createAutomationRuntime} from './runtime-loader.mjs';
const main='https://tenant.work.n.cn/';
const target={id:'overlay',type:'page',url:main+'desktop-overlay.html'};
test('only an exact same-origin desktop portal is eligible',()=>{
 assert.equal(selectDesktopOverlay([target],main),target);
 for(const bad of [{...target,type:'iframe'},{...target,url:main+'?namiwork_surface=desktop_voice'},
  {...target,url:'https://other.work.n.cn/desktop-overlay.html'}, {...target,url:'https://tenant.work.n.cn.evil.test/desktop-overlay.html'}])assert.equal(selectDesktopOverlay([bad],main),null);
 assert.equal(selectDesktopOverlay([target],'https://example.test'),null);
 assert.throws(()=>selectDesktopOverlay([target,{...target,id:'second'}],main),{code:'CDP_OVERLAY_AMBIGUOUS'});
});
test('ordinary browser does not attach a desktop portal',async()=>{
 const m=createDesktopOverlay({chromium:{connectOverCDP(){throw Error('must not attach')}},cdpUrl:'http://localhost:9222',
  getMainPage:()=>({url:()=>main,isClosed:()=>false}),fetchImpl:async()=>({ok:true,json:async()=>({'User-Agent':'Chrome/148'})})});
 assert.deepEqual(await m.scopes(),[]);await m.close();
});
test('failed auxiliary attachment releases its transport',async()=>{
 let closed=false;
 const m=createDesktopOverlay({chromium:{connectOverCDP:async()=>{throw Error('failed attachment')}},cdpUrl:'http://localhost:9222',
  getMainPage:()=>({url:()=>main,isClosed:()=>false}),transportFactory:async()=>({close(){closed=true}}),
  fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('/version')?{'User-Agent':'Electron/42',webSocketDebuggerUrl:'ws://localhost/browser'}:[target]})});
 await assert.rejects(m.scopes(),/failed attachment/);assert.equal(closed,true);await m.close();
});
let browser;
before(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_TEST_EXECUTABLE});});
after(async()=>{await browser?.close();});
test('menu lookup can cross portal, but uniqueness and explicit iframe scope stay strict',async()=>{
 const c=await browser.newContext();
 try{
  const p=await c.newPage(),o=await c.newPage();
  await p.setContent('<button id="main">Main</button><iframe id="vm" srcdoc="<button id=vm-button>VM</button>"></iframe>');
  await o.setContent('<button id="menu">Menu</button>');
  const runtime=createAutomationRuntime({page:p,registry:{},auxiliaryScopes:async()=>[{scope:o.locator('body'),name:'desktop-overlay'}],timeout:100});
  const result=await runtime.resolve({selector:'#menu',frame:'shell'});assert.equal(result.hit.scope,'desktop-overlay');
  await result.loc.click();assert.equal(await o.locator('#menu').evaluate(e=>e===document.activeElement),true);
  assert.equal((await runtime.resolve({selector:'body',frame:'shell'})).hit.scope,'shell');
  await p.locator('body').evaluate(e=>e.insertAdjacentHTML('beforeend','<button id="menu">Another menu</button>'));
  await assert.rejects(runtime.resolve({selector:'#menu',frame:'shell'}),{code:'AMBIGUOUS_TARGET'});
  const isolated=createAutomationRuntime({page:p,registry:{},vmIframe:'#vm',auxiliaryScopes:async()=>[{scope:o.locator('body'),name:'desktop-overlay'}],timeout:50,pollMs:5});
  await assert.rejects(isolated.resolve({selector:'#menu',frame:'vm'}),{code:'TARGET_TIMEOUT'});
  assert.equal((await isolated.resolve({selector:'#vm-button',frame:'vm'})).hit.scope,'vm');
 }finally{await c.close();}
});
