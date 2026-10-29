import test from 'node:test';
import assert from 'node:assert/strict';
import {selectBusinessTarget,scopedAttachMessage,connectApplication,createScopedTransport} from './cdp-connection.mjs';
const target={id:'app',type:'page',url:'https://tenant.work.n.cn/'};
test('transport delivers responses without an open callback and preserves child events',async()=>{
 let socket;
 class FakeSocket extends EventTarget {
   constructor(){super();socket=this;queueMicrotask(()=>this.dispatchEvent(new Event('open')));}
   send(raw){this.sent=JSON.parse(raw);}
   close(){this.dispatchEvent(new Event('close'));}
 }
 const t=await createScopedTransport('ws://localhost','app',{Socket:FakeSocket});
 const event={method:'Target.attachedToTarget',params:{sessionId:'iframe',targetInfo:{type:'iframe'}}};
 socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(event)}));
 const seen=[];t.onmessage=m=>seen.push(m);
 socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({id:1,result:{}})}));
 assert.deepEqual(seen,[event,{id:1,result:{}}]);
 t.send({id:2,method:'Runtime.enable',sessionId:'iframe'});
 assert.deepEqual(socket.sent,{id:2,method:'Runtime.enable',sessionId:'iframe'});
 let closed=false;t.onclose=()=>{closed=true};t.close();assert.equal(closed,true);
});
test('selects app, excluding empty target, extension and lookalike host',()=>{
 assert.equal(selectBusinessTarget([{id:'empty',type:'page',url:''},{type:'page',url:'https://work.n.cn.evil.test/'},{type:'page',url:'chrome-extension://x/'},target]).id,'app');
});
test('fails closed when no unique business target; explicit target must be business page',()=>{
 assert.throws(()=>selectBusinessTarget([]),/0/);
 assert.throws(()=>selectBusinessTarget([target,{...target,id:'second'}]),/2/);
 assert.equal(selectBusinessTarget([target,{...target,id:'second'}],'second').id,'second');
 assert.throws(()=>selectBusinessTarget([{id:'empty',type:'page',url:''}],'empty'),/0/);
});
test('only replaces root auto attachment with a real scoped CDP attachment',()=>{
 const root={id:2,method:'Target.setAutoAttach',params:{autoAttach:true,flatten:true}};
 assert.deepEqual(scopedAttachMessage(root,'app'),{id:2,method:'Target.attachToTarget',params:{targetId:'app',flatten:true}});
 for(const m of [{...root,sessionId:'iframe'}, {...root,params:{autoAttach:false}}, {id:9,method:'Runtime.evaluate',sessionId:'app'}])assert.equal(scopedAttachMessage(m,'app'),m);
 assert.equal(root.method,'Target.setAutoAttach');
});
test('Electron connects only selected target with normal Playwright options',async()=>{
 const marker={close(){throw Error('should remain open')}};let selected;
 const result=await connectApplication({connectOverCDP:async(t,o)=>{assert.equal(t,marker);assert.equal(o.timeout,30000);return 'browser'}},'http://127.0.0.1:9222',{
 fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('version')?{'User-Agent':'Electron/42.7',webSocketDebuggerUrl:'ws://127.0.0.1:9222/browser'}:[target]}),
 transportFactory:async(url,id)=>{selected=id;return marker}
 });assert.equal(result,'browser');assert.equal(selected,'app');
});
test('connection failure closes transport and keeps cause',async()=>{
 let closed=false;
 await assert.rejects(connectApplication({connectOverCDP:async()=>{throw Error('frame timeout')}},'http://127.0.0.1:9222',{
 fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('version')?{'User-Agent':'Electron/42.7',webSocketDebuggerUrl:'ws://127.0.0.1:9222/browser'}:[target]}),
 transportFactory:async()=>({close(){closed=true}})
 }),/frame timeout/);assert.equal(closed,true);
});
test('regular browsers retain normal CDP connection',async()=>{
 let seen;
 await connectApplication({connectOverCDP:async arg=>{seen=arg}},'http://127.0.0.1:9222',{fetchImpl:async()=>({ok:true,json:async()=>({'User-Agent':'Chrome/148'})})});
 assert.equal(seen,'http://127.0.0.1:9222');
});
test('excludes desktop voice surface before checking main page uniqueness',()=>{
 const voice={...target,id:'voice',url:target.url+'?vm_target=elec&namiwork_surface=desktop_voice'};
 assert.equal(selectBusinessTarget([voice,target]).id,'app');
 assert.throws(()=>selectBusinessTarget([voice]),{code:'CDP_TARGET_NOT_READY'});
 assert.throws(()=>selectBusinessTarget([voice,target],'voice'),{code:'CDP_TARGET_NOT_READY'});
 assert.throws(()=>selectBusinessTarget([voice,target,{...target,id:'second'}]),{code:'CDP_TARGET_AMBIGUOUS'});
});
test('waits for main renderer registration while only voice is ready',async()=>{
 let polls=0,selected;
 await connectApplication({connectOverCDP:async()=>({})},'http://127.0.0.1:9222',{
  timeout:1000,pollIntervalMs:1,
  fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('version')?{'User-Agent':'Electron/42',webSocketDebuggerUrl:'ws://localhost/browser'}:
   ++polls<3?[{...target,id:'voice',url:target.url+'?namiwork_surface=desktop_voice'}]:[target]}),
  transportFactory:async(url,id)=>{selected=id;return {close(){}}}
 });
 assert.equal(polls,3);assert.equal(selected,'app');
});
test('desktop overlay is auxiliary even without a surface query parameter',()=>{
 const overlay={...target,id:'overlay',url:target.url+'desktop-overlay.html?mode=selection'};
 assert.equal(selectBusinessTarget([overlay,target]).id,'app');
 assert.throws(()=>selectBusinessTarget([overlay]),{code:'CDP_TARGET_NOT_READY'});
 assert.throws(()=>selectBusinessTarget([overlay,target],'overlay'),{code:'CDP_TARGET_NOT_READY'});
 assert.throws(()=>selectBusinessTarget([overlay,target,{...target,id:'second'}]),{code:'CDP_TARGET_AMBIGUOUS'});
 // A business route mentioning the overlay is still a real main-page candidate.
 const route={...target,id:'route',url:target.url+'?redirect=desktop-overlay.html'};
 assert.equal(selectBusinessTarget([overlay,route]).id,'route');
});
test('waits for main renderer when voice and overlay windows register first',async()=>{
 let polls=0,selected;
 const auxiliary=[{...target,id:'voice',url:target.url+'?namiwork_surface=desktop_voice'},
  {...target,id:'overlay',url:target.url+'desktop-overlay.html'}];
 await connectApplication({connectOverCDP:async()=>({})},'http://127.0.0.1:9222',{
  timeout:1000,pollIntervalMs:1,
  fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('version')?{'User-Agent':'Electron/42',webSocketDebuggerUrl:'ws://localhost/browser'}:
   ++polls<3?auxiliary:[...auxiliary,target]}),
  transportFactory:async(url,id)=>{selected=id;return {close(){}}}
 });
 assert.equal(polls,3);assert.equal(selected,'app');
});
test('missing renderer has a bounded wait and never attaches auxiliary targets',async()=>{
 let attached=false;
 await assert.rejects(connectApplication({connectOverCDP:async()=>{}},'http://127.0.0.1:9222',{
  timeout:10,pollIntervalMs:1,
  fetchImpl:async url=>({ok:true,json:async()=>url.endsWith('version')?{'User-Agent':'Electron/42',webSocketDebuggerUrl:'ws://localhost/browser'}:[]}),
  transportFactory:async()=>{attached=true;return {close(){}}}
 }),{code:'CDP_TARGET_NOT_READY'});
 assert.equal(attached,false);
});
