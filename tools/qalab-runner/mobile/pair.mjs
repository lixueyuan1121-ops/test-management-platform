import {execFile, spawn} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseEnv, promisify} from 'node:util';

const exec = promisify(execFile);
const platforms = ['android','ios'];
const usage = 'node mobile/pair.mjs doctor | direct [send|followup|stop|history|all] | direct script <steps.json> | serve';
export async function discoverPhone(platform, run = async (bin,args) =>
  (await exec(bin, args, {timeout:15000})).stdout) {
  const output = platform === 'android' ? await run('adb',['devices','-l']) : await run('idevice_id',['-l']);
  const lines = output.trim().split(/\r?\n/).map(line => line.trim());
  const phones = platform === 'android'
    ? lines.filter(line => /^\S+\s+device\s.*\busb:\S+/.test(line) && !line.startsWith('emulator-')).map(line => line.split(/\s/)[0])
    : lines.filter(line => /^(?:[a-f\d]{40}|[a-f\d]{8}-[a-f\d]{16})$/i.test(line));
  if (phones.length !== 1) throw new Error(platform + ' 有 ' + phones.length + ' 台已授权 USB 真机，请连接一台或在该端配置中指定 MOBILE_UDID');
  return phones[0];
}

async function main(args) {
  if (args.includes('--help')) {console.log(usage); return;}
  if (!['doctor','direct','serve'].includes(args[0])) throw new Error(usage);
  const configs = await Promise.all(platforms.map(async platform => {
    const path = process.env['MOBILE_' + platform.toUpperCase() + '_ENV'];
    const local = path ? parseEnv(await readFile(path,'utf8')) : {};
    const env = {...process.env, ...local, MOBILE_PLATFORM:platform};
    env.MOBILE_APP_ID ||= 'com.qihoo.namiwork';
    env.MOBILE_UDID = local.MOBILE_UDID || await discoverPhone(platform);
    if (args[0] === 'serve' && (!env.RUNNER_TOKEN || !env.RUNNER_ID || !env.BASE_URL))
      throw new Error(platform + ' 缺少执行设备配置，请指定 MOBILE_' + platform.toUpperCase() + '_ENV');
    return env;
  }));
  if (args[0] === 'serve' && (configs[0].RUNNER_ID === configs[1].RUNNER_ID || configs[0].RUNNER_TOKEN === configs[1].RUNNER_TOKEN))
    throw new Error('每台真机需要独立登记的执行设备和 token');
  const entry = fileURLToPath(new URL('./runner.mjs', import.meta.url));
  const children = configs.map(env => spawn(process.execPath,[entry,...args],{env,stdio:'inherit'}));
  const stop = () => children.forEach(child => {if (child.exitCode === null) child.kill('SIGTERM');});
  process.once('SIGINT',stop);
  process.once('SIGTERM',stop);
  const codes = await Promise.all(children.map(child => new Promise(done => {
    child.once('exit', code => done(code ?? 1));
    child.once('error', e => {console.error(e.message); done(1);});
  })));
  process.exitCode = codes.some(code => code !== 0) ? 1 : 0;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch(e => {console.error(e.message); process.exitCode=1;});
}
