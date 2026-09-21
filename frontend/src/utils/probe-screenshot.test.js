import test from 'node:test'
import assert from 'node:assert/strict'
import { createProbeScreenshotLoader } from './probe-screenshot.js'
function harness(fetchProbe) {
  const urls=[], errors=[], queue=[]; let time=0
  const loader=createProbeScreenshotLoader({fetchProbe,onUrl:url=>urls.push(url),onError:e=>errors.push(e),now:()=>time,
    intervalMs:10,timeoutMs:30,setTimer:fn=>{queue.push(fn);return fn},clearTimer:fn=>{const i=queue.indexOf(fn);if(i>=0)queue.splice(i,1)}})
  return {loader,urls,errors,queue,async tick(){time+=10;await queue.shift()?.()}}
}
test('continues past done until a delayed screenshot is available',async()=>{
 let n=0;const h=harness(async()=>({status:'done',screenshot_url:++n===3?'/late.png':null}))
 await h.loader.load(1);assert.equal(h.queue.length,1);await h.tick();await h.tick()
 assert.deepEqual(h.urls,['/late.png']);assert.deepEqual(h.errors,[]);assert.equal(h.queue.length,0)
})
test('missing image and transient fetch failures have a deadline and can retry',async()=>{
 let ready=false;const h=harness(async()=>{if(!ready)throw Error('offline');return {screenshot_url:'/ok.png'}})
 await h.loader.load(1);await h.tick();await h.tick();await h.tick();assert.equal(h.errors.length,1);assert.equal(h.queue.length,0)
 ready=true;await h.loader.load(1);assert.deepEqual(h.urls,['/ok.png'])
})
test('late response from an older probe cannot overwrite the new screenshot',async()=>{
 let resolve;const h=harness(id=>id===1?new Promise(r=>{resolve=r}):Promise.resolve({screenshot_url:'/new.png'}))
 const old=h.loader.load(1);await h.loader.load(2);resolve({screenshot_url:'/old.png'});await old
 assert.deepEqual(h.urls,['/new.png']);assert.equal(h.queue.length,0)
})
test('scope change or unmount cancels both timers and in-flight results',async()=>{
 const h=harness(async()=>({screenshot_url:null}));await h.loader.load(1);h.loader.cancel();assert.equal(h.queue.length,0)
 let resolve;const pending=harness(()=>new Promise(r=>{resolve=r}));const loading=pending.loader.load(1);pending.loader.cancel();resolve({screenshot_url:'/stale.png'});await loading
 assert.deepEqual(pending.urls,[])
})

test('reports device capture failure immediately instead of polling a terminal record',async()=>{
 const h=harness(async()=>({status:'done',result:{screenshot_error:'capture timeout'}}));await h.loader.load(1);
 assert.equal(h.queue.length,0);assert.match(h.errors[0],/capture timeout/);
})
