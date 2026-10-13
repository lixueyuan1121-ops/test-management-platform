// Electron may expose an uninitializable empty page target. Attaching every
// target makes Playwright wait for that internal page even when the app is ready.
export function selectBusinessTarget(targets, targetId = '') {
  const matches = targets.filter(t => {
    if (t.type !== 'page') return false;
    try { const u = new URL(t.url); return ['http:', 'https:'].includes(u.protocol)
      && (u.hostname === 'work.n.cn' || u.hostname.endsWith('.work.n.cn')); }
    catch { return false; }
  }).filter(t => !targetId || t.id === targetId);
  if (matches.length !== 1) throw new Error(`客户端业务页面应唯一，实际 ${matches.length} 个；请打开主窗口或用 QALAB_CDP_TARGET_ID 指定本次页面`);
  return matches[0];
}

// Only the root attachment is scoped. Child sessions retain their real CDP
// commands/events so OOPIFs, workers, navigation and assertions run normally.
export function scopedAttachMessage(message, targetId) {
  if (!message.sessionId && message.method === 'Target.setAutoAttach' && message.params?.autoAttach) {
    return { ...message, method: 'Target.attachToTarget', params: { targetId, flatten: true } };
  }
  return message;
}

export async function createScopedTransport(endpoint, targetId, { timeout = 10000, Socket = globalThis.WebSocket } = {}) {
  if (!Socket) throw new Error('Electron 定向 CDP 连接需要 Node.js 22+，请升级 Runner Node 环境');
  const socket = new Socket(endpoint);
  const queued = [];
  let onmessage, closed = false;
  const transport = {
    get onmessage() { return onmessage; },
    set onmessage(handler) { onmessage = handler; if (handler) for (const m of queued.splice(0)) handler(m); },
    send(m) { socket.send(JSON.stringify(scopedAttachMessage(m, targetId))); },
    close() { socket.close(); },
  };
  socket.addEventListener('message', e => {
    let m;
    try { m = JSON.parse(e.data); } catch { socket.close(); return; }
    if (onmessage) onmessage(m); else queued.push(m);
  });
  socket.addEventListener('close', () => { closed = true; transport.onclose?.(); });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { socket.close(); reject(new Error('CDP WebSocket 连接超时')); }, timeout);
    socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP WebSocket 连接失败')); }, { once: true });
    socket.addEventListener('close', () => { clearTimeout(timer); reject(new Error('CDP WebSocket 提前关闭')); }, { once: true });
  });
  if (closed) throw new Error('CDP WebSocket 已关闭');
  return transport;
}

export async function connectApplication(chromium, cdpUrl, {
  targetId = process.env.QALAB_CDP_TARGET_ID || '', timeout = 30000,
  fetchImpl = globalThis.fetch, transportFactory = createScopedTransport,
} = {}) {
  // Preserve existing WS/remote-browser support; the Electron probe uses HTTP.
  if (!/^https?:/.test(cdpUrl)) return chromium.connectOverCDP(cdpUrl, { timeout });
  const base = cdpUrl.replace(/\/$/, '');
  const get = async path => {
    const r = await fetchImpl(base + path, { signal: AbortSignal.timeout(Math.min(timeout, 5000)), redirect: 'error' });
    if (!r.ok) throw new Error(`CDP ${path} 返回 HTTP ${r.status}`);
    return r.json();
  };
  const version = await get('/json/version');
  if (!/Electron\//i.test(version['User-Agent'] || version.userAgent || '')) {
    return chromium.connectOverCDP(cdpUrl, { timeout });
  }
  const target = selectBusinessTarget(await get('/json/list'), targetId);
  if (!version.webSocketDebuggerUrl) throw new Error('CDP 缺少浏览器 WebSocket 地址');
  const transport = await transportFactory(version.webSocketDebuggerUrl, target.id, { timeout: Math.min(timeout, 10000) });
  try {
    return await chromium.connectOverCDP(transport, { timeout });
  } catch (error) {
    transport.close();
    throw new Error(`客户端业务页连接失败（target=${target.id}）：${error.message}`, { cause: error });
  }
}
