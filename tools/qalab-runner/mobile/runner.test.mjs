import test from 'node:test';
import assert from 'node:assert/strict';
import { readConfig, androidObservation, iosObservation, checkTarget } from './device.mjs';
import { appiumSession } from './appium.mjs';

const config = readConfig({ MOBILE_PLATFORM: 'android', MOBILE_UDID: 'ABC123', MOBILE_APP_ID: 'com.example.test' });
const props = '[ro.product.model]: [Pixel 8]\n[ro.build.version.release]: [15]';
const pkg = 'versionCode=101 minSdk=26\nversionName=1.0.1';
test('requires a physically connected Android and actual package build', () => {
  const actual = androidObservation(config, 'usb:001', props, pkg);
  assert.match(actual.device_id, /^[a-f0-9]{64}$/);
  assert.equal(actual.build, '101');
  assert.equal(actual.version, '1.0.1');
  for (const args of [['local', props, pkg], ['usb:001', props + '\n[ro.kernel.qemu]: [1]', pkg], ['usb:001', props, 'Package not found']])
    assert.throws(() => androidObservation(config, ...args));
  assert.throws(() => readConfig({ MOBILE_PLATFORM:'android', MOBILE_UDID:'emulator-5554', MOBILE_APP_ID:'com.example.test' }));
});
test('only accepts actual iOS metadata from a real device', () => {
  const cfg = readConfig({ MOBILE_PLATFORM:'ios', MOBILE_UDID:'00008110-0123456789ABCDEF', MOBILE_APP_ID:'com.example.test' });
  const apps = { [cfg.appId]: { CFBundleVersion:'119', CFBundleShortVersionString:'2.3' } };
  const caps = { platformVersion:'17.5' };
  assert.equal(iosObservation(cfg, { isSimulator:0, model:'iPhone' }, apps, caps).build, '119');
  for (const info of [{isSimulator:1}, {}, {isSimulator:null}])
    assert.throws(() => iosObservation(cfg, info, apps, caps));
  assert.throws(() => iosObservation(cfg, {isSimulator:false,model:'iPhone'}, {}, caps));
  assert.equal(iosObservation(cfg, {}, apps, caps, {udid:cfg.udid,model:'iPhone18,1'}).model,'iPhone18,1');
  assert.throws(() => iosObservation(cfg, {isSimulator:1}, apps, caps, {udid:cfg.udid,model:'iPhone18,1'}));
  assert.throws(() => readConfig({ MOBILE_PLATFORM:'ios', MOBILE_UDID:'00000000-0000-0000-0000-000000000000', MOBILE_APP_ID:'com.example.test' }));
});
test('target must match attached phone, runner and installed build', () => {
  const actual = androidObservation(config, 'usb:001', props, pkg);
  const target = { ...actual, runner_id:'phone-1' };
  assert.doesNotThrow(() => checkTarget(target, actual, 'phone-1'));
  for (const change of [{ device_id:'other' }, {platform:'ios'}, {app_id:'other.app'}, {build:'102'}, {runner_id:'other'}])
    assert.throws(() => checkTarget({...target,...change},actual,'phone-1'));
});
test('appium pins session to UDID, sends W3C actions and closes mismatched sessions', async () => {
  const calls=[];
  const request = async (url,init) => {
    const body=init.body ? JSON.parse(init.body) : undefined;
    calls.push({url,method:init.method,body});
    const value = url.endsWith('/session') ? {sessionId:'s',capabilities:{deviceUDID:config.udid}} :
      url.endsWith('/elements') ? [{'element-6066-11e4-a52e-4f735466cecf':'e1'}] : null;
    return Response.json({value});
  };
  const driver=await appiumSession(config,request);
  assert.equal(calls[0].body.capabilities.alwaysMatch['appium:udid'],config.udid);
  assert.deepEqual(await driver.findAll({using:'id',value:'com.example.test:id/send'}),['e1']);
  await driver.click('e1');
  await driver.type('e1','你好');
  assert.equal(calls.at(-1).body.text, '你好');
  await driver.close();
  assert.equal(calls.at(-1).method,'DELETE');
  const events=[];
  await assert.rejects(() => appiumSession(config,async (url,init) => {
    events.push(init.method);
    return Response.json({value:init.method==='POST' ? {sessionId:'s',capabilities:{deviceUDID:'unexpected'}} : null});
  }));
  assert.deepEqual(events,['POST','DELETE']);
});

import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { runScenario, processPending } from './runner.mjs';
import { platformClient } from './platform.mjs';

async function evidence(t) {
  const path=await mkdtemp(join(tmpdir(),'qalab-mobile-unit-'));
  t.after(() => rm(path,{recursive:true,force:true}));
  return path;
}

const actual=androidObservation(config,'usb:001',props,pkg);
const payload={mobile_target:{...actual,runner_id:'phone-1'},mobile_scenario:'send'};
const item={run_id:12,payload};
test('mobile requests report physical metadata and only poll as a mobile consumer',async t => {
  const cfg={...config,runnerId:'phone-1',runnerToken:'test-only',baseUrl:'http://platform.invalid',evidenceDir:await evidence(t)};
  const requests=[];
  const api=platformClient(cfg,async (url,init) => {
    const path=new URL(url);
    requests.push({path,init});
    if (path.pathname.endsWith('/screenshot')) {
      assert.equal(init.body.get('file').type,'image/png');
      return Response.json({code:0,data:{screenshot_url:'/uploads/execs/12/1.png'}});
    }
    return Response.json({code:0,data:{}});
  });
  await api.presence(actual);
  await api.pending();
  await api.claim(12);
  await api.heartbeat(12);
  assert.equal(await api.screenshot(12,1,Buffer.from('test')),'/uploads/execs/12/1.png');
  await api.report(12,{verdict:'pass'});
  for (const {path,init} of requests) {
    assert.equal(init.headers.Authorization,'Bearer test-only');
    if (path.pathname.includes('exec-queue')) assert.equal(path.searchParams.get('consumer'),'mobile');
  }
  assert.equal(JSON.parse(requests[0].init.body).device_id,actual.device_id);
  assert.equal(requests.at(-1).init.method,'PATCH');
});
test('mismatched targets cannot be claimed or operated, and uncertain report writes do not repeat execution',async () => {
  const calls=[];
  const cfg={...config,runnerId:'phone-1'};
  const platform={
    claim:async () => {calls.push('claim');return item;},
    heartbeat:async () => calls.push('heartbeat'),
    presence:async () => calls.push('presence'),
    report:async () => {calls.push('report');throw new Error('network interrupted');}
  };
  const executeScenario=async () => {calls.push('execute');return {report:{verdict:'pass'}};};
  await assert.rejects(() => processPending({config:cfg,driver:{},device:actual,platform,
    item:{...item,payload:{...payload,mobile_target:{...payload.mobile_target,build:'999'}}},executeScenario}));
  assert.deepEqual(calls,[]);
  await assert.rejects(() => processPending({config:cfg,driver:{},device:actual,platform,item,executeScenario,inspectDevice:async () => actual}),/network interrupted/);
  assert.deepEqual(calls,['claim','heartbeat','execute','presence','report']);
  const received = await processPending({config:cfg,driver:{},device:actual,item,executeScenario,
    platform:{...platform,report:async (_,result) => result},inspectDevice:async () => ({...actual,build:'replaced'})});
  assert.equal(received.verdict,'blocked');
});
test('local and queue mode require actual successful checks and captures',async t => {
  const cfg={...config,evidenceDir:await evidence(t)};
  const driver={mobile:async () => 4,screenshot:async () => Buffer.from('png fixture'),source:async () => '<tree/>'};
  let seen;
  const execute=async ({artifact,marker}) => {
    seen=marker;
    return {verdict:'pass',reason:'OK',steps:[{no:1,ok:true,action:'assert_reply',check:{actual:'hi',expected:'nonempty'},shot:await artifact(1)}]};
  };
  const options={config:cfg,driver,device:actual,scenario:'send',execute,locator:() => () => {}};
  const local=await runScenario(options);
  assert.match(seen,/^QA-/);
  assert.equal(local.report.verdict,'pass');
  assert.equal(await readFile(local.report.evidence_url,'utf8'),'png fixture');
  const platform={screenshot:async (id,idx) => '/uploads/execs/' + id + '/' + idx + '.png'};
  const remote=await runScenario({...options,runId:12,platform});
  assert.equal(remote.report.evidence_url,'/uploads/execs/12/1.png');
  const missing=await runScenario({...options,execute:async () => ({verdict:'pass',reason:'claimed',steps:[{ok:true,action:'tap',shot:'wrong.png'}]})});
  assert.equal(missing.report.verdict,'blocked');
  assert.notEqual(missing.report.evidence_url,'wrong.png');
  const fail=await runScenario({...options,execute:async () => ({verdict:'fail',reason:'duplicate reply',steps:[{no:1,action:'assert_count',ok:false}]})});
  assert.equal(fail.report.fail_kind,'business');
  assert.equal(await readFile(join(fail.folder,'failure.xml'),'utf8'),'<tree/>');
});

import { activeLink } from './runner.mjs';
import { createHash } from 'node:crypto';

test('a timed-out Appium command gives its path, prevents later actions, and replaces the session', async () => {
  const calls = [];
  const driver = await appiumSession(config, async (url, init) => {
    calls.push(init.method);
    if (url.endsWith('/session')) return Response.json({value:{sessionId:'sensitive-session',capabilities:{}}});
    if (init.method === 'DELETE') return Response.json({value:null});
    throw new DOMException('Timeout', 'TimeoutError');
  });
  await assert.rejects(driver.source(), e => e.message.includes('GET /session/:id/source') && !e.message.includes('sensitive-session'));
  assert.equal(driver.needsReconnect,true);
  await assert.rejects(driver.click('send'));
  assert.equal(calls.filter(c => c === 'POST').length,1);
  const next = {driver:{},device:actual};
  assert.equal(await activeLink(config,{driver},async () => next),next);
  assert.equal(calls.at(-1),'DELETE');
  assert.equal(await activeLink(config,next,async () => assert.fail('healthy session should be reused')), next);
});
test('a blocked driver can still attach a PNG from the same USB serial', async t => {
  const cfg = {...config,evidenceDir:await evidence(t)};
  const png = Buffer.from([137,80,78,71,13,10,26,10,0]);
  const driver = {
    mobile:async () => 4,
    screenshot:async () => { throw new Error('Appium disconnected'); },
    source:async () => { throw new Error('Appium disconnected'); },
  };
  const result = await runScenario({
    config:cfg,device:actual,driver,scenario:'send',locator:() => () => {},
    execute:async () => ({verdict:'blocked',reason:'source timeout',steps:[{no:1,action:'locate',ok:false}]}),
    captureAndroid:async c => { assert.equal(c.udid,cfg.udid); return png; },
  });
  assert.equal(result.report.verdict,'blocked');
  assert.equal(result.report.report[0].screenshot_method,'adb_screencap');
  assert.deepEqual(await readFile(result.report.evidence_url),png);
});
test('the claimed native script is executed and persisted with content-addressed evidence', async t => {
  const cfg = {...config,runnerId:'phone-1',evidenceDir:await evidence(t)};
  const script = [{action:'new_chat'},{action:'compose',text:'你好🌟'},{action:'assert_draft',text:'你好🌟'}];
  const fetched = {...item,payload:{...payload,mobile_scenario:'script',mobile_script:[{action:'outdated'}]}};
  const claimed = {...item,payload:{...payload,mobile_scenario:'script',mobile_script:script}};
  const driver = {mobile:async () => 4,screenshot:async () => Buffer.from('image')};
  let folder;
  const platform = {claim:async () => claimed,heartbeat:async () => {},presence:async () => {},report:async (_,r) => r,
    screenshot:async () => '/uploads/execs/12/1.png'};
  const result = await processPending({config:cfg,driver,device:actual,platform,item:fetched,inspectDevice:async () => actual,
    executeScenario:async options => {
      const output = await runScenario({...options,locator:() => () => {},execute:async args => {
        assert.deepEqual(args.script,script);
        await args.artifact(1);
        return {verdict:'pass',reason:'matches',steps:[{no:1,ok:true,check:{actual:'你好🌟',expected:'你好🌟'}}]};
      }});
      folder = output.folder;
      return output;
    },
  });
  const bytes = await readFile(join(folder,'script.json'));
  assert.deepEqual(JSON.parse(bytes),script);
  assert.equal(result.report.at(-1).mobile_script_sha256,createHash('sha256').update(bytes).digest('hex'));
});
test('a long case independently renews run heartbeat and verified handset presence', async () => {
  let runs = 0, scans = 0, presences = 0;
  const platform = {
    claim:async () => item,
    heartbeat:async () => { runs++; },
    presence:async d => { assert.equal(d.device_id,actual.device_id); presences++; },
    report:async (_,r) => r,
  };
  const report = await processPending({
    config:{...config,runnerId:'phone-1'},driver:{},device:actual,platform,item,heartbeatMs:5,presenceMs:8,
    inspectDevice:async () => { scans++; return actual; },
    executeScenario:async () => {
      const until = Date.now() + 2000;
      while (presences < 2 && Date.now() < until) await new Promise(resolve => setTimeout(resolve,5));
      return {report:{verdict:'pass'}};
    },
  });
  assert.equal(report.verdict,'pass');
  assert.ok(runs >= 2);
  assert.ok(scans >= 3);
  assert.equal(scans,presences);
});

test('platform cancellation before the first action closes the session', async () => {
  let closed = false;
  let executed = false;
  const report = await processPending({
    config: {...config, runnerId:'phone-1'}, item, device:actual,
    driver: {close:async () => { closed = true; }},
    platform: {claim:async () => item, heartbeat:async () => ({alive:true, cancel_requested:true}), report:async (_,body) => body},
    executeScenario:async () => { executed = true; },
  });
  assert.equal(closed,true);
  assert.equal(executed,false);
  assert.equal(report.verdict,'blocked');
});
