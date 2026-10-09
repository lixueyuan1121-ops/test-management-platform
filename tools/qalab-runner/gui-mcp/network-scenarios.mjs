// Deterministic, case-scoped fetch instrumentation. No arbitrary script, headers,
// request bodies, credentials or full response bodies enter the execution report.
import { randomUUID } from 'node:crypto';

export { NETWORK_ACTIONS, validateNetworkScript } from './network-contract.mjs';

// Serialized into exactly one observed frame; cleanup restores only our wrapper.
function installFetchProbe(key) {
  if (Object.keys(window).some(k => k.startsWith('__qalab_network_') && !window[k]?.expired)) throw new Error('上一条网络场景尚未恢复，请等待清理');
  const original = window.fetch, watches = new Map(), faults = new Map();
  const pick = (o, path) => path.split('.').reduce((v, k) => v?.[k], o);
  const matches = (rule, url, method) => url.origin === location.origin && url.pathname === rule.path && method === rule.method && Object.entries(rule.query || {}).every(([k, v]) => url.searchParams.get(k) === v);
  const state = { watches, faults, error: null, original, wrapper: null, expired: false, timer: null };
  state.touch = () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.expired = true;
      for (const f of faults.values()) { f.active = false; for (const release of [...f.pending]) release(); }
      if (window.fetch === state.wrapper) window.fetch = original;
    }, 60000);
  };
  state.wrapper = async function(input, init) {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    const method = String(init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const observers = [...watches.values()].filter(w => matches(w, url, method));
    const entries = observers.map(w => { w.requested++; return { w, sequence: w.requested }; });
    const fault = [...faults.values()].find(f => f.active && matches(f, url, method));
    if (fault) fault.hits++;
    try {
      let response;
      if (fault?.mode === 'network_error') throw new TypeError('QA Lab simulated network failure');
      if (fault?.mode === 'response') response = new Response(JSON.stringify(fault.body), { status: fault.status, headers: { 'Content-Type': 'application/json' } });
      else response = await original.call(this, input, init);
      for (const { w, sequence } of entries) {
        w.received++; w.status = response.status;
        if (w.response_path) {
          try {
            const data = await response.clone().json(), list = pick(data, w.response_path);
            if (!response.ok || !Array.isArray(list) || list.length > 1000) throw new Error('响应不是成功的有界列表');
            const values = list.map(item => pick(item, w.item_field));
            if (values.some(v => typeof v !== 'string' || v.length > 500)) throw new Error('列表字段缺失、非字符串或过长');
            if (sequence >= w.sequence) { w.values = values; w.sequence = sequence; w.error = null; }
          } catch { if (sequence >= w.sequence) { w.sequence = sequence; w.values = null; w.error = '列表响应无法按指定字段解析'; } }
        }
      }
      if (fault?.mode === 'hold_response' && fault.active) {
        await new Promise(resolve => {
          let done = false;
          const release = () => { if (done) return; done = true; clearTimeout(timer); fault.pending.delete(release); resolve(); };
          const timer = setTimeout(() => { fault.expired++; release(); }, fault.timeout_ms);
          fault.pending.add(release);
        });
      }
      return response;
    } finally { for (const { w } of entries) w.completed++; }
  };
  window.fetch = state.wrapper;
  window[key] = state;
  state.touch();
}

export function createNetworkScenarios(getPage, runtime) {
  const key = '__qalab_network_' + randomUUID().replaceAll('-', '');
  const frames = new Set(), owners = new Map();
  async function frameFor(a) {
    const page = getPage();
    const found = a.frame === 'shell' ? [page.mainFrame()] : page.frames().filter(f => f.url().includes(a.frame.slice(4)));
    if (found.length !== 1) throw new Error('网络场景 frame 必须唯一');
    const f = found[0];
    if (!frames.has(f)) { await f.evaluate(installFetchProbe, key); frames.add(f); }
    return f;
  }
  async function command(kind, a) {
    const type = ['fault_route', 'release_fault', 'assert_fault_hits'].includes(kind) ? 'fault' : 'watch';
    let f = owners.get(type + a.id);
    if (['watch_network', 'fault_route'].includes(kind)) { f = await frameFor(a); owners.set(type + a.id, f); }
    if (!f) throw new Error('网络场景未注册');
    return f.evaluate(({ key, kind, a }) => {
      const s = window[key];
      if (!s || s.expired || window.fetch !== s.wrapper) throw new Error('网络场景失效：页面已重载、会话超时或 fetch 被替换');
      s.touch();
      if (kind === 'watch_network') { s.watches.set(a.id, { ...a, requested: 0, received: 0, completed: 0, sequence: 0, values: null }); return { watching: a.id }; }
      if (kind === 'fault_route') { s.faults.set(a.id, { ...a, active: true, hits: 0, expired: 0, pending: new Set() }); return { fault: a.id }; }
      const fault = s.faults.get(a.id);
      if (kind === 'release_fault') { fault.active = false; for (const release of [...fault.pending]) release(); return { released: a.id, hits: fault.hits }; }
      if (kind === 'assert_fault_hits') return { actual: fault.hits, expected: a.expected, pass: fault.hits === a.expected && fault.expired === 0 };
      const w = s.watches.get(a.id);
      return { requested: w.requested, received: w.received, completed: w.completed, values: w.values, status: w.status ?? null, error: w.error ?? null };
    }, { key, kind, a });
  }
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  return {
    async execute(action, a, target = {}) {
      if (['watch_network', 'fault_route', 'release_fault', 'assert_fault_hits'].includes(action)) return command(action, a);
      if (action === 'wait_network') {
        const deadline = Date.now() + (a.timeout_ms ?? 10000);
        do { const r = await command(action, a); if (r[a.phase] >= a.count) return { ...r, done: true }; await sleep(30); } while (Date.now() < deadline);
        throw new Error(`等待 ${a.id} ${a.phase}=${a.count} 超时，检查 fetch/frame/path/method`);
      }
      if (action === 'assert_network_count') {
        // Observe the whole window; a transient count of one must not hide a duplicate.
        await sleep(a.settle_ms ?? 500);
        const r = await command(action, a);
        return { actual: r.requested, expected: a.expected, pass: r.requested === a.expected };
      }
      if (action === 'assert_list_from_response') {
        const deadline = Date.now() + (a.timeout_ms ?? 3000);
        let r;
        do {
          const w = await command(action, a);
          if (!w.requested || w.completed !== w.requested || w.error || !Array.isArray(w.values)) throw new Error(w.error || '列表响应未完整捕获，请先 wait_network');
          const found = await runtime.inspect(target, { multiple: true, all: true });
          const hits = found.matches || [];
          if (new Set(hits.map(h => h.hit.scope)).size > 1) throw new Error('列表匹配多个 frame，请限定 frame');
          const selected = hits.filter(h => JSON.stringify(h.hit) === JSON.stringify(hits[0]?.hit));
          const actual = [];
          for (const h of selected) actual.push(...await h.loc.allTextContents());
          const clean = v => v.replace(/\s+/g, ' ').trim();
          const expected = w.values.map(clean);
          r = { actual: actual.map(clean), expected, pass: (a.allow_empty === true || expected.length > 0) && JSON.stringify(actual.map(clean)) === JSON.stringify(expected) };
          if (r.pass) return r;
          await sleep(50);
        } while (Date.now() < deadline);
        return r;
      }
      throw new Error('不支持的网络步骤');
    },
    async cleanup() {
      const errors = [];
      for (const frame of frames) {
        try {
          const result = await frame.evaluate(key => {
            const s = window[key]; if (!s) throw new Error('页面重载，网络场景证据丢失');
            clearTimeout(s.timer);
            const failures = [...s.faults.values()].filter(f => !f.hits || f.expired || f.pending.size).map(f => `${f.id}: hits=${f.hits}, expired=${f.expired}, pending=${f.pending.size}`);
            for (const f of s.faults.values()) { f.active = false; for (const release of [...f.pending]) release(); }
            if (window.fetch === s.wrapper) window.fetch = s.original;
            else failures.push('fetch 被其他代码替换');
            if (s.expired) failures.push('网络场景会话超时');
            delete window[key]; return failures;
          }, key);
          errors.push(...result);
        } catch (e) { errors.push(e.message); }
      }
      frames.clear(); owners.clear();
      if (errors.length) throw new Error(errors.join('；'));
    },
  };
}
