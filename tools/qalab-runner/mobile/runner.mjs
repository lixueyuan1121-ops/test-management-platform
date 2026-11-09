import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import lease from '../desktop-lease.cjs';
import { readConfig, inspectAndroid, inspectIOS, checkTarget, androidScreenshot } from './device.mjs';
import { appiumSession, checkedPng } from './appium.mjs';
import { platformClient } from './platform.mjs';

const scenarios = ['send','followup','stop','history'];
const usage = 'node mobile/runner.mjs doctor | direct [send|followup|stop|history|all] | direct script <steps.json> | serve';
const log = (...args) => console.log(new Date().toISOString(), ...args);

async function connect(config) {
  const android = config.platform === 'android';
  const androidDevice = android ? await inspectAndroid(config) : undefined;
  const driver = android && config.androidTransport === 'native'
    ? await (await import('./android-native.mjs')).androidNativeSession(config)
    : await appiumSession(config);
  try {
    const device = android ? androidDevice : await inspectIOS(config,driver);
    return {driver,device};
  } catch (error) {
    await driver.close().catch(() => {});
    throw error;
  }
}

export async function activeLink(config, previous, open = connect) {
  if (previous && !previous.driver.needsReconnect) return previous;
  if (previous) await previous.driver.close().catch(error => log('清理失效会话：', error.message));
  return open(config);
}

export async function runScenario({config,driver,device,scenario,script,runId,platform,execute,locator,captureAndroid = androidScreenshot}) {
  const start = Date.now();
  const marker = 'QA-' + randomUUID().slice(0,8);
  const folder = join(config.evidenceDir, config.deviceId, runId ? 'run-' + runId : scenario + '-' + marker);
  await mkdir(folder,{recursive:true,mode:0o700});
  const shots = new Map();
  const artifact = async index => {
    if (!Number.isSafeInteger(index) || index < 1) throw new Error('截图步骤编号无效');
    let png, method = config.platform === 'android' && config.androidTransport === 'native' ? 'uiautomator2' : 'appium';
    try {
      png = await driver.screenshot();
    } catch (error) {
      if (config.platform !== 'android') throw error;
      // A frozen UiAutomator2 cannot take a screenshot. Capture the same USB serial directly.
      png = checkedPng(await captureAndroid(config));
      method = 'adb_screencap';
    }
    const path = join(folder, 'step-' + index + '.png');
    await writeFile(path,png,{mode:0o600});
    const ref = platform ? await platform.screenshot(runId,index,png) : path;
    shots.set(index,{shot:ref, screenshot_method:method});
    return ref;
  };
  let result;
  try {
    if (scenario === 'script') {
      if (!Array.isArray(script) || !script.length) throw new Error('真机脚本需要非空 JSON 步骤数组');
    } else if (!scenarios.includes(scenario)) {
      throw new Error('未知真机对话场景 ' + scenario);
    }
    const app = config.platform === 'android' ? {appId:config.appId} : {bundleId:config.appId};
    if (config.platform === 'ios' && Number(await driver.mobile('queryAppState',app)) === 1)
      await driver.mobile('launchApp',app);
    else
      await driver.mobile('activateApp',app);
    if (Number(await driver.mobile('queryAppState',app)) !== 4) throw new Error('目标 APP 未进入前台');
    const runDialog = execute || (await import('./dialog.mjs')).runDialog;
    const createLocatorResolver = locator || (await import('./locator.mjs')).createLocatorResolver;
    result = await runDialog({driver,scenario,script,marker,artifact,resolveLocator:createLocatorResolver(driver,{appId:config.appId,build:device.build,cacheDir:process.env.MOBILE_LOCATOR_CACHE}), timeoutMs:config.timeoutMs});
    if (!['pass','fail','blocked'].includes(result?.verdict) || !Array.isArray(result.steps)) throw new Error('对话执行未返回有效结论');
  } catch (error) {
    result = {verdict:'blocked',reason:error.message,steps:[{no:1,action:'diagnostics',desc:'真机执行准备',ok:false,error:error.message}]};
  }
  const steps = result.steps.map((step,i) => {
    const no = step.no || i + 1;
    // Accept only this run's captured screenshots, including in direct mode.
    const { shot: ignored, ...rest } = step;
    return {...rest,no,...shots.get(no)};
  });
  if (result.verdict === 'pass' && (steps.some(s => s.ok === false) || !steps.some(s => s.ok && s.check) || !steps.some(s => s.shot)))
    result = {...result,verdict:'blocked',reason:'缺少完整的成功断言或真机截图，无法验证通过'};
  if (result.verdict !== 'pass' && !steps.some(s => !s.ok && s.shot)) {
    let step = steps.findLast(s => !s.ok);
    if (!step) { step={no:steps.length + 1,action:'diagnostics',desc:'失败现场',ok:false}; steps.push(step); }
    try { await artifact(step.no); Object.assign(step, shots.get(step.no)); } catch (error) { step.screenshot_error = error.message; }
  }
  // Keep the native tree private on the device host, separate from public screenshots.
  if (result.verdict !== 'pass') {
    try { await writeFile(join(folder,'failure.xml'),await driver.source(),{mode:0o600}); } catch {}
  }
  const snapshot = scenario === 'script' && Array.isArray(script) ? {
    mobile_script_sha256: createHash('sha256').update(JSON.stringify(script)).digest('hex'),
    mobile_script_length: script.length,
  } : {};
  if (scenario === 'script' && Array.isArray(script))
    await writeFile(join(folder, 'script.json'), JSON.stringify(script), {mode:0o600});
  const report = {verdict:result.verdict,reason:result.reason,fail_kind:result.verdict === 'pass' ? null : result.verdict === 'fail' ? 'business' : 'selector',
    duration_ms:Date.now()-start,evidence_url:steps.findLast(s => s.shot)?.shot || null,
    report:[...steps, {no:steps.length + 1,action:'device_info',desc:'真机执行环境',ok:true,device,scenario,marker,...snapshot}]};
  await writeFile(join(folder,'result.json'),JSON.stringify(report,null,2),{mode:0o600});
  return {folder, report};
}

const inspect = (config,driver) => config.platform === 'android' ? inspectAndroid(config) : inspectIOS(config,driver);

export async function processPending({config,driver,device,platform,item,executeScenario = runScenario,
  inspectDevice = inspect, heartbeatMs = 20000, presenceMs = 45000}) {
  const {run_id:id,payload} = item;
  checkTarget(payload?.mobile_target,device,config.runnerId);
  const claimed = await platform.claim(id);
  let timer, pulse, presenceTimer, presencePulse, report;
  const heartbeat = async () => {
    const state = await platform.heartbeat(id);
    if (state?.cancel_requested || state?.alive === false) {
      await driver.close();
      throw new Error('平台已终止本次真机执行');
    }
  };
  try {
    const current = claimed.payload;
    checkTarget(current?.mobile_target,device,config.runnerId);
    await heartbeat();
    timer = setInterval(() => {
      if (pulse) return;
      pulse = heartbeat().catch(async e => {
        log('执行心跳失败',id,e.message);
        if ([403,409].includes(e.status)) await driver.close().catch(() => {});
      }).finally(() => { pulse = null; });
    },heartbeatMs);
    // The platform has a separate 180s TTL for verified mobile presence. Re-observe the phone
    // independently of the queue heartbeat, which only proves this worker still owns the run.
    presenceTimer = setInterval(() => {
      if (presencePulse || driver.needsReconnect) return;
      presencePulse = (async () => {
        const actual = await inspectDevice(config, driver);
        checkTarget(current.mobile_target, actual, config.runnerId);
        await platform.presence(actual);
      })().catch(error => log('真机状态刷新失败：', id, error.message)).finally(() => { presencePulse = null; });
    }, presenceMs);
    const outcome = await executeScenario({config,driver,device,scenario:current.mobile_scenario,
      script:current.mobile_script,runId:id,platform});
    report = outcome.report;
    if (report.verdict === 'pass') {
      try {
        const finalDevice = await inspectDevice(config,driver);
        checkTarget(current.mobile_target,finalDevice,config.runnerId);
        await platform.presence(finalDevice);
      } catch (error) {
        const reason = '回填前真机校验失败：' + error.message;
        const steps = report.report || [];
        report = {...report, verdict:'blocked',fail_kind:'selector',reason,report:[...steps,
          {no:steps.length + 1,action:'device_check',desc:'回填前设备与构建校验',ok:false,error:reason}]};
        if (outcome.folder) await writeFile(join(outcome.folder,'result.json'),JSON.stringify(report,null,2),{mode:0o600});
      }
    }
  } catch (error) {
    if ([403,409].includes(error.status)) throw error;
    report = {verdict:'blocked',fail_kind:'selector',reason:error.message,
      report:[{no:1,action:'device_check',desc:'执行准备',ok:false,error:error.message,device}]};
  } finally {
    clearInterval(timer);
    clearInterval(presenceTimer);
    if (pulse) await pulse;
    if (presencePulse) await presencePulse;
  }
  // Publish once. A transport error can happen after commit and must never replace the result.
  return await platform.report(id,report);
}

async function serve(config,signal) {
  const platform = platformClient(config);
  let link;
  let advertised;
  try {
    while (!signal.aborted) {
      try {
        const previous = link;
        link = await activeLink(config, previous);
        if (link === previous) link.device = await inspect(config,link.driver);
        // ADB alone cannot confirm that the Appium session is still alive.
        if (config.platform === 'android') await link.driver.mobile('queryAppState',{appId:config.appId});
        const registration = await platform.presence(link.device);
        checkTarget(registration.mobile_target,link.device,config.runnerId);
        advertised = link.device;
        const pending = await platform.pending();
        if (pending?.length) await processPending({config,...link,platform,item:pending[0]});
      } catch (error) {
        log('等待真机或平台就绪：',error.message);
        if (advertised) await platform.presence({...advertised,online:false}).catch(() => {});
        advertised = undefined;
        if (link) await link.driver.close().catch(() => {});
        link = undefined;
      }
      await delay(5000,undefined,{signal}).catch(() => {});
    }
  } finally {
    if (advertised) await platform.presence({...advertised,online:false}).catch(() => {});
    if (link) await link.driver.close().catch(() => {});
  }
}

export async function main(argv = process.argv.slice(2)) {
  const [mode, scenario='all', scriptPath] = argv;
  if (['-h','--help'].includes(mode)) { console.log(usage); return; }
  if (!['doctor','direct','serve'].includes(mode) || (mode === 'direct' && !['all','script',...scenarios].includes(scenario)))
    throw new Error(usage);
  let script;
  if (mode === 'direct' && scenario === 'script') {
    if (!scriptPath) throw new Error(usage);
    script = JSON.parse(await readFile(scriptPath, 'utf8'));
    if (!Array.isArray(script) || !script.length) throw new Error('真机脚本需要非空 JSON 步骤数组');
  }
  const config = readConfig();
  const release = await lease.acquireDesktopLease({port:config.lockPort});
  if (!release) throw new Error('绑定的真机已被另一个进程占用');
  try {
    if (mode === 'serve') {
      const abort = new AbortController();
      const stop = () => abort.abort();
      process.once('SIGINT',stop); process.once('SIGTERM',stop);
      try { await serve(config,abort.signal); }
      finally { process.off('SIGINT',stop); process.off('SIGTERM',stop); }
      return;
    }
    let link = await connect(config);
    try {
      if (mode === 'doctor') { console.log(JSON.stringify(link.device,null,2)); return; }
      for (const name of (scenario === 'all' ? scenarios : [scenario])) {
        link = await activeLink(config, link);
        const {folder,report} = await runScenario({config,...link,scenario:name,script});
        log(name,report.verdict,report.reason,folder);
        if (report.verdict !== 'pass') process.exitCode=1;
      }
    } finally { await link.driver.close(); }
  } finally { await release(); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { console.error(error.message); process.exitCode=1; });
