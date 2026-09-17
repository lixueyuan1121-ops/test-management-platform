const { test } = require('node:test');
const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const { EventEmitter } = require('node:events');
const { chromium } = require('playwright');
const { QworkPool } = require('../src/qwork-pool');

test('QWork 使用独立端口及启动参数，不走 WorkBuddy 环境变量或关闭其他产品', async t => {
  let invocation, readiness = 0;
  const child = new EventEmitter(); child.unref = () => {};
  t.mock.method(childProcess, 'spawn', (...args) => { invocation = args; queueMicrotask(() => child.emit('spawn')); return child; });
  const page = { evaluate: async () => true };
  let closed = false;
  t.mock.method(chromium, 'connectOverCDP', async url => {
    assert.equal(url, 'http://127.0.0.1:9336');
    return { contexts: () => [{ pages: () => [page] }], close: async () => { closed = true; } };
  });
  const pool = new QworkPool({ executablePath: process.execPath });
  pool._ready = async () => ++readiness > 1;
  await pool.init();
  assert.equal(invocation[0], process.execPath);
  assert.deepEqual(invocation[1], ['--remote-debugging-port=9336', '--remote-debugging-address=127.0.0.1', '--inspect=127.0.0.1:9337']);
  assert.strictEqual(invocation[2].env, process.env);
  assert.equal(pool.getMainPage(), page);
  await pool.close(); assert(closed);
});

test('异步启动错误可控返回，不使 runner 崩溃', async t => {
  const child = new EventEmitter(); child.unref = () => {};
  t.mock.method(childProcess, 'spawn', () => { queueMicrotask(() => child.emit('error', new Error('EACCES'))); return child; });
  const pool = new QworkPool({ executablePath: process.execPath }); pool._ready = async () => false;
  await assert.rejects(pool.init(), /EACCES/);
});

test('端口属于其他客户端时拒绝下发，不使用其页面', async t => {
  let closed = false;
  t.mock.method(chromium, 'connectOverCDP', async () => ({
    contexts: () => [{ pages: () => [{ evaluate: async () => false }] }], close: async () => { closed = true; },
  }));
  const pool = new QworkPool({ readyTimeout: 1 }); pool._ready = async () => true;
  await assert.rejects(pool.init(), /不是.*QWork/);
  assert(closed); assert.equal(pool.getMainPage(), undefined);
});
