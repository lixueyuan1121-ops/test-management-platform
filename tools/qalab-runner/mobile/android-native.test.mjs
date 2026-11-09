import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {androidNativeSession} from './android-native.mjs';

test('Android bridge binds the serial, multiplexes responses and handles worker shutdown', async t => {
  const dir = await mkdtemp(join(tmpdir(),'android-transport-'));
  t.after(() => rm(dir,{recursive:true,force:true}));
  const executable = join(dir,'worker');
  await writeFile(executable, '#!' + process.execPath + '\n' + `
    const lines = require('node:readline').createInterface({input:process.stdin});
    lines.on('line', json => {
      const {id,method,params} = JSON.parse(json);
      const results = {hello:{deviceUDID:'usb-001'}, findAll:['elem'], text:'中文', enabled:true,
        screenshot:Buffer.from('89504e470d0a1a0a','hex').toString('base64')};
      const reply = method === 'mobile' && params.name==='bad' ? {id,error:'unsupported'} : {id,value:results[method] ?? null};
      setTimeout(() => console.log(JSON.stringify(reply)), method==='text'?10:0);
    });`, {mode:0o700});
  const config = {androidPython:executable, udid:'usb-001',appId:'org.example',platform:'android'};
  const d = await androidNativeSession(config);
  t.after(() => d.close());
  assert.deepEqual(await d.findAll({using:'accessibility id',value:'send'}),['elem']);
  assert.deepEqual(await Promise.all([d.text('elem'),d.enabled('elem')]),['中文',true]);
  assert.equal((await d.screenshot()).toString('hex'),'89504e470d0a1a0a');
  await assert.rejects(d.mobile('bad'),/unsupported/);
  await d.close();
  assert.equal(d.needsReconnect,true);
  await assert.rejects(d.source(),/已关闭/);
  await assert.rejects(androidNativeSession({...config,udid:'wrong'}),/不同手机/);
});
