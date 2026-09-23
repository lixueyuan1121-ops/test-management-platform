const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { connectDesktopCDP, describeCDP } = require('../src/cdp-connect');

test('cold-start timeout retries compatibility attachment and returns the usable connection', async t => {
  t.mock.method(global, 'fetch', async () => ({ ok: true, json: async () => ({}) }));
  const calls = [], warnings = [], browser = {};
  t.mock.method(chromium, 'connectOverCDP', async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) throw new Error('Timeout 30000ms exceeded.\n<ws connected>');
    return browser;
  });
  assert.equal(await connectDesktopCDP('http://127.0.0.1:9222', { product: '纳米Work', retryDelay: 0, logger: { warn: m => warnings.push(m) } }), browser);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.timeout, 45000);
  assert.equal(calls[0].options.noDefaults, undefined);
  assert.equal(calls[1].options.noDefaults, true);
  assert.equal(warnings.length, 1);
});

test('persistent timeout is bounded, reports product/endpoint/original error, never reports success', async t => {
  t.mock.method(global, 'fetch', async () => ({ ok: true, json: async () => ({}) }));
  let calls = 0;
  t.mock.method(chromium, 'connectOverCDP', async () => { calls++; throw new Error('Timeout 45000ms exceeded.\n<ws connected>'); });
  await assert.rejects(connectDesktopCDP('http://127.0.0.1:9335', { product: 'WorkBuddy', retryDelay: 0 }), error => {
    assert.match(error.message, /WorkBuddy.*9335.*尝试 2 次/);
    assert.match(error.message, /尚未发送测评提问/);
    assert.match(error.message, /<ws connected>/);
    return true;
  });
  assert.equal(calls, 2);
});

test('authentication/protocol errors are not retried', async t => {
  t.mock.method(global, 'fetch', async () => ({ ok: true, json: async () => ({}) }));
  let calls = 0;
  t.mock.method(chromium, 'connectOverCDP', async () => { calls++; throw new Error('HTTP 401 Unauthorized'); });
  await assert.rejects(connectDesktopCDP('http://localhost:9222', { retryDelay: 0 }), /401 Unauthorized/);
  assert.equal(calls, 1);
});


test('diagnostics identify kernel and target counts without exposing task content or URLs', async t => {
  t.mock.method(global, 'fetch', async url => ({ ok: true, json: async () =>
    url.pathname.endsWith('version') ? { Browser: 'Chrome/148.0.7778.280' } : [
      { type: 'page', title: 'private task', url: 'https://secret/?token=private' }, { type: 'page' }, { type: 'service_worker' },
    ] }));
  const result = await describeCDP('http://localhost:9222');
  assert.match(result, /Chrome\/148/);
  assert.match(result, /"page":2/);
  assert.doesNotMatch(result, /private|secret/);
});
