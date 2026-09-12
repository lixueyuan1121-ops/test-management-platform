import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createRecordingPump } from './recording-pump.mjs';
import { loadRegistry, registrySnapshot } from './selector-registry.mjs';

test('上传失败和回包丢失保留事件；重启重传；最终确认才结束', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'record-pump-'));
  let received = [], attempt = 0, acked = [], stopped = false;
  const ev = { event_id: '1:doc:1', ts: 1, type: 'click', candidates: [{ by: 'testid', value: 'save' }] };
  const gui = { startRecording: async () => {}, stopRecording: async () => { stopped = true; },
    drainRecordEvents: async () => acked.length ? [] : [{ ev, frame: 'vm' }],
    ackRecordEvents: async ids => { acked = ids; } };
  const upload = async (id, body) => {
    received.push(body);
    if (++attempt < 3) throw Error(attempt === 1 ? 'offline' : 'response lost');
    return { acked: body.events.map(e => e.event_id), status: body.final ? 'stopped' : 'recording' };
  };
  try {
    let pump = createRecordingPump({ gui, upload, directory, consumerId: 'a' });
    await assert.rejects(pump.advance({ id: 1, status: 'recording' }), /offline/);
    assert.equal(JSON.parse(readFileSync(join(directory, '1.json'))).length, 1);
    pump = createRecordingPump({ gui, upload, directory, consumerId: 'b' });
    await assert.rejects(pump.advance({ id: 1, status: 'stopping' }), /response lost/);
    assert.equal(stopped, true);
    assert.equal(pump.activeId, 1);
    assert.equal(await pump.advance({ id: 1, status: 'stopping' }), false);
    assert.equal(pump.activeId, null);
    assert.ok(received.every(b => b.events.length === 1 && b.events[0].event_id === ev.event_id));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('成功空注册表替换旧数据，失败不回退旧表，快照不共享可变对象', async () => {
  const old = { registry: { old: { frame: 'shell', candidates: [{ by: 'css', value: '#old' }] } }, version: '1' };
  const first = await loadRegistry(async () => old, 1);
  const empty = await loadRegistry(async () => ({ registry: {}, version: '2' }), 1);
  assert.deepEqual(empty.registry, {});
  await assert.rejects(loadRegistry(async () => { throw Error('offline'); }, 1), { fail_kind: 'selector' });
  first.registry.old.frame = 'vm';
  assert.equal(registrySnapshot(old).registry.old.frame, 'shell');
});


test('binding 到达即落盘，上传期间新到事件不会被旧确认覆盖', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'record-ingress-'));
  let sink;
  const raw = id => ({ev:{event_id:'1:doc:'+id,ts:id,type:'click',candidates:[{by:'testid',value:'save'}]},frame:'vm'});
  const gui = {setRecordEventSink:fn => {sink=fn}, startRecording:async()=>{},stopRecording:async()=>{},drainRecordEvents:async()=>[],ackRecordEvents:async()=>{}};
  try {
    const pump=createRecordingPump({gui,directory,consumerId:'a',upload:async(id,body)=>{
      sink(1,raw(2));
      return {acked:body.events.map(e=>e.event_id),status:'recording'};
    }});
    sink(1,raw(1));
    assert.equal(JSON.parse(readFileSync(join(directory,'1.json'))).length,1);
    await pump.advance({id:1,status:'recording'});
    assert.deepEqual(JSON.parse(readFileSync(join(directory,'1.json'))).map(e=>e.event_id),['1:doc:2']);
  } finally {rmSync(directory,{recursive:true,force:true});}
});
