'use strict';
// Scope a CDP attachment to responsive product pages. Never close/reload a target.
// Playwright's ordinary attachment waits for EVERY existing page to initialize.
const { randomBytes } = require('node:crypto');
const { setTimeout: pause } = require('node:timers/promises');
const { ws: WebSocket, wsServer: WebSocketServer } = require('playwright-core/lib/utilsBundle');

async function openSocket(endpoint, timeout = 5000) {
  const socket = new WebSocket(endpoint, { handshakeTimeout: timeout });
  await new Promise((resolve, reject) => {
    socket.once('open', resolve);
    socket.once('error', reject);
  });
  return socket;
}

function rpc(socket, timeout) {
  let next = 0;
  const pending = new Map();
  socket.on('message', data => {
    let message;
    try { message = JSON.parse(data); } catch { return; }
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id); clearTimeout(request.timer);
    message.error ? request.reject(new Error(`${request.method}: ${message.error.message}`)) : request.resolve(message.result);
  });
  socket.on('close', () => {
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error(`${request.method}: CDP disconnected`)); }
    pending.clear();
  });
  socket.on('error', () => {});
  return (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++next;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method}: ${timeout}ms 内未响应`)); }, timeout);
    pending.set(id, { resolve, reject, timer, method });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}

async function probePages(endpoint, matches, { timeout = 4000, waitTimeout = 15000, logger } = {}) {
  const socket = await openSocket(endpoint);
  const send = rpc(socket, timeout);
  try {
    const deadline = Date.now() + waitTimeout;
    let targets;
    do {
      ({ targetInfos: targets } = await send('Target.getTargets'));
      if (targets.some(target => target.type === 'page' && matches(target.url))) break;
      await pause(500);
    } while (Date.now() < deadline);
    const candidates = targets.filter(target => target.type === 'page' && matches(target.url));
    if (!candidates.length) throw new Error('未发现纳米Work主页面。请确认客户端已登录并进入主界面；未接管其他窗口。');
    const results = await Promise.all(candidates.map(async target => {
      let sessionId;
      try {
        ({ sessionId } = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true }));
        await send('Page.getFrameTree', {}, sessionId);
        const result = await send('Runtime.evaluate', { expression: '1 + 1', returnByValue: true }, sessionId);
        if (result.result?.value !== 2) throw new Error('Runtime.evaluate: 页面响应异常');
        return { id: target.targetId, ok: true };
      } catch (error) {
        logger?.warn(`CDP 页面 ${target.targetId} 探测失败：${error.message}`);
        return { id: target.targetId, error: error.message };
      } finally {
        if (sessionId) await send('Target.detachFromTarget', { sessionId }).catch(() => {});
      }
    }));
    const accepted = new Set(results.filter(result => result.ok).map(result => result.id));
    if (!accepted.size) throw new Error(`纳米Work主页面均未响应：${results.map(r => `${r.id}: ${r.error}`).join('；')}`);
    const rejected = new Set(targets.filter(t => t.type === 'page' && !accepted.has(t.targetId)).map(t => t.targetId));
    logger?.info(`CDP 页面检查：接管 ${accepted.size} 个可响应主页面，跳过 ${rejected.size} 个其他或无响应页面（不关闭窗口）`);
    return { accepted, rejected };
  } finally { socket.terminate(); }
}

async function createTargetProxy(endpoint, { accepted, rejected }, matches) {
  const path = `/${randomBytes(24).toString('hex')}`;
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0, path });
  await new Promise((resolve, reject) => { server.once('listening', resolve); server.once('error', reject); });
  const upstreams = new Set(), pending = new Map();
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    for (const socket of upstreams) socket.terminate();
    for (const socket of server.clients) socket.terminate();
    server.close();
  };
  server.on('connection', async downstream => {
    // A private, random endpoint is owned by one Playwright connection only.
    if (server.clients.size > 1) { downstream.close(); return; }
    const queued = [], ignored = new Set();
    let upstream, internalId = -1;
    downstream.on('error', () => {});
    downstream.on('close', () => { upstream?.terminate(); });
    downstream.on('message', data => {
      let message;
      try { message = JSON.parse(data); } catch { downstream.close(); return; }
      if (message.id != null) pending.set(message.id, { method: message.method, sessionId: message.sessionId });
      if (upstream?.readyState === WebSocket.OPEN) upstream.send(data.toString());
      else queued.push(data.toString());
    });
    try {
      upstream = await openSocket(endpoint);
      upstreams.add(upstream);
      if (closed || downstream.readyState !== WebSocket.OPEN) { upstream.terminate(); return; }
      upstream.on('error', () => downstream.close());
      upstream.on('close', () => { upstreams.delete(upstream); downstream.close(); });
      upstream.on('message', data => {
        let message;
        try { message = JSON.parse(data); } catch { return; }
        if (message.id < 0) return; // responses to resume/detach of excluded targets
        if (message.id != null) pending.delete(message.id);
        if (ignored.has(message.sessionId)) return;
        if (message.method === 'Target.attachedToTarget' && !message.sessionId) {
          const { targetInfo: target, sessionId, waitingForDebugger } = message.params;
          if (target.type === 'page' && (rejected.has(target.targetId) || (!accepted.has(target.targetId) && !matches(target.url)))) {
            ignored.add(sessionId);
            // Auto-attach can pause new renderers. Resume before detaching, never
            // close a window or leave the user's unrelated renderer paused.
            if (waitingForDebugger) upstream.send(JSON.stringify({ id: internalId--, method: 'Runtime.runIfWaitingForDebugger', sessionId }));
            upstream.send(JSON.stringify({ id: internalId--, method: 'Target.detachFromTarget', params: { sessionId } }));
            return;
          }
        }
        if (message.method === 'Target.detachedFromTarget' && ignored.delete(message.params.sessionId)) return;
        if (downstream.readyState === WebSocket.OPEN) downstream.send(data.toString());
      });
      for (const data of queued) upstream.send(data);
      queued.length = 0;
    } catch { downstream.close(); }
  });
  return {
    url: `ws://127.0.0.1:${server.address().port}${path}`, close,
    pendingMethods: () => [...new Set([...pending.values()].map(p => p.method))].join(', ') || '等待页面首次导航/初始化完成',
  };
}

async function connectProductPages(chromium, url, { matches, logger, timeout = 45000, noDefaults = false, probeTimeout, waitTimeout } = {}) {
  const response = await fetch(new URL('/json/version', url), { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`调试端口 HTTP ${response.status}`);
  const version = await response.json();
  if (!version.webSocketDebuggerUrl) throw new Error('调试端口未返回 browser WebSocket 地址');
  const pages = await probePages(version.webSocketDebuggerUrl, matches, { logger, timeout: probeTimeout, waitTimeout });
  const proxy = await createTargetProxy(version.webSocketDebuggerUrl, pages, matches);
  try {
    const browser = await chromium.connectOverCDP(proxy.url, { timeout, noDefaults });
    browser.once('disconnected', proxy.close);
    return browser;
  } catch (error) {
    const detail = proxy.pendingMethods();
    proxy.close();
    throw new Error(`${error.message}\n未完成的 CDP 指令：${detail}`, { cause: error });
  }
}
module.exports = { connectProductPages, createTargetProxy, probePages };
