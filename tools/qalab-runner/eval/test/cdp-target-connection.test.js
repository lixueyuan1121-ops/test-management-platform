const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtemp, readFile, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { chromium } = require('playwright');
const { ws: WebSocket, wsServer: WebSocketServer } = require('playwright-core/lib/utilsBundle');
const { connectProductPages } = require('../src/cdp-target-connection');
const http = require('node:http');

// Real Chromium behind a fault-injection CDP relay. No external sites or user apps.
test('one frozen page blocks ordinary attachment but responsive product pages remain usable', { timeout: 30000 }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'qalab-cdp-target-'));
  let context, attached, relay;
  const sockets = new Set(), commands = [], warnings = [];
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ Browser: 'test', webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/relay` }));
  });
  const wss = new WebSocketServer({ server, path: '/relay' });
  try {
    context = await chromium.launchPersistentContext(dir, { headless: true, args: ['--remote-debugging-port=0'] });
    const good = context.pages()[0];
    await good.goto('data:text/html,product-good');
    await good.setContent('<input id="question"><iframe srcdoc="<p>embedded chat</p>"></iframe>');
    for (const name of ['unrelated-hidden', 'product-frozen']) {
      const page = await context.newPage(); await page.goto(`data:text/html,${name}`);
    }
    const [port, path] = (await readFile(join(dir, 'DevToolsActivePort'), 'utf8')).trim().split('\n');
    const realEndpoint = `ws://127.0.0.1:${port}${path}`;
    wss.on('connection', downstream => {
      const upstream = new WebSocket(realEndpoint); sockets.add(downstream); sockets.add(upstream);
      const frozen = new Set(), queued = [];
      downstream.on('error', () => {}); upstream.on('error', () => {});
      downstream.on('close', () => upstream.terminate()); upstream.on('close', () => downstream.close());
      downstream.on('message', data => {
        const message = JSON.parse(data); commands.push(message.method);
        // Simulates a live browser process whose hidden renderer never replies.
        if (frozen.has(message.sessionId) && /^(Page\.|Runtime.evaluate)/.test(message.method)) return;
        if (upstream.readyState === WebSocket.OPEN) upstream.send(data.toString()); else queued.push(data.toString());
      });
      upstream.on('open', () => queued.forEach(data => upstream.send(data)));
      upstream.on('message', data => {
        const message = JSON.parse(data);
        if (message.method === 'Target.attachedToTarget' && /frozen|hidden/.test(message.params.targetInfo.url)) frozen.add(message.params.sessionId);
        if (downstream.readyState === WebSocket.OPEN) downstream.send(data.toString());
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const url = `http://127.0.0.1:${server.address().port}`;
    await assert.rejects(chromium.connectOverCDP(url, { timeout: 1500 }), /Timeout/);
    attached = await connectProductPages(chromium, url, {
      matches: url => url.includes('product-'), timeout: 8000, probeTimeout: 250, waitTimeout: 500,
      logger: { info() {}, warn: message => warnings.push(message) },
    });
    const pages = attached.contexts().flatMap(context => context.pages());
    assert.equal(pages.length, 1);
    assert.match(pages[0].url(), /product-good/);
    await pages[0].locator('#question').fill('runner can send a question');
    assert.equal(await good.locator('#question').inputValue(), 'runner can send a question');
    assert.equal(await pages[0].frameLocator('iframe').locator('p').innerText(), 'embedded chat');
    assert(warnings.some(message => /Page.getFrameTree/.test(message)));
    assert.equal(context.pages().length, 3, 'unresponsive/unrelated windows remain open');
    assert(!commands.some(method => ['Target.closeTarget', 'Page.reload', 'Browser.close'].includes(method)));
    await attached.close(); attached = null;
    assert.equal(context.pages().length, 3, 'disconnect does not close client windows');
    await assert.rejects(connectProductPages(chromium, url, {
      matches: url => url.includes('product-frozen'), probeTimeout: 100, waitTimeout: 100,
    }), /主页面均未响应.*Page.getFrameTree/);
    await assert.rejects(connectProductPages(chromium, url, {
      matches: () => false, probeTimeout: 100, waitTimeout: 100,
    }), /未发现纳米Work主页面/);
  } finally {
    await attached?.close();
    for (const socket of sockets) socket.terminate();
    wss.close(); await new Promise(resolve => server.close(resolve));
    await context?.close(); await rm(dir, { recursive: true, force: true });
  }
});
