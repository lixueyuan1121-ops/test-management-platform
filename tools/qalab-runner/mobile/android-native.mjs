// USB-native fallback. The persistent Python process owns one serial-bound uiautomator2 connection.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {checkedPng} from './appium.mjs';

export async function androidNativeSession(config) {
  if (config.platform !== 'android' || !/^[a-z\d][\w.-]{1,127}$/i.test(config.udid || '') ||
      !/^[a-z][\w-]*(\.[a-z\d_][\w-]*)+$/i.test(config.appId || '')) {
    throw new Error('Android 原生驱动需要绑定的手机和应用包名');
  }
  const python = config.androidPython || process.env.MOBILE_ANDROID_PYTHON || 'python3';
  const script = fileURLToPath(new URL('./android-hierarchy.py', import.meta.url));
  const child = spawn(python, ['-u', script, config.udid, config.appId, config.adb || 'adb'],
    {shell:false, windowsHide:true, stdio:['pipe','pipe','pipe']});
  const pending = new Map();
  const lines = createInterface({input:child.stdout});
  let seq = 0, closed = false;
  // The Python library may log transport diagnostics that include device data; keep stdout protocol-only.
  child.stderr.resume();
  function terminate(error) {
    closed = true;
    for (const call of pending.values()) { clearTimeout(call.timer); call.reject(error); }
    pending.clear();
    lines.close();
    if (child.exitCode === null) child.kill('SIGKILL');
  }
  child.on('error', () => terminate(new Error('无法启动 Android Python，请检查 MOBILE_ANDROID_PYTHON')));
  child.on('exit', () => terminate(new Error('Android 原生驱动进程已退出')));
  child.stdin.on('error', () => terminate(new Error('Android 原生驱动管道断开')));
  lines.on('line', line => {
    let result;
    try { result = JSON.parse(line); } catch { terminate(new Error('Android 原生驱动返回了无效数据')); return; }
    const call = pending.get(result.id);
    if (!call) return;
    pending.delete(result.id);
    clearTimeout(call.timer);
    if (result.error) call.reject(new Error(result.error));
    else call.resolve(result.value);
  });
  function call(method, params = {}) {
    if (closed) return Promise.reject(new Error('Android 原生驱动已关闭'));
    const id = ++seq;
    return new Promise((resolve,reject) => {
      const timer = setTimeout(() => terminate(new Error('Android 原生驱动超时：' + method)), method === 'hello' ? 60000 : 35000);
      pending.set(id, {resolve,reject,timer});
      child.stdin.write(JSON.stringify({id,method,params}) + '\n');
    });
  }
  const close = async () => {
    if (!closed) terminate(new Error('Android 原生驱动已关闭'));
  };
  try {
    const capabilities = await call('hello');
    if (capabilities.deviceUDID !== config.udid) throw new Error('原生驱动连接了不同手机');
    return {
      platform:'android', capabilities, close,
      get needsReconnect() { return closed; },
      findAll: locator => call('findAll', locator),
      click: id => call('click',{id}), clear: id => call('clear',{id}),
      type: (id,text) => call('type',{id,text}), text:id => call('text',{id}),
      attribute:(id,name) => call('attribute',{id,name}), displayed:id => call('displayed',{id}),
      enabled:id => call('enabled',{id}), source:() => call('source'),
      screenshot:async () => checkedPng(Buffer.from(await call('screenshot'), 'base64')),
      mobile:(name,params={}) => call('mobile',{name,...params}),
    };
  } catch (error) {
    await close();
    throw error;
  }
}
