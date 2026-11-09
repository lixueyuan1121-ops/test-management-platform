import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { promisify } from 'node:util';

const run = promisify(execFile);
function boundedInt(key, raw, min, max, fallback) {
  if (raw == null || raw === '') return fallback;
  const n = Number(raw);
  if (!Number.isSafeInteger(n) || n < min || n > max) throw new Error(key + ' 超出范围');
  return n;
}

export function readConfig(env = process.env) {
  const platform = env.MOBILE_PLATFORM?.toLowerCase();
  const androidTransport = env.MOBILE_ANDROID_TRANSPORT || 'appium';
  if (!['native', 'appium'].includes(androidTransport)) throw new Error('MOBILE_ANDROID_TRANSPORT 仅支持 native 或 appium');
  const udid = env.MOBILE_UDID?.trim();
  const appId = env.MOBILE_APP_ID?.trim();
  if (!['android', 'ios'].includes(platform)) throw new Error('需要 MOBILE_PLATFORM=android 或 ios');
  if (!/^[A-Za-z0-9][\w.-]{1,127}$/.test(udid || '') || /^emulator-/i.test(udid))
    throw new Error('需要物理手机的 MOBILE_UDID');
  if (platform === 'ios' && !/^(?:[a-f0-9]{40}|[a-f0-9]{8}-[a-f0-9]{16})$/i.test(udid))
    throw new Error('MOBILE_UDID 不是 iOS 物理设备标识');
  if (!/^[a-zA-Z][\w-]*(?:\.[a-zA-Z0-9_][\w-]*)+$/.test(appId || '')) throw new Error('需要完整包名 MOBILE_APP_ID');
  const appiumUrl = new URL(env.APPIUM_URL || 'http://127.0.0.1:4723');
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(appiumUrl.hostname) || !['http:', 'https:'].includes(appiumUrl.protocol))
    throw new Error('Appium 必须运行在连接该手机的本机');
  const id = platform === 'ios' ? udid.toLowerCase() : udid;
  const wdaUrl = env.MOBILE_WDA_URL ? new URL(env.MOBILE_WDA_URL) : null;
  if (wdaUrl && (wdaUrl.protocol !== 'http:' || !['localhost','127.0.0.1','[::1]'].includes(wdaUrl.hostname)))
    throw new Error('MOBILE_WDA_URL 必须指向绑定该 UDID 的本机 USB 转发端口');
  const deviceId = createHash('sha256').update(platform + ':' + id).digest('hex');
  const systemPort = boundedInt('MOBILE_SYSTEM_PORT', env.MOBILE_SYSTEM_PORT, 1024, 65535);
  const wdaPort = boundedInt('MOBILE_WDA_PORT', env.MOBILE_WDA_PORT, 1024, 65535, 10000 + parseInt(deviceId.slice(0, 5),16) % 9000);
  return {
    platform, udid, appId, deviceId, appiumUrl:appiumUrl.href.replace(/\/$/, ''), adb:env.ADB || 'adb',
    androidTransport, androidPython:env.MOBILE_ANDROID_PYTHON,
    appActivity:env.MOBILE_APP_ACTIVITY, expectedVersion:env.MOBILE_APP_VERSION, expectedBuild:env.MOBILE_APP_BUILD,
    systemPort, wdaPort, wdaUrl:wdaUrl?.href, preinstalledWda:env.MOBILE_USE_PREINSTALLED_WDA === '1',
    xcodeOrgId:env.MOBILE_XCODE_ORG_ID, wdaBundleId:env.MOBILE_WDA_BUNDLE_ID,
    runnerId:env.RUNNER_ID?.trim(), runnerToken:env.RUNNER_TOKEN?.trim(), baseUrl:env.BASE_URL?.replace(/\/$/, ''),
    timeoutMs:boundedInt('MOBILE_TIMEOUT_MS', env.MOBILE_TIMEOUT_MS, 1000, 600000, 90000),
    evidenceDir:env.MOBILE_EVIDENCE_DIR || join(tmpdir(), 'qalab-mobile-evidence'),
    lockPort: 20000 + parseInt(deviceId.slice(0, 8),16) % 20000,
  };
}

function observation(config, model, system, version, build) {
  if (![model,system,version,build].every(value => typeof value === 'string' && value.trim()))
    throw new Error('无法确认实际手机型号、系统或已安装 APP 版本与构建号');
  if (config.expectedVersion && config.expectedVersion !== version) throw new Error('APP 版本不符，实测 ' + version);
  if (config.expectedBuild && config.expectedBuild !== build) throw new Error('APP 构建不符，实测 ' + build);
  return { platform:config.platform, device_id:config.deviceId, physical:true, online:true,
    app_id:config.appId, model, os_version:system, version, build };
}

export function androidObservation(config, devPath, properties, packageInfo) {
  if (!/^usb:\S+$/i.test(devPath.trim())) throw new Error('无法确认此 Android 手机经 USB 连接');
  const props = Object.fromEntries([...properties.matchAll(/^\[([^\]]+)\]: \[([^\]]*)\]/gm)].map(([,key,value]) => [key,value]));
  if (['ro.kernel.qemu','ro.boot.qemu'].some(k => ['1','true'].includes(props[k])) ||
      /goldfish|ranchu|vbox|emulator|sdk_gphone|genymotion/i.test([props['ro.hardware'], props['ro.product.model'], props['ro.product.device']].join(' ')))
    throw new Error('拒绝使用 Android 模拟器');
  return observation(config, props['ro.product.model'], props['ro.build.version.release'],
    /\bversionName=(\S+)/.exec(packageInfo)?.[1], /\bversionCode=(\d+)/.exec(packageInfo)?.[1]);
}

export async function inspectAndroid(config) {
  async function adb(...args) {
    const { stdout } = await run(config.adb, ['-s',config.udid,...args], {timeout:12000,maxBuffer:8*1024*1024,windowsHide:true});
    return stdout.trim();
  }
  const [path,props,pkg] = await Promise.all([adb('get-devpath'), adb('shell','getprop'), adb('shell','dumpsys','package',config.appId)]);
  return androidObservation(config,path,props,pkg);
}

export async function androidScreenshot(config) {
  const {stdout} = await run(config.adb, ['-s', config.udid, 'exec-out', 'screencap', '-p'], {
    timeout: 10000,
    maxBuffer: 24 * 1024 * 1024,
    encoding: 'buffer',
    windowsHide: true,
  });
  return stdout;
}

export function iosObservation(config, info, apps, caps, hostDevice) {
  const simulation = info?.isSimulator;
  const usbVerified = !!hostDevice && hostDevice.udid?.toLowerCase() === config.udid.toLowerCase();
  if (![0, false, '0', 'false'].includes(simulation) && !(simulation == null && usbVerified))
    throw new Error('未确认 iOS 真机身份');
  const app = apps?.[config.appId];
  return observation(config, hostDevice?.model || info?.ProductType || info?.productType || info?.model, hostDevice?.version || caps.platformVersion || caps['appium:platformVersion'] || info?.ProductVersion,
    app?.CFBundleShortVersionString, app?.CFBundleVersion);
}

export async function findPhysicalIOS(udid) {
  const options = {timeout:15000,maxBuffer:4*1024*1024};
  try {
    const list = await run('idevice_id',['-l'],options);
    if (list.stdout.split(/\s+/).some(line => line.toLowerCase() === udid.toLowerCase())) {
      const get = async key => (await run('ideviceinfo',['-u',udid,'-k',key],options)).stdout.trim();
      return {udid,model:await get('ProductType'),version:await get('ProductVersion')};
    }
  } catch { /* Xcode can also attest a USB phone when libimobiledevice is unavailable. */ }
  const directory = await mkdtemp(join(tmpdir(),'qalab-devicectl-'));
  try {
    const out = join(directory,'devices.json');
    await run('xcrun',['devicectl','list','devices','--json-output',out],options);
    const devices = JSON.parse(await readFile(out,'utf8')).result?.devices || [];
    for (const device of devices) {
      const hw = device.hardwareProperties || {}, link = device.connectionProperties || {};
      if (hw.udid?.toLowerCase() === udid.toLowerCase() && hw.reality === 'physical' && ['wired','usb'].includes(link.transportType?.toLowerCase()))
        return {udid,model:hw.productType,version:device.deviceProperties?.osVersionNumber};
    }
    throw new Error('Xcode 未发现指定 UDID 的 USB iPhone');
  } finally { await rm(directory,{recursive:true,force:true}); }
}

export async function inspectIOS(config, driver) {
  const info = await driver.mobile('deviceInfo');
  const apps = await driver.mobile('listApps', {returnAttributes:['CFBundleIdentifier','CFBundleVersion','CFBundleShortVersionString']});
  const host = info?.isSimulator == null || config.wdaUrl ? await findPhysicalIOS(config.udid) : undefined;
  return iosObservation(config, info, apps, driver.capabilities, host);
}

export function checkTarget(target, actual, runnerId) {
  if (!target || target.runner_id !== runnerId) throw new Error('执行机归属与任务快照不匹配');
  for (const key of ['device_id','platform','app_id','build']) {
    if (!target[key] || target[key] !== actual[key]) throw new Error('真机任务快照不符：' + key);
  }
}
