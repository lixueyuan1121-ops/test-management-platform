// 纳米 Work 专用：观察 openclaw-app 实际使用的消息连接。
// 新客户端的 ClawFallbackSocket 可通过 Electron 桥接传输，CDP 的 websocket 事件看不到它。
// 只加 message 监听，不调用业务接口，不替换 onmessage / onEvent，也不从 DOM 猜工具调用。
const { randomUUID } = require('node:crypto');
const { attachWsTrace, createTraceCollector } = require('./ws-trace');

// 在主文档或 work.n.cn iframe 内执行；回传有界缓冲，避免每个 token 发一次 CDP 调用。
function drainGateway({ key, clear = false, dispose = false }) {
  let observer = window[key];
  if (dispose) {
    observer?.dispose();
    delete window[key];
    return null;
  }
  if (!observer) {
    const sockets = new Map();
    let queue = [], size = 0, dropped = 0;
    let originalFactory, proxyFactory;
    const currentSocket = () => document.querySelector('openclaw-app')?.client?.ws;
    const observe = (socket) => {
      if (!socket?.addEventListener || sockets.has(socket)) return;
      const message = (event) => {
        // 同一页面可能还有其它连接，只收当前纳米 gateway 的业务事件。
        if (socket !== currentSocket()) return;
        const raw = event.data;
        if (typeof raw !== 'string') return; // gateway.handleMessage 本身消费 JSON 文本
        let frame;
        try { frame = JSON.parse(raw); } catch { return; }
        if (frame?.type !== 'event' || !['agent', 'chat'].includes(frame.event)) return;
        if (queue.length >= 2000 || size + raw.length > 8 * 1024 * 1024) { dropped++; return; }
        queue.push(raw);
        size += raw.length;
      };
      const close = () => {
        socket.removeEventListener('message', message);
        socket.removeEventListener('close', close);
        sockets.delete(socket);
      };
      sockets.set(socket, close);
      socket.addEventListener('message', message);
      socket.addEventListener('close', close);
    };
    const scan = () => {
      // 包装当前构造器（可能已被 Electron preload 替换），保留其参数/返回值/原型。
      // 重连时在首帧到达前监听；已有连接直接加监听，无需刷新正在显示的对话。
      if (typeof window.WebSocket === 'function' && window.WebSocket !== proxyFactory) {
        originalFactory = window.WebSocket;
        proxyFactory = new Proxy(originalFactory, {
          construct(target, args, newTarget) {
            const socket = Reflect.construct(target, args, newTarget);
            observe(socket);
            return socket;
          },
          apply(target, thisArg, args) {
            const socket = Reflect.apply(target, thisArg, args);
            observe(socket);
            return socket;
          },
        });
        window.WebSocket = proxyFactory;
      }
      observe(currentSocket());
    };
    const timer = setInterval(scan, 100);
    observer = window[key] = {
      drain(clearQueue) {
        scan();
        const socket = currentSocket();
        const result = { frames: clearQueue ? [] : queue, dropped: clearQueue ? 0 : dropped,
          connected: !!socket && sockets.has(socket) && socket.readyState === 1 };
        queue = []; size = 0; dropped = 0;
        return result;
      },
      dispose() {
        clearInterval(timer);
        for (const close of [...sockets.values()]) close();
        if (window.WebSocket === proxyFactory) window.WebSocket = originalFactory;
        queue = []; size = 0;
      },
    };
  }
  return observer.drain(clear);
}

function isWorkFrame(frame) {
  try {
    const url = new URL(frame.url());
    return ['http:', 'https:'].includes(url.protocol) &&
      (url.hostname === 'work.n.cn' || url.hostname.endsWith('.work.n.cn'));
  } catch { return false; }
}

async function attachNamiTrace(page) {
  const key = `__qalabNamiTrace_${randomUUID().replaceAll('-', '')}`;
  const gateway = createTraceCollector();
  const browser = attachWsTrace(page);
  let closed = false, busy = false, chain = Promise.resolve();
  let dropped = 0, pollErrors = 0;
  // 排队串行化 drain / reset / dispose；禁止旧轮次的异步采集回流到新轮次。
  const enqueue = (fn) => {
    const next = chain.then(fn);
    chain = next.catch(() => {});
    return next;
  };
  const drain = async (clear = false) => {
    if (closed) return;
    let connected = false;
    for (const frame of page.frames().filter(isWorkFrame)) {
      try {
        const data = await frame.evaluate(drainGateway, { key, clear });
        connected ||= data.connected;
        if (!clear) {
          dropped += data.dropped;
          for (const raw of data.frames) gateway.ingest(raw);
        }
      } catch { pollErrors++; } // 导航销毁 execution context，下次轮询重挂；不丢弃已收内容。
    }
    gateway.setConnected(connected);
  };
  const poll = () => {
    if (closed || busy) return;
    busy = true;
    enqueue(() => drain()).finally(() => { busy = false; }).catch(() => {});
  };
  page.on('framenavigated', poll);
  const timer = setInterval(poll, 200);
  timer.unref?.();
  await enqueue(() => drain());
  const collector = {
    async ensureReady({ timeoutMs = 5000 } = {}) {
      const deadline = Date.now() + timeoutMs;
      do {
        await enqueue(() => drain());
        if (!closed && (gateway._state.wsConnected || browser.hasProtocolConnection())) return;
        if (Date.now() < deadline) await new Promise(r => setTimeout(r, 100));
      } while (!closed && Date.now() < deadline);
      throw new Error('[TRACE_CAPTURE_UNAVAILABLE] 纳米 Work 过程采集未就绪，请检查客户端连接后重试；本轮尚未发送');
    },
    async reset() {
      await enqueue(async () => {
        await drain(true);
        gateway.reset(); browser.reset();
        dropped = 0; pollErrors = 0;
      });
    },
    async beginContinuation() {
      await enqueue(async () => {
        await drain(); // 先收齐上一轮，旧文本边界包含缓冲中的 final。
        gateway.beginContinuation(); browser.beginContinuation();
      });
    },
    async buildTrace(runId) {
      return enqueue(async () => {
        await drain(); // 收口前清空页面缓冲，包含最后一帧，不靠固定 sleep 猜测。
        const g = gateway.buildTrace(runId), b = browser.buildTrace(runId);
        // 两种传输单独聚合，再选择来源；真实浏览器连接会被两侧看到，不能重复计工具/文本。
        const useGateway = g.ws_captured || (!b.ws_captured && g.ws_connected);
        const trace = useGateway ? g : b;
        trace.capture_source = useGateway ? 'nami_gateway' : 'browser_websocket';
        trace.capture_health = {
          status: dropped ? 'incomplete' : trace.ws_captured ? 'captured' : 'no_events',
          gateway_connected: g.ws_connected, browser_connected: b.ws_connected,
          dropped_frames: dropped, poll_errors: pollErrors,
        };
        return trace;
      });
    },
    async dispose() {
      closed = true;
      clearInterval(timer);
      page.off('framenavigated', poll);
      browser.dispose();
      await enqueue(async () => {
        await Promise.all(page.frames().filter(isWorkFrame).map(frame =>
          frame.evaluate(drainGateway, { key, dispose: true }).catch(() => {})));
        gateway.setConnected(false);
      });
    },
  };
  return collector;
}

module.exports = { attachNamiTrace };
