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
import { validCands, pickCandidates } from "./candidates.mjs";
import { pickCoreKeys, failedCoreKeys } from "../core-keys.mjs";
import { pressOsEscape } from "../os-key.mjs";

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
    const r = el.getBoundingClientRect();
    out.push({ tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || "", text: (el.innerText || el.value || "").trim().slice(0, 40), rect: { x: r.left, y: r.top, w: r.width, h: r.height }, candidates: candidates.slice(0, 4), best: candidates[0] });
  }
  return out;
};

// 从 CDP context 的 pages() 结果里挑"就绪可用"的页面:优先 url 含业务域 work.n.cn 的页,
// 否则首个未关闭页;一个可用页都没有(冷启动时页面 target 尚未在 CDP 注册)→ null,由调用方
// 继续轮询等待。纯函数(无 playwright 依赖),单测见 pick-ready-page.test.mjs。
export function pickReadyPage(pages) {
  const open = (Array.isArray(pages) ? pages : []).filter((p) => p && !p.isClosed?.());
  if (!open.length) return null;
  return open.find((p) => (p.url() || "").includes("work.n.cn")) || open[0];
}

// 工厂:创建一个 gui-core 实例(持有 browser/page 连接态)。
// opts: { cdpUrl, timeout, selectorsPath, registry, vmIframe }
// registry/vmIframe 若传入则直接用之(runner 从 API 拉的注册表),否则 readFileSync 内置 selectors.json。
export function createGuiCore(opts = {}) {
  const CDP_URL = opts.cdpUrl || process.env.CDP_URL || "http://127.0.0.1:9222";
  const DEFAULT_TIMEOUT = Number(opts.timeout || process.env.GUI_TIMEOUT_MS || 10000);
  // 冷启动时等页面 target 在 CDP 注册出来的上限(端口活≠页面就绪,见 ensureConnected)。
  const PAGE_READY_TIMEOUT = Number(opts.pageReadyTimeout || process.env.CDP_PAGE_READY_MS || 15000);
  // let(非 const):setRegistry 就地换表后,闭包引用它的 resolveKey/isKeyVisible/scopesFor/contentFrame 立即生效。
  let REGISTRY, VM_IFRAME;
  if (opts.registry) {
    REGISTRY = opts.registry; VM_IFRAME = opts.vmIframe || "";
  } else {
    const j = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"));
    REGISTRY = j.registry; VM_IFRAME = j.vmIframe;
  }

  // 内置兜底副本(始终从仓库 selectors.json 读一份):DB 某 key 候选全坏/缺时逐 key 回落到内置同名 key。
  let BUILTIN = {};
  try { BUILTIN = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8")).registry || {}; }
  catch { BUILTIN = {}; }

  // 核心 key 清单(进入/首页/登录类):单一事实源在 selectors.json 顶层 coreKeys(见 core-keys.mjs)。
  // 供 verify 巡检默认目标 + 失效告警。读不到 → []（巡检退化为按传入 keys,不误报）。
  let CORE_KEYS = [];
  try { CORE_KEYS = pickCoreKeys(JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"))); }
  catch { CORE_KEYS = []; }

  let browser = null;
  let page = null;

  async function ensureConnected() {
    if (browser && browser.isConnected() && page && !page.isClosed()) return;
    browser = await chromium.connectOverCDP(CDP_URL);
    const ctx = browser.contexts()[0] || (await browser.newContext());
    // 冷启动竞态:CDP 端口先活、渲染进程的页面 target 后注册,刚连上时 ctx.pages() 可能仍空。
    // 此时若直接 ctx.newPage() 会撞 Electron 的 Target.createTarget: Not supported(不支持建 target),
    // 导致冷启动首条用例复位失败。故轮询等页面 target 出现(≤PAGE_READY_TIMEOUT),拿到就用;
    // 等满仍无页面才回落 newPage(非 Electron/特殊场景的兜底,正常路径不会走到)。
    const end = Date.now() + PAGE_READY_TIMEOUT;
    for (;;) {
      const p = pickReadyPage(ctx.pages());
      if (p) { page = p; return; }
      if (Date.now() >= end) break;
      await new Promise((r) => setTimeout(r, 300));
    }
    page = await ctx.newPage();
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
    // vm:业务 iframe(现状 <vm_id>.work.n.cn 嵌在顶层 work.n.cn/claw 里)。兼容"子域名扁平化"——
    // 若客户端改成顶层直接加载 <vm_id>.work.n.cn(业务上顶层、无内嵌 iframe),vm iframe 不存在时
    // 回退 shell(顶层 page)兜底,vm key 照常定位,无需改 selectors.json 的 frame 归属。
    // 现状(业务在 iframe)零副作用:iframe 里能命中就不会走到 shell。
    if (frame === "vm") return [vm, shell];
    // url:<子串> —— 从 page.frames() 扁平列表(含任意深度嵌套)找 url 含该子串的首个 Frame,
    // 直接作为定位 scope(Frame 与 Page/FrameLocator 同一套定位 API,byToLocator 无需分支)。
    // 找不到(目标 frame 未加载/页面结构变)→ 回退 [shell, vm] 再试一遍(与 auto 一致的容错)。
    if (typeof frame === "string" && frame.startsWith("url:")) {
      const pat = frame.slice(4);
      const f = pat && page.frames().find((fr) => (fr.url() || "").includes(pat));
      if (f) return [{ name: "urlframe", scope: f }];
      return [shell, vm];
    }
    return [shell, vm];
  }

  async function resolveKey(key, { timeout = DEFAULT_TIMEOUT, requireVisible = true } = {}) {
    const entry = REGISTRY[key];
    if (!entry) throw new Error(`未定义语义 key "${key}"(selectors.json 无此项;先看 listKeys)`);
    const plan = [];
    const cands = pickCandidates(entry.candidates, (BUILTIN[key] || {}).candidates);
    for (const s of scopesFor(entry.frame)) for (const cand of cands) plan.push({ s, cand });
    const end = Date.now() + timeout;
    // 一个候选可能匹配多个元素(尤其 by:text 子串,如"首页"命中导航项+页面别处文案+隐藏面板)。
    // 不锁死 .first():要求可见时在前若干个匹配里挑第一个"可见"的,避免 first 恰好是隐藏/错位元素时
    // 明明有可见的目标却判"未命中"。单匹配 key 行为不变(scan=1,即原 first)。
    const MAX_MATCH_SCAN = 5;
    for (;;) {
      for (const { s, cand } of plan) {
        try {
          const base = byToLocator(s.scope, cand);
          const total = await base.count();
          if (total === 0) continue;
          const hit = { scope: s.name, by: cand.by, value: cand.value || cand.name };
          if (!requireVisible) return { loc: base.first(), hit };
          const scan = Math.min(total, MAX_MATCH_SCAN);
          for (let i = 0; i < scan; i++) {
            const one = base.nth(i);
            if (await one.isVisible().catch(() => false)) return { loc: one, hit };
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
    const cands = pickCandidates(entry.candidates, (BUILTIN[key] || {}).candidates);
    for (const s of scopesFor(entry.frame)) {
      for (const cand of cands) {
        try {
          // 与 resolveKey 同口径:不锁死 .first(),扫前若干个匹配,任一可见即算命中
          // (by:text 子串常多匹配,first 恰是隐藏元素时不应误判"不可见")。
          const base = byToLocator(s.scope, cand);
          const scan = Math.min(await base.count(), 5);
          for (let i = 0; i < scan; i++) {
            if (await base.nth(i).isVisible().catch(() => false)) return true;
          }
        } catch { /* 试下一个 */ }
      }
    }
    return false;
  }

  // ---- 对外操作(server 和 StepExecutor 共用)----
  return {
    get registry() { return REGISTRY; },
    get coreKeys() { return CORE_KEYS.slice(); },
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
    async probe({ contains = "", limit = 40, screenshot = false } = {}) {
      await ensureConnected();
      // 多级页面:遍历页面所有 frame(Playwright 的 page.frames() 已含任意深度的嵌套 iframe),
      // 逐 frame 跑发现脚本。主框架标 shell;主 vm iframe(.work.n.cn)标 vm;其余嵌套 iframe 标 iframe。
      const main = page.mainFrame();
      const vm = contentFrame();   // 主内容 iframe(与执行侧 contentFrame 同源)
      const frameLabel = (f) =>
        f === main ? "shell" : f === vm ? "vm" : "iframe";
      // frameMatch:加为 key 时写入 selector_key.frame 的值。shell/vm 沿用旧语义;深层 iframe
      // 取 url:<hostname>——执行侧 scopesFor 据此从 page.frames() 扁平查找该 Frame 定位。
      const frameMatch = (f) => {
        if (f === main) return "shell";
        if (f === vm) return "vm";
        const u = f.url() || "";
        try { const h = new URL(u).hostname; if (h) return `url:${h}`; } catch { /* 非法/相对 url,退回截断 */ }
        return `url:${u.slice(0, 80)}`;
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
          els = await target.evaluate(DISCOVER_SCRIPT);
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
        // 无元素的 frame 不产空组(减少噪音),但保留有错误的组供排查。
        if (els.length) groups.push({ frame, frameMatch: fmatch, url: target.url(), total: els.length, elements: els.slice(0, limit) });
      }
      return { groups, pageSize, screenshotBuffer };
    },
    // 校验一批语义 key 是否在当前页命中(逐个 isKeyVisible,复用同一定位引擎)。
    // 供 runner 的 probe verify 模式用:回归确认某作用域已登记的 key 仍能在页面上定位到。
    async verifyKeys(keys) {
      await ensureConnected();
      const out = {};
      for (const k of (keys || [])) out[k] = await isKeyVisible(k);
      return { verify: out };
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
    async goto(url) {
      await ensureConnected();
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      return { url: page.url(), title: await page.title() };
    },
    // 用例间硬复位:reload 顶层回初始加载态(清全部前端瞬态:选中/展开/弹窗/输入残留/焦点),
    // 等 vm iframe 就绪(waitForContentFrame,不依赖业务选择器);可选再等首页锚点就绪(尽力,失败不阻断)。
    // 串行执行时每条 gui/e2e 前调,消除上一条遗留状态污染,让进入段自导航从初始主界面开始。
    async resetHome({ readyKey = "homepageTitle", readyTimeout = 8000 } = {}) {
      await ensureConnected();
      await page.reload({ waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      await waitForContentFrame();
      if (readyKey && REGISTRY[readyKey]) {
        try { await resolveKey(readyKey, { timeout: readyTimeout, requireVisible: true }); }
        catch { /* 首页锚点尽力而为,不阻断复位 */ }
      }
      return { reset: true, url: page.url() };
    },
    async click(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      await loc.click({ timeout: DEFAULT_TIMEOUT });
      return { clicked: args.key || args.selector, via: hit };
    },
    // 鼠标悬停到目标元素(触发 mouseover/mouseenter + CSS :hover);常用于"悬停才显示"的
    // 菜单/浮层:hover → wait_for(浮层出现) → click/assert。定位与 click 同一套引擎(语义 key 优先)。
    async hover(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      await loc.hover({ timeout: DEFAULT_TIMEOUT });
      return { hovered: args.key || args.selector, via: hit };
    },
    async fill(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      try {
        await loc.fill(args.text, { timeout: DEFAULT_TIMEOUT });
      } catch (e) {
        // 富文本/自定义元素(如纳米 Work 的 <chat-compose-rich-textarea>)不是标准 <input>/<textarea>,
        // Playwright 的 fill 直接拒绝。兜底:先找元素内部的 contenteditable/textarea 填;找不到就
        // click 聚焦后用键盘逐字输入。兜底再失败则抛原错(退化为当前的 selector 阻塞,不比原来差)。
        if (!/not an\b|contenteditable|is not an <input>|Element is not/i.test(e.message || "")) throw e;
        const inner = loc.locator('[contenteditable="true"], [contenteditable=""], textarea, input').first();
        if (await inner.count().catch(() => 0)) {
          try { await inner.fill(args.text, { timeout: DEFAULT_TIMEOUT }); }
          catch { await inner.click({ timeout: DEFAULT_TIMEOUT }); await page.keyboard.type(args.text); }
        } else {
          await loc.click({ timeout: DEFAULT_TIMEOUT });
          await page.keyboard.press("Control+A").catch(() => {});   // 清空已有内容再输入
          await page.keyboard.type(args.text);
        }
        return { filled: args.key || args.selector, via: hit, fallback: "contenteditable" };
      }
      return { filled: args.key || args.selector, via: hit };
    },
    // type —— 逐字符追加输入（不清空原有内容）。
    // 先 click 聚焦目标；支持 key/selector 定位；文本末尾不发送，只模拟键盘打字。
    // 适用于：需要在输入框现有内容后追加文本、或对 fill 兼容性差的富文本组件二次输入。
    async type(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args);
      await loc.click({ timeout: DEFAULT_TIMEOUT });   // 聚焦，光标保留原位（通常末尾）
      await page.keyboard.type(String(args.text ?? ""));
      return { typed: args.key || args.selector, via: hit };
    },
    // pressKey —— 向目标元素（或全局页面）发送单个按键（如 End / Home / Enter / Escape / Tab）。
    // args.key_name: 必填，Playwright 按键名（如 "End"/"Home"/"Enter"/"Escape"/"Tab"/"Control+A"）。
    // args.target_key / args.selector: 可选；有值时先 click 聚焦再按键，无值时向全局页面发。
    // 常与 type 配合：pressKey(End) → type(追加文字)，或 pressKey(Enter) 提交表单。
    async pressKey(args) {
      await ensureConnected();
      const key = String(args.key_name || "");
      if (!key) throw new Error("pressKey: 缺少 key_name（如 End / Enter / Escape）");
      // 有定位目标时先聚焦；target_key 是选择器语义 key，selector 是 CSS/XPath。
      if (args.target_key || args.selector) {
        const targetArgs = args.target_key ? { key: args.target_key } : { selector: args.selector };
        const { loc } = await resolveTarget(targetArgs);
        await loc.click({ timeout: DEFAULT_TIMEOUT });
      }
      await page.keyboard.press(key);
      return { pressed: key };
    },
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
    // 断言文本:返回 {pass, actual, expected, mode, negate, via}(不抛错,由调用方按 pass 判定)。
    // negate=true 表示「否定断言」:期望文本**不**等于/不包含 expected(用于"不显示/已关闭/不含 X")。
    // 物理上无法用正向 equals 表达否定(元素不存在时 textContent 为空,equals 恒失败 → 假 fail),故显式支持。
    async assertText(args) {
      await ensureConnected();
      const { loc, hit } = await resolveTarget(args, { requireVisible: false });
      const actual = ((await loc.textContent()) ?? "").trim();
      const matched = args.contains ? actual.includes(args.expected) : actual === args.expected;
      const pass = args.negate ? !matched : matched;
      return { pass, actual: actual.slice(0, 200), expected: args.expected, mode: args.contains ? "contains" : "equals", negate: !!args.negate, via: hit };
    },
    // 断言元素可见。失败时区分两种性质(供调用方归类 fail_kind):
    //   - locatable=false:元素在 DOM 里压根定位不到(key 未注册/候选没覆盖/selector 0 匹配)→ 选择器阻塞(selector)。
    //   - locatable=true :元素在 DOM 里但不可见(隐藏/未渲染出来)→ 真功能问题(business,"该可见却没可见")。
    // 基于 count()+isVisible() 判定,对 key 与裸 selector 都可靠(裸 selector 经 resolveTarget 不抛错,
    // 必须显式查 count,否则会误判"可见")。
    async assertVisible(args) {
      await ensureConnected();
      let loc;
      try { ({ loc } = await resolveTarget(args, { requireVisible: false })); }
      catch (e) { return { pass: false, target: args.key || args.selector, error: e.message, locatable: false }; }
      const cnt = await loc.count().catch(() => 0);
      if (cnt === 0) return { pass: false, target: args.key || args.selector, error: "元素定位不到(选择器/key 未覆盖)", locatable: false };
      const visible = await loc.isVisible().catch(() => false);
      if (visible) return { pass: true, target: args.key || args.selector, locatable: true };
      return { pass: false, target: args.key || args.selector, error: "元素已定位但不可见", locatable: true };
    },
    // 断言元素「不存在/不可见」(否定式可见断言)。定位不到 / 0 匹配 / 存在但不可见 → 通过(这正是期望);
    // 仍可见 → 不通过(business:本应消失却还在)。对 key 与裸 selector 都可靠。
    // 用短超时:不存在的元素不必等满 DEFAULT_TIMEOUT(它本就该没有)。用于"移除后 Chip 消失""菜单关闭后消失"。
    async assertAbsent(args) {
      await ensureConnected();
      let loc;
      try { ({ loc } = await resolveTarget({ ...args, timeout_ms: Math.min(args.timeout_ms || 1500, 2000) }, { requireVisible: false })); }
      catch { return { pass: true, target: args.key || args.selector, locatable: false }; }  // 定位不到 → 已不存在
      const cnt = await loc.count().catch(() => 0);
      if (cnt === 0) return { pass: true, target: args.key || args.selector, locatable: false };  // 0 匹配 → 不存在
      const visible = await loc.isVisible().catch(() => false);
      return visible
        ? { pass: false, target: args.key || args.selector, locatable: true }   // 仍可见 → 未消失
        : { pass: true, target: args.key || args.selector, locatable: false };  // 存在但隐藏 → 视作已消失
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
    async close() {
      // connectOverCDP 的 close 只断开连接,不关被测客户端
      if (browser) { try { await browser.close(); } catch { /* 已断开 */ } }
      browser = null; page = null;
    },
  };
}
