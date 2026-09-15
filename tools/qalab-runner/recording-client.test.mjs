import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRecordingClient } from './recording-client.mjs';
import { createRecordingPump } from './recording-pump.mjs';

test('queue claim, retries and final upload use one consumer and stable event IDs', async () => {
 const requests=[];let failed=false;const saved=new Set();
 const api=async(method,path,body)=>{
  const url=new URL(path,'http://fixture');requests.push({method,url,body});
  assert.equal(url.searchParams.get('runner'),'bendi-win & test');
  if(method==='GET') { assert.ok(url.searchParams.get('consumer_id'));return [{id:1,status:'recording'}]; }
  assert.equal(body.consumer_id,requests[0].url.searchParams.get('consumer_id'));
  for(const event of body.events){assert.ok(event.event_id);saved.add(event.event_id);}
  if(!failed){failed=true;throw Error('response lost');}
  return {acked:body.events.map(e=>e.event_id),status:body.final?'stopped':'recording'};
 };
 const client=createRecordingClient(api,'bendi-win & test');
 assert.notEqual(client.consumerId,createRecordingClient(api,'bendi-win & test').consumerId);
 const directory=mkdtempSync(join(tmpdir(),'record-client-'));
 let acked=false;
 const gui={startRecording:async()=>{},stopRecording:async()=>{},ackRecordEvents:async()=>{acked=true},
  drainRecordEvents:async()=>acked?[]:[{ev:{event_id:'1:doc:1',ts:1,type:'click',candidates:[{by:'testid',value:'save'}]},frame:'shell'}]};
 try {
  const pump=createRecordingPump({gui,directory,consumerId:client.consumerId,upload:client.reportRecordEvents});
  const [session]=await client.fetchRecords();await assert.rejects(pump.advance(session),/response lost/);
  await client.fetchRecords();assert.equal(await pump.advance(session),true);
  assert.equal(await pump.advance({id:1,status:'stopping'}),false);assert.equal(saved.size,1);
  assert.equal(requests.at(-1).body.final,true);
 } finally {rmSync(directory,{recursive:true,force:true});}
});
