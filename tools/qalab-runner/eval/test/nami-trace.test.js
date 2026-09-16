const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { createHash } = require('node:crypto');
const { chromium } = require('playwright');
const { attachNamiTrace } = require('../src/nami-trace');
const DesktopRunner = require('../src/desktop-runner');

let browser, page, trace;
before(async () => { browser = await chromium.launch({ headless: true,
  args: ['--disable-features=LocalNetworkAccessChecks'] }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage();
  await page.route('http://*.work.n.cn/**', route => route.fulfill({
    contentType: 'text/html', body: '<openclaw-app></openclaw-app>',
  }));
});
afterEach(async () => { await trace?.dispose(); trace = null; await page.close(); });

async function bridge(frame = page) {
  await frame.evaluate(() => {
    // 对应真机 ClawFallbackSocket：EventTarget，通过原生桥接派发 message，无浏览器 WS。
    window.WebSocket = class ClawFallbackSocket extends EventTarget {
      constructor() { super(); this.readyState = 1; }
      close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
    };
    window.originalFactory = window.WebSocket;
    const app = document.querySelector('openclaw-app');
    app.client = { ws: new WebSocket() };
    window.appMessages = 0;
    app.client.ws.addEventListener('message', () => window.appMessages++);
  });
}
async function fixture() {
  await page.goto('http://device.work.n.cn/');
  await bridge();
  trace = await attachNamiTrace(page);
  await trace.ensureReady();
}
const agent = (stream, data) => ({ type: 'event', event: 'agent',
  payload: { sessionId: 'synthetic-session', runId: 'model-run', stream, data } });
async function emit(frames, frame = page) {
  await frame.evaluate(frames => {
    const ws = document.querySelector('openclaw-app').client.ws;
    for (const data of frames) ws.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) }));
  }, frames);
}

test('already-open native bridge captures protocol thinking, tool results and final answer without reload', async () => {
  let browserSockets = 0;
  page.on('websocket', () => browserSockets++);
  await fixture();
  await emit([
    agent('thinking', { text: '先计算' }),
    agent('tool', { toolCallId: 't', name: 'exec', originalToolName: 'mcp__math__exec', phase: 'start', args: { command: '37*41' } }),
    agent('tool', { toolCallId: 't', phase: 'result', result: '1517' }),
    agent('tool', { toolCallId: 't', phase: 'result', result: {} }),
    { type: 'event', event: 'chat', payload: { state: 'final', message: { content: [{ type: 'text', text: '1517' }] } } },
  ]);
  const t = await trace.buildTrace(7);
  assert.equal(browserSockets, 0);
  assert.equal(t.capture_source, 'nami_gateway');
  assert.equal(t.thinking, '先计算');
  assert.equal(t.answer, '1517');
  assert.equal(t.session_id, 'synthetic-session');
  assert.equal(t.run_id, 7);
  assert.equal(t.tool_calls.length, 1);
  assert.equal(t.tool_calls[0].result_text, '1517');
  assert.equal(t.tool_calls[0].mcp_server, 'math');
  assert.equal(t.capture_health.status, 'captured');
  assert.equal(await page.evaluate(() => window.appMessages), 5, '业务监听器继续收到全部消息');
});

test('reconnect captures the first synchronous frame from the replacement socket', async () => {
  await fixture();
  const data = agent('thinking', { text: '重连首帧' });
  await page.evaluate(data => {
    const app = document.querySelector('openclaw-app');
    app.client.ws.close();
    app.client.ws = new WebSocket();
    app.client.ws.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) }));
  }, data);
  assert.equal((await trace.buildTrace()).thinking, '重连首帧');
});

test('continuation keeps both rounds of tools while a shorter final answer replaces the initial response', async () => {
  await fixture();
  const final = (runId, text) => ({ type: 'event', event: 'chat', payload: {
    runId, sessionId: 'synthetic-session', state: 'final', message: { content: [{ type: 'text', text }] },
  } });
  await emit([agent('tool', { toolCallId: 'before', name: 'search', phase: 'result', result: 'first' }),
    final('model-run', '第一轮很长的说明，还没有完成文件交付。')]);
  await trace.beginContinuation();
  const tool = agent('tool', { toolCallId: 'after', name: 'write_file', phase: 'result', result: 'file.txt' });
  tool.payload.runId = 'continued-run';
  await emit([tool, final('continued-run', '已交付'), final('model-run', '第一轮迟到的更长文字，不应该覆盖继续工作后的最终结果。')]);
  const result = await trace.buildTrace(9);
  assert.equal(result.answer, '已交付');
  assert.deepEqual(result.tool_calls.map(t => t.tool_call_id), ['before', 'after']);
  assert.equal(result.capture_health.status, 'captured');
  await trace.reset();
  await emit([final('model-run', '新的测评')]);
  assert.equal((await trace.buildTrace()).answer, '新的测评', 'next test must reset the continuation boundary');
});

test('reset discards queued old frames while keeping the live socket ready for the next turn', async () => {
  await fixture();
  await emit([agent('thinking', { text: '上一轮' })]);
  await trace.reset();
  await trace.ensureReady();
  assert.equal((await trace.buildTrace()).thinking, '');
  await emit([agent('thinking', { text: '下一轮' })]);
  assert.equal((await trace.buildTrace()).thinking, '下一轮');
});

test('navigation to another device reattaches and does not carry old turn data after reset', async () => {
  await fixture();
  await emit([agent('thinking', { text: '旧设备' })]);
  await page.goto('http://another.work.n.cn/');
  await bridge();
  await trace.reset();
  await trace.ensureReady();
  await emit([agent('thinking', { text: '新设备' })]);
  assert.equal((await trace.buildTrace()).thinking, '新设备');
});

test('gateway in a work iframe is captured and flushed before buildTrace returns', async () => {
  await page.setContent('<iframe src="http://device.work.n.cn/embedded"></iframe>');
  const frame = page.frames().find(f => f.url().includes('device.work.n.cn'));
  await frame.waitForSelector('openclaw-app', { state: 'attached' });
  await bridge(frame);
  trace = await attachNamiTrace(page);
  await trace.ensureReady();
  await emit([agent('assistant', { text: 'iframe 最后一帧' })], frame);
  assert.equal((await trace.buildTrace()).answer, 'iframe 最后一帧');
});

test('frames from a replaced or unrelated socket are excluded', async () => {
  await fixture();
  await page.evaluate(data => {
    const app = document.querySelector('openclaw-app');
    const old = app.client.ws;
    app.client.ws = new WebSocket();
    old.dispatchEvent(new MessageEvent('message', { data }));
    new WebSocket().dispatchEvent(new MessageEvent('message', { data }));
  }, JSON.stringify(agent('thinking', { text: '不属于当前连接' })));
  assert.equal((await trace.buildTrace()).thinking, '');
});

test('missing gateway fails preflight with an actionable error', async () => {
  await page.goto('http://device.work.n.cn/');
  trace = await attachNamiTrace(page);
  await assert.rejects(trace.ensureReady({ timeoutMs: 20 }), /TRACE_CAPTURE_UNAVAILABLE.*本轮尚未发送/);
});

test('desktop execution does not type or click send when trace preflight fails', async () => {
  const runner = Object.create(DesktopRunner.prototype);
  let inputActions = 0;
  runner.platform = {};
  runner._focus = async () => {};
  runner._fl = () => ({});
  runner.dr = {
    execution: {}, _captureBaseline: async () => ({}),
    _beginTurn: require('../src/dialog-runner').prototype._beginTurn, _applyDialogOptions: async () => {},
    _ctx: () => ({ locator() { inputActions++; throw new Error('must not reach input'); } }),
  };
  runner.beforeSend = async () => { throw new Error('[TRACE_CAPTURE_UNAVAILABLE] test'); };
  await assert.rejects(runner._sendOne({ caseId: 'synthetic', question: '不要发送' }), /TRACE_CAPTURE_UNAVAILABLE/);
  assert.equal(inputActions, 0);
});

test('dispose restores the original factory and removes capture state without touching business listeners', async () => {
  await fixture();
  await trace.dispose();
  const state = await page.evaluate(() => ({ factoryRestored: window.WebSocket === window.originalFactory,
    keys: Object.keys(window).filter(k => k.startsWith('__qalabNamiTrace_')) }));
  assert.equal(state.factoryRestored, true);
  assert.deepEqual(state.keys, []);
  await emit([agent('thinking', { text: '结束后的业务消息' })]);
  assert.equal(await page.evaluate(() => window.appMessages), 1);
  assert.equal((await trace.buildTrace()).thinking, '');
});

test('bounded buffer reports dropped frames instead of claiming a complete trace', async () => {
  await fixture();
  await emit(Array.from({ length: 2001 }, () => agent('assistant', { text: 'bounded' })));
  const t = await trace.buildTrace();
  assert.equal(t.capture_health.status, 'incomplete');
  assert.equal(t.capture_health.dropped_frames, 1);
});

test('build/reset operations cannot asynchronously put an old turn into the new turn', async () => {
  await fixture();
  await emit([agent('thinking', { text: '旧轮次' })]);
  const [previous] = await Promise.all([trace.buildTrace(), trace.reset()]);
  assert.equal(previous.thinking, '旧轮次');
  assert.equal((await trace.buildTrace()).thinking, '');
});

test('real browser WebSocket remains compatible and is not counted twice', { timeout: 10000 }, async () => {
  const server = createServer();
  const connections = [];
  server.on('upgrade', (req, socket) => {
    connections.push(socket);
    const accept = createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.goto('http://device.work.n.cn/');
    trace = await attachNamiTrace(page);
    await page.evaluate(port => new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://127.0.0.1:${port}`);
      document.querySelector('openclaw-app').client = { ws };
      ws.addEventListener('open', resolve);
      ws.addEventListener('error', () => reject(new Error('fixture websocket connection failed')));
    }), server.address().port);
    await trace.ensureReady();
    const data = Buffer.from(JSON.stringify(agent('tool', { toolCallId: 'native', phase: 'result', name: 'math', result: '1517' })));
    const header = Buffer.alloc(4); header[0] = 0x81; header[1] = 126; header.writeUInt16BE(data.length, 2);
    connections[0].write(Buffer.concat([header, data]));
    await page.waitForTimeout(100);
    const t = await trace.buildTrace();
    assert.equal(t.capture_source, 'nami_gateway');
    assert.equal(t.capture_health.browser_connected, true);
    assert.equal(t.tool_calls.length, 1);
    assert.equal(t.capture_diagnostics.frames, 1);
    assert.equal(t.tool_calls[0].result_text, '1517');
    // 客户端未暴露 gateway 对象时仍保留浏览器 WS 路径；reset 不应忘记已确认的协议连接。
    await page.evaluate(() => { document.querySelector('openclaw-app').client = null; });
    await trace.reset();
    await trace.ensureReady();
    connections[0].write(Buffer.concat([header, data]));
    await page.waitForTimeout(100);
    const fallback = await trace.buildTrace();
    assert.equal(fallback.capture_source, 'browser_websocket');
    assert.equal(fallback.tool_calls[0].result_text, '1517');
  } finally {
    connections.forEach(socket => socket.destroy());
    await new Promise(resolve => server.close(resolve));
  }
});
