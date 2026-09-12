// gui-core —— 纳米Work GUI 自动化的**纯核心**(无 MCP、无进程),供两方复用:
//   1) gui-mcp/server.mjs:包成 MCP 工具给 claude 用(judge 步/无 script 兜底);
//   2) runner 的 StepExecutor:直接函数调用,按 script 确定性执行 gui 步骤(P3)。
// 两方共用同一套定位引擎(语义 key + 多候选自愈 + iframe 穿透),保证行为一致。
//
// 依赖:playwright-core。前提:namiclaw 已带 --remote-debugging-port 启动。
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { rectInsideRatio } from "./probe-collect.mjs";
import { createAutomationRuntime } from "./runtime-loader.mjs";
import { tokensForKey, pickConfident, mintedToCandidates, discoverInPage } from "./heal.mjs";
import { pickCoreKeys, failedCoreKeys } from "../core-keys.mjs";
import { pressOsEscape } from "../os-key.mjs";
import { CAPTURE_INIT, DRAIN_SCRIPT, STOP_SCRIPT, ACK_SCRIPT } from "../record-capture.mjs";

const SELECTORS_PATH = join(dirname(fileURLToPath(import.meta.url)), "selectors.json");

// 页面探测脚本(浏览器 context 内执行):扫可见可交互元素 + 按稳定性打分的候选选择器。
export const DISCOVER_SCRIPT = function ({ relax = false } = {}) {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const isBEM = (cls) => /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(cls);
  const isHash = (cls) => /[A-Za-z0-9]{6,}$/.test(cls) && !/[-_]/.test(cls.slice(-8));
  const genCandidates = (el) => {
    const cands = [];
    // 测试专用锚点(最稳,开发提测约定):兼容三种属性名 data-testid/data-test-id/data-test。
    // 用命中的**实际属性名**生成 CSS 属性选择器(by=css 走 locator();不用 by=testid/getByTestId——
    // 那个只认 data-testid,开发用 data-test-id 会定位不到)。score 100=最高优先。
    for (const attr of ["data-testid", "data-test-id", "data-test"]) {
      const v = el.getAttribute(attr);
      if (v) { const s = `[${attr}=${JSON.stringify(v)}]`; cands.push({ sel: s, score: 100, by: attr === "data-testid" ? "testid" : "css", value: attr === "data-testid" ? v : s }); }
    }
    if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ sel: `#${CSS.escape(el.id)}`, score: 90, by: "css", value: `#${CSS.escape(el.id)}` });
    const aria = el.getAttribute("aria-label");
    if (aria && aria.length < 60) cands.push({ sel: `[aria-label=${JSON.stringify(aria)}]`, score: 80, by: "label", value: aria, exact: true });
    const name = el.getAttribute("name");
    if (name) cands.push({ sel: `[name=${JSON.stringify(name)}]`, score: 75, by: "css", value: `[name=${JSON.stringify(name)}]` });
    const ph = el.getAttribute("placeholder");
    if (ph) cands.push({ sel: `[placeholder=${JSON.stringify(ph)}]`, score: 70, by: "placeholder", value: ph, exact: true });
    const role = el.getAttribute("role") || ({ BUTTON: 'button', A: 'link', INPUT: ['button','submit','reset'].includes(el.type) ? 'button' : el.type === 'checkbox' ? 'checkbox' : el.type === 'radio' ? 'radio' : 'textbox', TEXTAREA: 'textbox', SELECT: 'combobox' })[el.tagName];
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
    const accessibleName = labelled || aria || (el.labels?.length ? [...el.labels].map(label => label.textContent).join(' ').trim() : '') || (el.innerText || '').trim();
    if (role && accessibleName) cands.push({ sel: `role=${role}`, score: 95, by: 'role', value: role, name: accessibleName, exact: true });
    const classes = Array.from(el.classList);
    const bem = classes.filter(isBEM);
    const stable = bem.length ? bem : classes.filter((c) => !isHash(c) && c.length > 3);
    if (stable.length) { const sel = stable.map((c) => `.${CSS.escape(c)}`).join(""); cands.push({ sel, score: bem.length ? 60 : 45, by: "css", value: sel }); }
    const txt = (el.innerText || el.textContent || "").trim().slice(0, 30);
    if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ sel: `text=${txt}`, score: 65, by: "text", value: txt, exact: true });
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");
    if (type) cands.push({ sel: `${tag}[type="${type}"]`, score: 30, by: "css", value: `${tag}[type="${type}"]` });
    return cands.sort((a, b) => b.score - a.score);
  };
  // 递归收集所有元素,穿透 open shadowRoot(与 probe-collect.mjs 的 collectDeep 同款,evaluate 内不能 import 故内联)。
  const collectDeep = (root) => {
    const acc = [];
    for (const el of root.querySelectorAll("*")) {
      acc.push(el);
      if (el.shadowRoot) { for (const s of collectDeep(el.shadowRoot)) acc.push(s); }
    }
    return acc;
  };
  const all = collectDeep(document);
  let elements;
  if (relax) {
    // 框选放宽:框内全量——不套白名单、不做父级去重、不判 cursor;只留可见(有候选在末尾筛)。
    elements = all;
  } else {
    // 全页扫描:白名单选择器 + cursor:pointer 补充,再按父级同文本去重(原逻辑,但采集根已穿 shadow)。
    // 白名单:可交互元素 + 展示文本类(section-title/分组标题/标签/heading——纯展示 div 不可交互,
    // 但常是断言目标,如"最近任务"分组标题。加它们让 probe/自愈能采到、能断言/定位。
    // 父级同文本去重 + isVisible + 有候选 三重兜底,故加宽白名单不会灌垃圾。
    const sel = "a, button, [role=button], [role=tab], [role=menuitem], input, textarea, select, [contenteditable=true], [onclick], [class*=btn], [class*=action], [class*=nav__item], [class*=menu-item], [class*=section-title], [class*=__title], [class*=__label], [class*=__header], [role=heading], h1, h2, h3";
    const set = new Set();
    for (const el of all) {
      try { if (el.matches(sel) || getComputedStyle(el).cursor === "pointer") set.add(el); } catch { /* 忽略 */ }
    }
    elements = [...set].filter((el) => {
      const t = (el.innerText || "").trim();
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (set.has(p) && (p.innerText || "").trim() === t) return false;
      }
      return true;
    });
  }
  const out = [];
  const epoch = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  const refs = new Map();
  window.__qalabProbeElements = refs; // 仅本次探测有效；下一次探测/导航后旧引用失效。
  for (const el of elements) {
    if (!isVisible(el)) continue;
    const candidates = genCandidates(el);
    if (!candidates.length) continue;
    const r = el.getBoundingClientRect();
    const element_ref = epoch + ':' + out.length;
    refs.set(element_ref, el);
    out.push({ element_ref, tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "", text: (el.innerText || el.value || "").trim().slice(0, 40), rect: { x: r.left, y: r.top, w: r.width, h: r.height }, candidates: candidates.slice(0, 6), best: candidates[0] });
  }
  return out;
};

// 开启被测应用的 test-id 注入模式(localStorage "openclaw.testids"="1")。
// ctx.addInitScript:此后本 context 所有 frame/所有加载在页面脚本运行前即带 flag(reset-home reload、
// 重连、vm iframe 各 origin 一次配置全程生效)。首连时当前页已加载(错过 main.ts 启动时机):对现有各
// frame 即时 setItem;若之前未开过(localStorage 非 "1")则 reload 一次让 main.ts 重跑、观察器当场启动,
// 已开过则跳过 reload 不折腾。抽成模块级纯函数(注入行为不依赖 gui-core 闭包)便于单测。
const TESTID_SET = () => { try { localStorage.setItem("openclaw.testids", "1"); } catch { /* SSR/隐私模式 */ } };

export async function injectTestIdMode(ctx, page, { reloadTimeout = 15000 } = {}) {
  try { await ctx.addInitScript(TESTID_SET); } catch { /* 接口不可用则仅靠下方即时注入 + 后续导航兜底 */ }
  let already = false;
  try { already = await page.evaluate(() => localStorage.getItem("openclaw.testids") === "1"); } catch { /* 取不到按未开 */ }
  for (const f of page.frames()) {
    await f.evaluate(TESTID_SET).catch(() => { /* 跨域/未就绪 frame 忽略,addInitScript 在其下次加载兜底 */ });
  }
  if (!already) {
    try { await page.reload({ waitUntil: "domcontentloaded", timeout: reloadTimeout }); } catch { /* reload 失败不阻断,靠后续导航生效 */ }
  }
  return { reloaded: !already };
}

// 从 CDP context 的 pages() 结果里挑"就绪可用"的页面:优先 url 含业务域 work.n.cn 的页,
// 否则首个未关闭页;一个可用页都没有(冷启动时页面 target 尚未在 CDP 注册)→ null,由调用方
// 继续轮询等待。纯函数(无 playwright 依赖),单测见 pick-ready-page.test.mjs。
export function pickReadyPage(pages) {
  const open = (Array.isArray(pages) ? pages : []).filter((p) => p && !p.isClosed?.());
  if (!open.length) return null;
  return open.find((p) => (p.url() || "").includes("work.n.cn")) || open[0];
}

// frame URL 去掉会话参数和 hash；相同路径的多帧由 runtime 消歧，不能随便取第一帧。
export function recordingFrame(frame, main, vm, url = frame.url()) {
  if (frame === main) return "shell";
  if (frame === vm) return "vm";
  try { const parsed = new URL(url); parsed.search = ''; parsed.hash = ''; return "url:" + parsed.href; }
  catch { return "url:" + url; }
}

// 工厂:创建一个 gui-core 实例(持有 browser/page 连接态)。
// opts: { cdpUrl, timeout, selectorsPath, registry, vmIframe }
// registry/vmIframe 若传入则直接用之(runner 从 API 拉的注册表),否则 readFileSync 内置 selectors.json。
export function createGuiCore(opts = {}) {
  const CDP_URL = opts.cdpUrl || process.env.CDP_URL || "http://127.0.0.1:9222";
  const DEFAULT_TIMEOUT = Number(opts.timeout || process.env.GUI_TIMEOUT_MS || 10000);
  // 冷启动时等页面 target 在 CDP 注册出来的上限(端口活≠页面就绪,见 ensureConnected)。
  const PAGE_READY_TIMEOUT = Number(opts.pageReadyTimeout || process.env.CDP_PAGE_READY_MS || 15000);
  // let(非 const):setRegistry 就地换表后,共享 runtime 通过 getter 读取新注册表。
  let REGISTRY, VM_IFRAME;
  let activeRecordingSession = null;
  let recordingEnabled = false;
  let recordEventSink = null;
  let recordPersistenceError = null;
  const pendingRecordEvents = new Map();
  const recordingContexts = new WeakSet();
  if (opts.registry) {
    REGISTRY = opts.registry; VM_IFRAME = opts.vmIframe || "";
  } else {
    const j = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"));
    REGISTRY = j.registry; VM_IFRAME = j.vmIframe;
  }

  const DEFAULT_REGISTRY = REGISTRY;
  const DEFAULT_VM_IFRAME = VM_IFRAME;

  // 核心 key 清单(进入/首页/登录类):单一事实源在 selectors.json 顶层 coreKeys(见 core-keys.mjs)。
  // 供 verify 巡检默认目标 + 失效告警。读不到 → []（巡检退化为按传入 keys,不误报）。
  let CORE_KEYS = [];
  try { CORE_KEYS = pickCoreKeys(JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"))); }
  catch { CORE_KEYS = []; }
  CORE_KEYS = (opts.coreKeys || CORE_KEYS).filter((key) => REGISTRY[key]);
  const DEFAULT_CORE_KEYS = CORE_KEYS.slice();

  let browser = null;
  let page = null;

  let ctx = null;
  let traceContext = null;
  const HEALS = [];
  let testidInjected = false;
  async function enableTestIdMode() {
    if (testidInjected) return;
    await injectTestIdMode(ctx, page, { reloadTimeout: PAGE_READY_TIMEOUT });
    testidInjected = true;
  }
  const runtime = createAutomationRuntime({
    page: () => page, registry: () => REGISTRY, vmIframe: () => VM_IFRAME,
    timeout: DEFAULT_TIMEOUT,
  });

  async function ensureConnected() {
    if (browser && browser.isConnected() && page && !page.isClosed()) {
      await enableTestIdMode();
      return;
    }
    if (browser) {
      try { await runtime.unmockAll(); } catch {}
      try { await browser.close(); } catch {}
      runtime.resetConnection();
      traceContext = null;
    }
    testidInjected = false;
    browser = await chromium.connectOverCDP(CDP_URL);
    ctx = browser.contexts()[0] || (await browser.newContext());
    // 冷启动竞态:CDP 端口先活、渲染进程的页面 target 后注册,刚连上时 ctx.pages() 可能仍空。
    // 此时若直接 ctx.newPage() 会撞 Electron 的 Target.createTarget: Not supported(不支持建 target),
    // 导致冷启动首条用例复位失败。故轮询等页面 target 出现(≤PAGE_READY_TIMEOUT),拿到就用;
    // 等满仍无页面才回落 newPage(非 Electron/特殊场景的兜底,正常路径不会走到)。
    const end = Date.now() + PAGE_READY_TIMEOUT;
    for (;;) {
      const p = pickReadyPage(ctx.pages());
      if (p) { page = p; await enableTestIdMode(); return; }
      if (Date.now() >= end) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    page = await ctx.newPage();
    await enableTestIdMode();
  }

  async function contentFrame() {
    if (VM_IFRAME) {
      const frames = page.locator(VM_IFRAME);
      const count = await frames.count();
      if (count > 1) throw new Error("业务 iframe 匹配多个目标");
      if (count === 1) {
        const handle = await frames.elementHandle();
        try {
          const frame = await handle?.contentFrame();
          if (frame) return frame;
        } finally { await handle?.dispose(); }
      }
    }
    return page.mainFrame();
  }

  async function waitForContentFrame(timeoutMs = 8000) {
    const start = Date.now();
    for (;;) {
      const f = await contentFrame();
      if (f !== page.mainFrame()) return f;
      if (Date.now() - start > timeoutMs) return f;
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  const resolveKey = (key, options = {}) => runtime.resolve({ key, timeout_ms: options.timeout ?? DEFAULT_TIMEOUT }, options);
  const isKeyVisible = (key) => runtime.isKeyVisible(key);

  // Discovery produces review suggestions; it never changes the failing test's
  // target or verdict. Standalone exports therefore keep the same semantics.
  async function suggestMissingKey(key) {
    const entry = REGISTRY[key];
    if (!entry || HEALS.some((item) => item.key === key) || process.env.GUI_HEAL === "0") return;
    const args = tokensForKey(key, entry);
    const frameName = entry.frame;
    const frames = frameName === "shell" ? [page.mainFrame()]
      : frameName === "vm" ? [await contentFrame()]
      : typeof frameName === "string" && frameName.startsWith("url:")
        ? page.frames().filter((f) => f.url().includes(frameName.slice(4))) : page.frames();
    const proposals = [];
    for (const frame of frames) {
      const best = pickConfident(await frame.evaluate(discoverInPage, args));
      if (best) proposals.push({ best, frame });
    }
    if (proposals.length !== 1) return;
    const { best, frame } = proposals[0];
    const candidates = mintedToCandidates(best.minted);
    if (candidates.length) HEALS.push({ key, candidates, evidence: {
      applied: false, matched: best.why, text: best.text, tag: best.tag, score: best.score,
      frame_url: frame.url().slice(0, 200), reason: "定位失败后的候选建议，未用于改变测试结果",
    } });
  }
  async function operate(method, args) {
    await ensureConnected();
    try { return await runtime[method](args); }
    catch (e) {
      if (e.code === "TARGET_TIMEOUT" && args?.key && !args.within) await suggestMissingKey(args.key).catch(() => {});
      throw e;
    }
  }

  // reload 后确保回到首页:探首页锚点(多别名),已在首页直接返回;不在则点侧栏『首页』导航回去,再等就绪。
  // 关键——SPA reload 只重载当前路由(客户端常驻不重启),上一条用例把路由停在任务详情/其它 Tab 时,
  // reload 回不到首页,必须应用内导航(点 navHome)才回得去。navHome 定位不到/点不上就跳过(尽力而为,
  // 交上层 resetOrBlock 多轮自愈兜底);首页锚点探不到不抛错(不阻断复位)。
  async function ensureOnHome(readyKeyCandidates, readyTimeout) {
    const homeKeys = [...new Set(readyKeyCandidates)].filter((k) => k && REGISTRY[k]);
    if (!homeKeys.length) return;                        // 注册表无首页锚点,无从判断,不折腾
    const onHome = async () => {
      for (const k of homeKeys) if (await isKeyVisible(k)) return true;
      return false;
    };
    if (await onHome()) return;                          // reload 后已在首页,无需导航
    if (REGISTRY.navHome) {                               // 不在首页:点侧栏『首页』导航回去
      try {
        const { loc } = await resolveKey("navHome", { timeout: 3000, requireVisible: true });
        await loc.click({ timeout: DEFAULT_TIMEOUT });
      } catch { /* navHome 定位不到/点不上,跳过,交上层自愈 */ }
    }
    const end = Date.now() + readyTimeout;               // 等首页锚点就绪(尽力,不抛)
    for (;;) {
      if (await onHome()) return;
      if (Date.now() >= end) return;
      await new Promise((r) => setTimeout(r, 300));
    }
  }

  // ---- 对外操作(server 和 StepExecutor 共用)----
  return {
    get registry() { return REGISTRY; },
    get vmIframe() { return VM_IFRAME; },
    get coreKeys() { return CORE_KEYS.slice(); },
    // 就地换注册表(runner 每条 gui/e2e 用例执行前按 project_id 从 API 拉后调):只换 REGISTRY/VM_IFRAME,
    // 不动 browser/page 连接态。共享 runtime 的定位和回复基线同步更新。
    setRegistry(registry, vmIframe, coreKeys) {
      REGISTRY = registry ?? DEFAULT_REGISTRY;
      VM_IFRAME = vmIframe ?? DEFAULT_VM_IFRAME;
      CORE_KEYS = (Array.isArray(coreKeys) ? coreKeys : DEFAULT_CORE_KEYS).filter((key) => REGISTRY[key]);
      runtime.resetResponse();
      HEALS.length = 0;
    },
    ensureConnected,
    contentFrame,
    // 取走并清空本轮自愈记录(runner 每条用例执行完调用,POST /api/selectors/learned 上报评审)。
    drainHeals() { return HEALS.splice(0); },

    // ---- 录制:注入事件捕获 / 排空缓冲 / 停止(见 record-capture.mjs)----
    async startRecording(sessionId) {
      await ensureConnected();
      // addInitScript 先注册:保证 testid 注入若触发 reload,reload 后的新页/新 iframe 在脚本运行前即带捕获钩子。
      // (顺序坑:之前先 injectTestIdMode 后 addInitScript → reload 早发生、捕获脚本没覆盖到,vm iframe 无钩子→录不到)。
      if (activeRecordingSession !== String(sessionId)) pendingRecordEvents.clear();
      activeRecordingSession = String(sessionId);
      recordingEnabled = true;
      if (!recordingContexts.has(ctx)) {
        await ctx.exposeBinding('__qalabRecorderState', () => recordingEnabled ? activeRecordingSession : null);
        await ctx.exposeBinding('__qalabRecorderEvent', async (source, ev) => {
          const url = ev.document_url || source.frame.url(); // 在回调到达时固定 URL，导航之后不改写同一事件。
          if (activeRecordingSession && ev?.event_id?.startsWith(activeRecordingSession + ":")) {
            const frame = recordingFrame(source.frame, page.mainFrame(), await contentFrame(), url);
            pendingRecordEvents.set(ev.event_id, { ev, frame });
            try { if (recordEventSink) await recordEventSink(Number(activeRecordingSession), { ev, frame }); }
            catch (error) { recordPersistenceError = error; throw error; }
          }
        });
        await ctx.addInitScript({ content: `window.__qalabRecorderState().then(id => { if (id) (${CAPTURE_INIT.toString()})({sessionId:id}); });` });
        recordingContexts.add(ctx);
      }
      const { reloaded } = await injectTestIdMode(ctx, page, { reloadTimeout: PAGE_READY_TIMEOUT });  // 保证元素带 testid
      // reload 过 → 等业务 iframe 重新就绪,再逐 frame 即时注入(现有帧;addInitScript 覆盖首次加载的帧)。
      if (reloaded) { try { await waitForContentFrame(PAGE_READY_TIMEOUT); } catch { /* 尽力而为 */ } }
      for (const f of page.frames()) {
        await f.evaluate(CAPTURE_INIT, { sessionId: activeRecordingSession });
      }
      return { recording: true };
    },
    // 排空各 frame 的捕获缓冲,带上 frame 标签(shell/vm/url:路径,与 probe frameMatch 同口径)。
    setRecordEventSink(sink) { recordEventSink = sink; },
    async drainRecordEvents() {
      await ensureConnected();
      if (recordPersistenceError) {
        const error = recordPersistenceError; recordPersistenceError = null;
        throw new Error('录制事件持久化失败：' + error.message);
      }
      const main = page.mainFrame();
      const vm = await contentFrame();
      const labelFor = f => recordingFrame(f, main, vm);
      const out = [...pendingRecordEvents.values()].filter(({ ev }) => ev.event_id?.startsWith(activeRecordingSession + ":"));
      for (const f of page.frames()) {
        let raw;
        try { raw = await f.evaluate(DRAIN_SCRIPT); } catch { continue; }
        if (!Array.isArray(raw) || !raw.length) continue;
        const label = labelFor(f);
        for (const ev of raw) if (ev.event_id?.startsWith(activeRecordingSession + ":")) {
          out.push(pendingRecordEvents.get(ev.event_id) || { ev, frame: label });
        }
      }
      return out;   // [{ev(原始捕获), frame}] —— runner 侧再经 rawEventToStep 规整
    },
    async stopRecording() {
      recordingEnabled = false;
      for (const f of page.frames()) {
        try { await f.evaluate(STOP_SCRIPT); }
        catch (error) { if (!f.isDetached()) throw error; }
      }
      return { recording: false };
    },
    async ackRecordEvents(ids) {
      for (const id of ids) pendingRecordEvents.delete(id);
      for (const f of page.frames()) await f.evaluate(ACK_SCRIPT, ids).catch(() => {});
    },

    async connect() {
      await ensureConnected();
      const f = await waitForContentFrame();
      return { connected: true, title: await page.title(), url: page.url(), frame_url: f.url(), in_iframe: f !== page.mainFrame() };
    },
    async setChecked(args) { await ensureConnected(); return runtime.setChecked(args); },
    async selectOption(args) { await ensureConnected(); return runtime.selectOption(args); },
    listKeys() {
      return { count: Object.keys(REGISTRY).length, keys: Object.entries(REGISTRY).map(([k, v]) => ({ key: k, frame: v.frame, desc: v.desc })) };
    },
    async validateSelection({ items = [] } = {}) {
      await ensureConnected();
      const results = [];
      for (const item of items.slice(0, 100)) {
        const registry = { ...REGISTRY, __selection: { frame: item.frame || 'auto', candidates: item.candidates || [] } };
        const checker = createAutomationRuntime({ page, registry, vmIframe: VM_IFRAME, timeout: 500 });
        try {
          const result = await checker.inspect({ ...(item.target || {}), key: '__selection' }, { requireVisible: true });
          const visible = result.count === 1 && await result.loc.isVisible();
          const actual = result.count === 1 ? await result.loc.evaluate(el => ({ tag: el.tagName.toLowerCase(), text: (el.innerText || el.value || '').trim().slice(0, 40) })) : null;
          const identityVerified = item.element_ref && result.count === 1
            ? await result.loc.evaluate((el, ref) => window.__qalabProbeElements?.get(ref) === el, item.element_ref) : false;
          const sameElement = (!item.element_ref || identityVerified) && (!item.expected || (actual && (!item.expected.tag || actual.tag === item.expected.tag.toLowerCase())
            && (!item.expected.text || actual.text === item.expected.text.trim().slice(0, 40))));
          if (visible && sameElement) await result.loc.evaluate(el => {
            const old = el.style.outline;
            el.style.outline = '3px solid #409eff';
            setTimeout(() => { el.style.outline = old; }, 1200);
          });
          results.push({ key: item.key, ok: visible && !!sameElement, identity_verified: !!identityVerified, count: result.count, visible, actual, hit: result.hit,
            error: !visible ? '当前页面未唯一匹配可见元素' : !sameElement ? '页面状态已变化，当前元素与选中时不同，请重新探测' : null });
        } catch (error) { results.push({ key: item.key, ok: false, error: error.message, code: error.code }); }
      }
      return { validation: results, checked_at: new Date().toISOString() };
    },
    async probe({ contains = "", bbox = null, limit = 0, screenshot = false } = {}) {
      const relax = !!bbox;               // 框选：放宽采集(穿透+不过滤白名单/去重)
      // 每 frame 返回上限。默认放大到 1000：真实复杂应用(如 namiclaw vm iframe)单帧可交互元素
      // 常达数百个(聊天消息+导航+输入区),旧默认 40 会按 DOM 顺序把靠底部的输入区控件(如「边想边做」
      // 在去重后第 486 位)截断——采到却切掉、传不到前端,组头还显示全量 total 造成"共 N 个却只有 40 行"
      // 的误导。框选(bbox)已先按框空间过滤，剩余通常远小于 300。需更精准时用框选或 contains 过滤。
      const cap = limit || (bbox ? 300 : 1000);
      await ensureConnected();
      // 多级页面:遍历页面所有 frame(Playwright 的 page.frames() 已含任意深度的嵌套 iframe),
      // 逐 frame 跑发现脚本。主框架标 shell;主 vm iframe(.work.n.cn)标 vm;其余嵌套 iframe 标 iframe。
      const main = page.mainFrame();
      const vm = await contentFrame();   // 主内容 iframe(与执行侧 contentFrame 同源)
      const frameLabel = (f) =>
        f === main ? "shell" : f === vm ? "vm" : "iframe";
      // frameMatch:加为 key 时写入 selector_key.frame 的值。shell/vm 沿用旧语义;深层 iframe
      // 取 url:<origin/path>——执行侧 scopesFor 据此从 page.frames() 扁平查找该 Frame 定位。
      const frameMatch = (f) => {
        if (f === main) return "shell";
        if (f === vm) return "vm";
        return recordingFrame(f, main, vm);
      };
      // 整页截图(可选,discover 用):fullPage 展开主文档滚动区,坐标系=主文档内容左上(0,0)。
      // 注:iframe 内部滚动区不随 fullPage 展开——iframe 内滚出可视区的元素框可能不准(已知限制)。
      let screenshotBuffer = null;
      if (screenshot) {
        try { screenshotBuffer = await page.screenshot({ fullPage: true, type: "png" }); }
        catch { screenshotBuffer = null; }   // 截图失败不阻断探测,降级为无底图
      }
      // 主文档滚动量 + CSS 尺寸:元素 rect 是各 frame 视口相对,+ mainScroll 转主文档内容绝对
      // (对齐 fullPage 图);pageSize 供前端把 absRect 归一化到截图展示尺寸(自动消 devicePixelRatio)。
      const mainScroll = await main.evaluate(() => ({ x: window.scrollX, y: window.scrollY })).catch(() => ({ x: 0, y: 0 }));
      const pageSize = await main.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight })).catch(() => ({ w: 0, h: 0 }));
      const groups = [];
      for (const target of page.frames()) {
        const frame = frameLabel(target);
        const fmatch = frameMatch(target);
        // 该 frame 视口左上在 main viewport 的位置(main=0,0;iframe 用 frameElement 的 box,
        // Playwright 的 boundingBox 任意深度都相对 main viewport,单层取值即可)。
        // 取不到(跨域/时序)→ 兜底用 {0,0} 近似(标 approx),保证元素仍有 absRect(前端画虚线框),
        // 而不是整组无框。近似框位置可能偏(缺 iframe 偏移),但比完全不显示强。
        let frameBox = { x: 0, y: 0 };
        let approx = false;
        if (target !== main) {
          try { const fe = await target.frameElement(); const b = await fe.boundingBox(); if (b) frameBox = { x: b.x, y: b.y }; else { approx = true; } }
          catch { approx = true; }
        }
        let els = [];
        try {
          els = await target.evaluate(DISCOVER_SCRIPT, { relax });
        } catch (e) {
          // 跨域/已卸载的 frame evaluate 会抛错;记为一组错误、跳过,不中断其它 frame。
          groups.push({ frame, frameMatch: fmatch, url: target.url(), error: e.message, elements: [] });
          continue;
        }
        if (contains) els = els.filter((e) => (e.text || "").includes(contains));
        // 整页绝对坐标 absRect = frameBox + rect(frame 视口相对) + mainScroll。frameBox 兜底为 {0,0}
        // 时标 absApprox=true(前端虚线提示位置近似)。这样列表里每个元素都有框,不再漏。
        for (const el of els) {
          if (el.rect) { el.absRect = { x: frameBox.x + el.rect.x + mainScroll.x, y: frameBox.y + el.rect.y + mainScroll.y, w: el.rect.w, h: el.rect.h }; if (approx) el.absApprox = true; }
        }
        // 框选:只留"大部分落在框内"的元素(insideRatio≥0.5)。any-overlap 会把盖住框的页面级大容器
        // (.shell/.chat-main 等,仅极小比例与框相交)全放进来,淹没目标小控件;按占比过滤精准得多。
        if (bbox) els = els.filter((e) => rectInsideRatio(e.absRect, bbox) >= 0.5);
        // 无元素的 frame 不产空组(减少噪音),但保留有错误的组供排查。
        if (els.length) groups.push({ frame, frameMatch: fmatch, url: target.url(), total: els.length, elements: els.slice(0, cap) });
      }
      return { groups, pageSize, screenshotBuffer };
    },
    // 校验一批语义 key 是否在当前页命中(逐个 isKeyVisible,复用同一定位引擎)。
    // 供 runner 的 probe verify 模式用:回归确认某作用域已登记的 key 仍能在页面上定位到。
    async verifyKeys(keys) {
      await ensureConnected();
      const out = {}, details = {};
      for (const k of (keys || [])) {
        try {
          const result = await runtime.inspect({ key: k }, { requireVisible: true });
          out[k] = result.count === 1 && await result.loc.isVisible();
          details[k] = { count: result.count, hit: result.hit, error: out[k] ? null : '当前页没有唯一可见目标' };
        } catch (error) { out[k] = false; details[k] = { code: error.code, error: error.message }; }
      }
      return { verify: out, details };
    },
    // 核心 key 巡检:探核心 key(默认内置 coreKeys,可传子集覆盖)是否都在当前页可见,
    // 返回 {verify, failed, core}。failed 非空 = 有核心 key 失效(进入段/复位/掉登录检测会塌),供告警。
    async verifyCoreKeys(keys) {
      await ensureConnected();
      const core = (Array.isArray(keys) && keys.length) ? keys : CORE_KEYS;
      const out = {};
      for (const k of core) out[k] = await isKeyVisible(k);
      return { verify: out, failed: failedCoreKeys(core, out), core };
    },
    async goto(url, args = {}) {
      await ensureConnected();
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: Math.max(1, args.timeout_ms ?? DEFAULT_TIMEOUT) });
      return { url: page.url(), title: await page.title() };
    },
    // 用例间硬复位:reload 顶层清前端瞬态(选中/展开/弹窗/输入残留/焦点),等 vm iframe 就绪,再**主动
    // 导航回首页**——reload 只重载当前 SPA 路由(客户端常驻),上一条用例可能把路由停在任务详情/其它 Tab,
    // 故 reload 后探首页锚点、不在首页就点『首页』导航回去(见 ensureOnHome)。串行执行每条 gui/e2e 前调,
    // 让进入段自导航从首页开始。首页锚点尽力等,探不到不抛(交上层就绪门禁/自愈裁决)。
    async resetHome({ readyKey = "homepageTitle", readyTimeout = 8000 } = {}) {
      await ensureConnected();
      await runtime.unmockAll();   // 清上一条遗留拦截，失败时阻塞复位
      runtime.resetResponse();
      await page.reload({ waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      await waitForContentFrame();
      await ensureOnHome([readyKey, "homeGreetingTitle"], readyTimeout);
      return { reset: true, url: page.url() };
    },
    click: (args) => operate("click", args),
    hover: (args) => operate("hover", args),
    fill: (args) => operate("fill", args),
    type: (args) => operate("type", args),
    pressKey: (args) => operate("pressKey", args),
    // 页面层 ESC:page.keyboard 发 Escape,关网页模态/浮层/下拉(只作用于被测页面内部)。快、无害。
    // 复位自愈第一招:首页疑似被网页弹窗挡住时先按它清障再重探。
    async pressEscapePage() {
      await ensureConnected();
      await page.keyboard.press("Escape");
      return { escaped: true, layer: "page" };
    },
    // OS 级 ESC:向操作系统前台窗口发 Escape,关被测页面之外的系统窗(如误触发的文件资源管理器/原生
    // 文件选择框)。走 os-key 的 pressOsEscape(平台命令,尽力而为、绝不抛);powershell 冷启动约数秒,
    // 故仅作页面层 ESC 未果后的升级手段(见 reset-home 分层自愈)。escaped=是否真发出(不支持/超时→false)。
    async pressEscapeOs() {
      const os = await pressOsEscape();
      return { escaped: os, layer: "os" };
    },
    async getText(args) { await ensureConnected(); return runtime.getText(args); },
    async waitFor(args) { await ensureConnected(); return runtime.waitFor(args); },
    async captureResponse(args) { await ensureConnected(); return runtime.captureResponse(args); },
    async waitResponse(args) { await ensureConnected(); return runtime.waitResponse(args); },
    async assertText(args) { await ensureConnected(); return runtime.assertText(args); },
    async assertVisible(args) { await ensureConnected(); return runtime.assertVisible(args); },
    async assertAbsent(args) { await ensureConnected(); return runtime.assertAbsent(args); },
    async startTrace() {
      await ensureConnected();
      if (traceContext) throw new Error("上一条执行 Trace 尚未结束");
      await ctx.tracing.start({ screenshots: true, snapshots: true, sources: false });
      traceContext = ctx;
    },
    async stopTrace(path) {
      const recording = traceContext;
      traceContext = null;
      if (!recording) throw new Error("Trace 未启动或连接已重建");
      await recording.tracing.stop(path ? { path } : {});
      return path;
    },
    async screenshot(path) {
      await ensureConnected();
      await page.screenshot({ path, fullPage: false });
      return { evidence: path };
    },
    // 截当前视口为 PNG Buffer(供执行报告上传,不落本地文件)。失败返回 null,不阻断执行。
    async shotBuffer() {
      try {
        await ensureConnected();
        return await page.screenshot({ fullPage: false, type: "png" });
      } catch {
        return null;
      }
    },
    async mockRoute(args) { await ensureConnected(); return runtime.mockRoute(args); },
    async unmockRoute(args) { return runtime.unmockRoute(args); },
    async unmockAll() { return runtime.unmockAll(); },
    mockStats() { return runtime.mockStats(); },
    async close() {
      // connectOverCDP 的 close 只断开连接,不关被测客户端
      try { await runtime.unmockAll(); } catch { /* disconnected context */ }
      if (traceContext) { try { await traceContext.tracing.stop(); } catch {} }
      if (browser) { try { await browser.close(); } catch { /* 已断开 */ } }
      traceContext = null;
      runtime.resetConnection();
      browser = null; page = null; ctx = null; testidInjected = false;
    },
  };
}
