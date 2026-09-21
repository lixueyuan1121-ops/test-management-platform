import test from 'node:test';
import assert from 'node:assert/strict';
import { captureProbeScreenshot } from './probe-screenshot.mjs';
test('retries a stalled Electron screenshot without changing full-page coordinates', async () => {
  let calls=0;
  const r=await captureProbeScreenshot({screenshot:async o=>{ assert.deepEqual(o,{fullPage:true,type:'png',timeout:8000}); if(++calls===1)throw Error('Timeout');return Buffer.from('png'); }});
  assert.equal(calls,2);assert.equal(r.screenshotBuffer.toString(),'png');assert.equal(r.screenshotError,null);
});
test('reports a bounded failure after two attempts, without hiding the reason',async()=>{
  let calls=0;const r=await captureProbeScreenshot({screenshot:async()=>{calls++;throw Error('capture failed')}});
  assert.equal(calls,2);assert.equal(r.screenshotBuffer,null);assert.match(r.screenshotError,/capture failed/);
});
test('successful capture does not repeat',async()=>{let calls=0;await captureProbeScreenshot({screenshot:async()=>{calls++;return Buffer.from('png')}});assert.equal(calls,1)});
