const { test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const { chromium } = require('playwright');
const { resolveExecutable } = require('../src/electron-executable');
const { WorkbuddyPool } = require('../src/workbuddy-pool');

let temp;
beforeEach(() => { temp = fs.mkdtempSync(path.join(os.tmpdir(), 'workbuddy-launch-')); });
afterEach(() => { fs.rmSync(temp, { recursive: true, force: true }); });

function executable(file, content = '#!/bin/sh\nexit 0\n') {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content, { mode: 0o755 });
  return file;
}
function bundle(name = 'Work Buddy.app', binary = 'Electron') {
  const app = path.join(temp, name);
  const file = executable(path.join(app, 'Contents', 'MacOS', binary));
  return { app, file };
}
function poolFor(file) {
  const logs = [];
  const pool = new WorkbuddyPool({ executablePath: file, killExisting: true }, { info: m => logs.push(m) });
  return { pool, logs };
}
function childStub() {
  const child = new EventEmitter();
  child.unref = () => { child.unreferenced = true; };
  return child;
}

test('.app directory with spaces and trailing slash resolves to its Electron binary', () => {
  const { app, file } = bundle();
  assert.equal(resolveExecutable(app + path.sep), file);
  assert.equal(resolveExecutable(file), file);
});

test('app-named binary wins over helper files and Electron fallback', () => {
  const { app, file } = bundle('Example.app', 'Example');
  executable(path.join(app, 'Contents', 'MacOS', 'Electron'));
  executable(path.join(app, 'Contents', 'MacOS', 'A Helper'));
  assert.equal(resolveExecutable(app), file);
});

test('macOS bundle metadata resolves a renamed main executable', { skip: process.platform !== 'darwin' }, () => {
  const { app, file } = bundle('Example.app', 'Renamed Launcher');
  executable(path.join(app, 'Contents', 'MacOS', 'Example'));
  fs.writeFileSync(path.join(app, 'Contents', 'Info.plist'),
    '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleExecutable</key><string>Renamed Launcher</string></dict></plist>');
  assert.equal(resolveExecutable(app), file);
});

test('empty, missing, directory, broken and ambiguous bundle paths give clear errors', () => {
  assert.throws(() => resolveExecutable(' '), /未配置/);
  assert.throws(() => resolveExecutable(path.join(temp, 'missing.app')), /不存在/);
  assert.throws(() => resolveExecutable(temp), /不能直接启动目录/);
  const empty = path.join(temp, 'Empty.app');
  fs.mkdirSync(empty);
  assert.throws(() => resolveExecutable(empty), /无法确定应用主程序/);
  const { app } = bundle('Ambiguous.app', 'Candidate A');
  executable(path.join(app, 'Contents', 'MacOS', 'Candidate B'));
  assert.throws(() => resolveExecutable(app), /无法确定应用主程序/);
});

test('non-executable file reports EACCES before starting anything', { skip: process.platform === 'win32' }, () => {
  const file = path.join(temp, 'NoPermission');
  fs.writeFileSync(file, 'not executable');
  assert.throws(() => resolveExecutable(file), /没有执行权限（EACCES）/);
});

test('invalid configuration is rejected before stopping the existing client', async () => {
  const { pool, logs } = poolFor(path.join(temp, 'Missing.app'));
  pool._portReady = async () => false;
  let killed = false;
  pool._killExisting = () => { killed = true; };
  await assert.rejects(pool.init(), /路径不存在/);
  assert.equal(killed, false);
  assert.equal(pool._launched, false);
  assert.equal(logs.some(m => m.includes('已启动')), false);
});

test('spawn waits for success, uses the resolved file and keeps the debug-port environment', async t => {
  const { app, file } = bundle();
  const { pool, logs } = poolFor(app);
  const child = childStub();
  let invocation;
  t.mock.method(childProcess, 'spawn', (...args) => { invocation = args; return child; });
  const starting = pool._spawnClient();
  assert.equal(pool._launched, false);
  assert.equal(logs.some(m => m.includes('已启动')), false);
  child.emit('spawn');
  await starting;
  assert.equal(invocation[0], file);
  assert.deepEqual(invocation[1], []);
  assert.equal(invocation[2].env.WORKBUDDY_REMOTE_DEBUGGING_PORT, '9335');
  assert.equal(invocation[2].detached, true);
  assert.equal(child.unreferenced, true);
  assert.equal(pool._launched, true);
  assert.equal(logs.filter(m => m.includes('已启动')).length, 1);
});

test('native asynchronous spawn ENOENT is caught instead of becoming an unhandled error', async t => {
  const nativeSpawn = childProcess.spawn;
  const { pool, logs } = poolFor(process.execPath);
  t.mock.method(childProcess, 'spawn', () => nativeSpawn(path.join(temp, 'missing-executable'), [], { stdio: 'ignore' }));
  await assert.rejects(pool._spawnClient(), /WorkBuddy 启动失败（ENOENT）/);
  assert.equal(pool._launched, false);
  assert.equal(logs.some(m => m.includes('已启动')), false);
});

test('native asynchronous spawn EACCES is caught and init never waits for the port', { skip: process.platform === 'win32' }, async t => {
  const nativeSpawn = childProcess.spawn;
  const denied = path.join(temp, 'Denied');
  fs.writeFileSync(denied, 'not executable');
  const { pool, logs } = poolFor(process.execPath);
  pool._portReady = async () => false;
  pool._killExisting = () => {};
  pool._sleep = async () => {};
  pool._waitPort = async () => { assert.fail('spawn failure must skip port waiting'); };
  t.mock.method(childProcess, 'spawn', () => nativeSpawn(denied, [], { stdio: 'ignore' }));
  await assert.rejects(pool.init(), /WorkBuddy 启动失败（EACCES）/);
  assert.equal(pool._launched, false);
  assert.equal(logs.some(m => m.includes('已启动')), false);
});

test('synchronous spawn failure is also reported through init', async t => {
  const { pool } = poolFor(process.execPath);
  t.mock.method(childProcess, 'spawn', () => { throw new Error('synthetic spawn failure'); });
  await assert.rejects(pool._spawnClient(), /WorkBuddy 启动失败.*synthetic spawn failure/);
  assert.equal(pool._launched, false);
});

test('early process exit is reported without waiting through the launch timeout', async t => {
  const { pool } = poolFor(process.execPath);
  const child = childStub();
  t.mock.method(childProcess, 'spawn', () => { queueMicrotask(() => child.emit('spawn')); return child; });
  await pool._spawnClient();
  child.emit('exit', 7, null);
  pool._portReady = async () => { assert.fail('should fail immediately'); };
  await assert.rejects(pool._waitPort(75000), /启动进程已退出（退出码 7）/);
  assert.equal(pool._launched, false);
});

test('errors after spawn are handled while waiting for CDP', async t => {
  const { pool } = poolFor(process.execPath);
  const child = childStub();
  t.mock.method(childProcess, 'spawn', () => { queueMicrotask(() => child.emit('spawn')); return child; });
  await pool._spawnClient();
  child.emit('error', Object.assign(new Error('late failure'), { code: 'EIO' }));
  await assert.rejects(pool._waitPort(75000), /启动失败（EIO）/);
});

test('already-open CDP attaches even if the local launch path is unavailable', async t => {
  const { pool } = poolFor(path.join(temp, 'NotInstalled.app'));
  pool._portReady = async () => true;
  pool._killExisting = () => { assert.fail('must not stop a ready client'); };
  pool._spawnClient = () => { assert.fail('must not spawn a ready client'); };
  const page = { locator: () => ({ first: () => ({ waitFor: async () => {} }) }), url: () => 'file:///synthetic/index.html' };
  const context = { grantPermissions: async () => {}, pages: () => [page] };
  t.mock.method(chromium, 'connectOverCDP', async () => ({ contexts: () => [context] }));
  await pool.init();
  assert.equal(pool.getMainPage(), page);
  assert.equal(pool._launched, false);
});

test('initialization resolves the app before stopping it and awaits spawn before CDP', async t => {
  const { app, file } = bundle();
  const { pool } = poolFor(app);
  const events = [];
  pool._portReady = async () => false;
  pool._sleep = async () => {};
  pool._killExisting = () => { assert.equal(pool.executablePath, file); events.push('stop'); };
  const child = childStub();
  t.mock.method(childProcess, 'spawn', () => {
    queueMicrotask(() => { events.push('spawn'); child.emit('spawn'); });
    return child;
  });
  pool._waitPort = async () => { assert.equal(pool._launched, true); events.push('port'); };
  const page = { locator: () => ({ first: () => ({ waitFor: async () => {} }) }), url: () => 'file:///synthetic/index.html' };
  t.mock.method(chromium, 'connectOverCDP', async () => {
    events.push('attach');
    return { contexts: () => [{ grantPermissions: async () => {}, pages: () => [page] }] };
  });
  await pool.init();
  assert.deepEqual(events, ['stop', 'spawn', 'port', 'attach']);
  assert.equal(pool.getMainPage(), page);
});
