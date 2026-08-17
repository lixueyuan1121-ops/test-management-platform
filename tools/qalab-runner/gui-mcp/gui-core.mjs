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

const SELECTORS_PATH = join(dirname(fileURLToPath(import.meta.url)), "selectors.json");

// 页面探测脚本(浏览器 context 内执行):扫可见可交互元素 + 按稳定性打分的候选选择器。
export const DISCOVER_SCRIPT = function () {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const isBEM = (cls) => /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(cls);
  const isHash = (cls) => /[A-Za-z0-9]{6,}$/.test(cls) && !/[-_]/.test(cls.slice(-8));
  const genCandidates = (el) => {
    const cands = [];
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testid) cands.push({ sel: `[data-testid="${testid}"]`, score: 100, by: "testid", value: testid });
    if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ sel: `#${CSS.escape(el.id)}`, score: 90, by: "css", value: `#${el.id}` });
    const aria = el.getAttribute("aria-label");
    if (aria && aria.length < 60) cands.push({ sel: `[aria-label="${aria}"]`, score: 80, by: "label", value: aria });
    const name = el.getAttribute("name");
    if (name) cands.push({ sel: `[name="${name}"]`, score: 75, by: "css", value: `[name="${name}"]` });
    const ph = el.getAttribute("placeholder");
    if (ph) cands.push({ sel: `[placeholder="${ph}"]`, score: 70, by: "placeholder", value: ph });
    const role = el.getAttribute("role") || el.tagName.toLowerCase();
    if (role && aria) cands.push({ sel: `${role}[aria-label="${aria}"]`, score: 68, by: "role", value: role, name: aria });
    const classes = Array.from(el.classList);
    const bem = classes.filter(isBEM);
    const stable = bem.length ? bem : classes.filter((c) => !isHash(c) && c.length > 3);
    if (stable.length) { const sel = stable.map((c) => `.${CSS.escape(c)}`).join(""); cands.push({ sel, score: bem.length ? 60 : 45, by: "css", value: sel }); }
    const txt = (el.innerText || el.textContent || "").trim().slice(0, 30);
    if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ sel: `text=${txt}`, score: 40, by: "text", value: txt });
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");
    if (type) cands.push({ sel: `${tag}[type="${type}"]`, score: 30, by: "css", value: `${tag}[type="${type}"]` });
    return cands.sort((a, b) => b.score - a.score);
  };
  const sel = "a, button, [role=button], [role=tab], [role=menuitem], input, textarea, select, [contenteditable=true], [onclick], [class*=btn], [class*=action], [class*=nav__item], [class*=menu-item]";
  const set = new Set(document.querySelectorAll(sel));
  for (const el of document.querySelectorAll("body *")) {
    if (set.has(el)) continue;
    try { if (getComputedStyle(el).cursor === "pointer") set.add(el); } catch { /* 忽略 */ }
  }
  const elements = [...set].filter((el) => {
    const t = (el.innerText || "").trim();
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (set.has(p) && (p.innerText || "").trim() === t) return false;
    }
    return true;
  });
  const out = [];
  for (const el of elements) {
    if (!isVisible(el)) continue;
    const candidates = genCandidates(el);
    if (!candidates.length) continue;
    out.push({ tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "", text: (el.innerText || el.value || "").trim().slice(0, 40), candidates: candidates.slice(0, 4), best: candidates[0] });
  }
  return out;
};

// 工厂:创建一个 gui-core 实例(持有 browser/page 连接态)。
// opts: { cdpUrl, timeout, selectorsPath, registry, vmIframe }
// registry/vmIframe 若传入则直接用之(runner 从 API 拉的注册表),否则 readFileSync 内置 selectors.json。
export function createGuiCore(opts = {}) {
  const CDP_URL = opts.cdpUrl || process.env.CDP_URL || "http://127.0.0.1:9222";
  const DEFAULT_TIMEOUT = Number(opts.timeout || process.env.GUI_TIMEOUT_MS || 10000);
  // let(非 const):setRegistry 就地换表后,闭包引用它的 resolveKey/isKeyVisible/scopesFor/contentFrame 立即生效。
  let REGISTRY, VM_IFRAME;
  if (opts.registry) {
    REGISTRY = opts.registry; VM_IFRAME = opts.vmIframe || "";
  } else {
    const j = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"));
    REGISTRY = j.registry; VM_IFRAME = j.vmIframe;
  }

  let browser = null;
  let page = null;

  async function ensureConnected() {
    if (browser && browser.isConnected() && page && !page.isClosed()) return;
    browser = await chromium.connectOverCDP(CDP_URL);
    const ctx = browser.contexts()[0] || (await browser.newContext());
    const pages = ctx.pages();
    page = pages.find((p) => (p.url() || "").includes("work.n.cn")) || pages[0];
    if (!page) page = await ctx.newPage();
  }

  function contentFrame() {
    const main = page.mainFrame();
    const frames = page.frames();
    const embed = frames.find((f) => f !== main && /\.work\.n\.cn/i.test(f.url() || ""));
    return embed || frames.find((f) => f !== main) || main;
  }

  async function waitForContentFrame(timeoutMs = 8000) {
    const start = Date.now();
    for (;;) {
      const f = contentFrame();
      if (f !== page.mainFrame()) return f;
      if (Date.now() - start > timeoutMs) return f;
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  function byToLocator(scope, cand) {
    switch (cand.by) {
      case "testid": return scope.getByTestId(cand.value);
      case "role": return scope.getByRole(cand.value, cand.name ? { name: cand.name } : undefined);
      case "label": return scope.getByLabel(cand.value);
      case "text": return scope.getByText(cand.value);
      case "placeholder": return scope.getByPlaceholder(cand.value);
      case "css":
      default: return scope.locator(cand.value);
    }
  }

  function scopesFor(frame) {
    const shell = { name: "shell", scope: page };
    const vm = { name: "vm", scope: page.frameLocator(VM_IFRAME) };
    if (frame === "shell") return [shell];
    if (frame === "vm") return [vm];
    return [shell, vm];
  }

  async function resolveKey(key, { timeout = DEFAULT_TIMEOUT, requireVisible = true } = {}) {
    const entry = REGISTRY[key];
    if (!entry) throw new Error(`未定义语义 key "${key}"(selectors.json 无此项;先看 listKeys)`);
    const plan = [];
    for (const s of scopesFor(entry.frame)) for (const cand of entry.candidates) plan.push({ s, cand });
    const end = Date.now() + timeout;
    for (;;) {
      for (const { s, cand } of plan) {
        try {
          const loc = byToLocator(s.scope, cand).first();
          if ((await loc.count()) > 0 && (!requireVisible || (await loc.isVisible().catch(() => false)))) {
            return { loc, hit: { scope: s.name, by: cand.by, value: cand.value || cand.name } };
          }
        } catch { /* 试下一个候选 */ }
      }
      if (Date.now() >= end) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    const tried = plan.map(({ s, cand }) => `${s.name}:${cand.by}=${cand.value || cand.name}`).join(" | ");
    throw new Error(`未命中 key "${key}"(${entry.desc || ""});已试(含 iframe): ${tried} → 更新 selectors.json 的 "${key}".candidates`);
  }

  async function resolveTarget(args, { requireVisible = true } = {}) {
    if (args.key) return await resolveKey(args.key, { requireVisible, timeout: args.timeout_ms || DEFAULT_TIMEOUT });
    if (args.selector) return { loc: contentFrame().locator(args.selector).first(), hit: { scope: "content", by: "css", value: args.selector } };
    throw new Error("需要提供 key(语义,优先)或 selector(原始 CSS)之一");
  }

  // 某语义 key 当前在页面上是否可见(不抛错,快速探一次;用于 waitResponse 轮询生成态)。
  async function isKeyVisible(key) {
    const entry = REGISTRY[key];
    if (!entry) return false;
    for (const s of scopesFor(entry.frame)) {
      for (const cand of entry.candidates) {
        try {
          const loc = byToLocator(s.scope, cand).first();
          if ((await loc.count()) > 0 && (await loc.isVisible().catch(() => false))) return true;
        } catch { /* 试下一个 */ }
      }
    }
    return false;
  }

  // ---- 对外操作(server 和 StepExecutor 共用)----
  return {
    get registry() { return REGISTRY; },
    // 就地换注册表(runner 每条 gui/e2e 用例执行前按 project_id 从 API 拉后调):只换 REGISTRY/VM_IFRAME,
    // 不动 browser/page 连接态。闭包引用它俩的 resolveKey/isKeyVisible/scopesFor/contentFrame 随即生效。
    setRegistry(registry, vmIframe) { REGISTRY = registry || {}; VM_IFRAME = vmIframe || VM_IFRAME; },
    ensureConnected,
    contentFrame,

    async connect() {
      await ensureConnected();
      const f = await waitForContentFrame();
      return { connected: true, title: await page.title(), url: page.url(), frame_url: f.url(), in_iframe: f !== page.mainFrame() };
    },
    listKeys() {
      return { count: Object.keys(REGISTRY).length, keys: Object.entries(REGISTRY).map(([k, v]) => ({ key: k, frame: v.frame, desc: v.desc })) };
    },
    async probe({ contains = "", limit = 40 } = {}) {
      await ensureConnected();
      const cf = contentFrame();
      const scopes = [{ frame: "shell", target: page.mainFrame() }];
      if (cf !== page.mainFrame()) scopes.push({ frame: "vm", target: cf });
      const groups = [];
      for (const { frame, target } of scopes) {
        let els = [];
        try { els = await target.evaluate(DISCOVER_SCRIPT); } catch (e) { groups.push({ frame, url: target.url(), error: e.message, elements: [] }); continue; }
        if (contains) els = els.filter((e) => (e.text || "").includes(contains));
        groups.push({ frame, url: target.url(), total: els.length, elements: els.slice(0, limit) });
      }
      return { groups };
    },
    async goto(url) {
      await ensureConnected();
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      return { url: page.url(), title: await page.title() };
    },
    async click(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      await loc.click({ timeout: DEFAULT_TIMEOUT });
      return { clicked: args.key || args.selector, via: hit };
    },
    async fill(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      await loc.fill(args.text, { timeout: DEFAULT_TIMEOUT });
      return { filled: args.key || args.selector, via: hit };
    },
    async getText(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args, { requireVisible: false });
      return { text: (await loc.textContent()) ?? "", via: hit };
    },
    async waitFor(args) {
      await ensureConnected();
      const timeout = args.timeout_ms || DEFAULT_TIMEOUT;
      if (args.key) await resolveKey(args.key, { timeout, requireVisible: true });
      else await contentFrame().locator(args.selector).first().waitFor({ state: "visible", timeout });
      return { visible: args.key || args.selector };
    },
    // 等 AI 回复生成完成(e2e 关键):发消息后调。判据 = stopBtn(生成中标志)消失 且 出现带 has-copy 的
    // answerBubble(流式输出完成)。带上限 timeout_ms(默认 90s),超时不抛崩溃、返回 {done:false} 由调用方判 fail。
    // 逻辑:先等生成"起来"(stopBtn 出现,最多 quietMs 内没起来就认为无需等),再等它"结束"(stopBtn 消失 + answerBubble 就绪)。
    async waitResponse({ timeout_ms = 90000 } = {}) {
      await ensureConnected();
      const start = Date.now();
      const stopKey = REGISTRY.stopBtn ? "stopBtn" : null;
      const doneKey = REGISTRY.answerBubble ? "answerBubble" : null;
      let sawGenerating = false;
      for (;;) {
        const generating = stopKey ? await isKeyVisible(stopKey) : false;
        if (generating) sawGenerating = true;
        const answered = doneKey ? await isKeyVisible(doneKey) : false;
        // 完成判据:不在生成中 且 已出现完成的回复气泡(has-copy)
        if (!generating && answered && (sawGenerating || Date.now() - start > 3000)) {
          return { done: true, elapsed_ms: Date.now() - start, saw_generating: sawGenerating };
        }
        if (Date.now() - start > timeout_ms) {
          return { done: false, elapsed_ms: Date.now() - start, saw_generating: sawGenerating, reason: `等待回复超时(>${timeout_ms}ms):generating=${generating} answered=${answered}` };
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    },
    // 断言文本:返回 {pass, actual, expected, mode, via}(不抛错,由调用方按 pass 判定)
    async assertText(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args, { requireVisible: false });
      const actual = (await loc.textContent()) ?? "";
      const pass = args.contains ? actual.includes(args.expected) : actual.trim() === args.expected;
      return { pass, actual: actual.trim().slice(0, 200), expected: args.expected, mode: args.contains ? "contains" : "equals", via: hit };
    },
    async assertVisible(args) {
      await ensureConnected();
      try {
        await resolveTarget(args, { requireVisible: true });
        return { pass: true, target: args.key || args.selector };
      } catch (e) {
        return { pass: false, target: args.key || args.selector, error: e.message };
      }
    },
    async screenshot(path) {
      await ensureConnected();
      await page.screenshot({ path, fullPage: false });
      return { evidence: path };
    },
    async close() {
      // connectOverCDP 的 close 只断开连接,不关被测客户端
      if (browser) { try { await browser.close(); } catch { /* 已断开 */ } }
      browser = null; page = null;
    },
  };
}
