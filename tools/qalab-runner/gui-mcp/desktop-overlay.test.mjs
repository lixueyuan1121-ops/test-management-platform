import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {selectDesktopOverlay,createDesktopOverlay,desktopOverlayState,annotateOverlayTimeout} from './desktop-overlay.mjs';
import {createAutomationRuntime} from './runtime-loader.mjs';
const main='https://tenant.work.n.cn/';
const target={id:'overlay',type:'page',url:main+'desktop-overlay.html'};

test('timeout reports an unavailable populated portal without relabeling ordinary missing targets',()=>{
 const error=Object.assign(new Error('missing target'),{code:'TARGET_TIMEOUT',diagnostic:{target:'menu'}});
 const state={width:0,height:0,active:false,populated:true};
 assert.equal(annotateOverlayTimeout(error,state),error);
 assert.match(error.message,/0×0/);assert.equal(error.diagnostic.target,'menu');
 assert.deepEqual(error.diagnostic.desktop_overlay,state);
 for(const s of [{active:false,populated:false},{active:true,populated:true}]){
  const ordinary=Object.assign(new Error('missing target'),{code:'TARGET_TIMEOUT'});
  annotateOverlayTimeout(ordinary,s);assert.equal(ordinary.message,'missing target');
 }
 const ambiguous=Object.assign(new Error('duplicate'),{code:'AMBIGUOUS_TARGET'});
 annotateOverlayTimeout(ambiguous,state);assert.equal(ambiguous.message,'duplicate');
});
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

test('parked native portal cannot add phantom inputs; restored portal remains strict and clickable',async()=>{
 const c=await browser.newContext();let manager;
 try{
  const p=await c.newPage(),o=await c.newPage();
  await p.setContent('<input class="editor"><button id="duplicate">Main</button>');
  await o.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<div class="desktop-overlay-root"><input class="editor"><button id="duplicate">Portal</button><button id="menu">Select expert</button></div>'}));
  await o.goto(target.url);await o.setViewportSize({width:2,height:2});
  let options;
  manager=createDesktopOverlay({chromium:{connectOverCDP:async(_transport,opts)=>{options=opts;return {contexts:()=>[{pages:()=>[o]}],isConnected:()=>true,close:async()=>{}}}},
   cdpUrl:'http://localhost:9222',getMainPage:()=>({url:()=>main,isClosed:()=>false}),transportFactory:async()=>({}),
   fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('/version')?{'User-Agent':'Electron/42',webSocketDebuggerUrl:'ws://localhost/browser'}:[target]})});
  const runtime=createAutomationRuntime({page:p,registry:{},auxiliaryScopes:()=>manager.scopes(),timeout:100});
  assert.equal((await runtime.resolve({selector:'.editor'})).hit.scope,'shell');
  assert.equal(options.noDefaults,true);
  assert.deepEqual(await manager.state(),{width:2,height:2,populated:true,active:false});
  await assert.rejects(runtime.resolve({selector:'#menu'}),{code:'TARGET_TIMEOUT'});
  await o.setViewportSize({width:600,height:400});
  assert.equal((await desktopOverlayState(o)).active,true);
  const result=await runtime.resolve({selector:'#menu'});await result.loc.click();
  assert.equal(await o.locator('#menu').evaluate(e=>e===document.activeElement),true);
  await assert.rejects(runtime.resolve({selector:'#duplicate'}),{code:'AMBIGUOUS_TARGET'});
  await o.locator('.desktop-overlay-root').evaluate(e=>{e.innerHTML='<svg><defs><path d="M0 0"/></defs></svg><style>.unused {color:red}</style>';});
  assert.equal((await manager.state()).populated,false);
 }finally{await manager?.close();await c.close()}
});
