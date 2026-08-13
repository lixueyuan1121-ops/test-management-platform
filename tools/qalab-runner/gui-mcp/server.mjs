#!/usr/bin/env node
// GUI MCP server —— 把 namiclaw(纳米Work,Electron)的 CDP 操作封装成 Claude Code 可调用的固定工具。
// Claude 只负责"用哪个元素、断言什么",不现写 Playwright,保证稳定性与证据链。
//
// 依赖:npm i (在本目录) → @modelcontextprotocol/sdk + playwright-core
// 前提:namiclaw 已带 --remote-debugging-port=9222 启动(由 runner 的 ensureNamiclaw 保证)。
//
// 【语义选择器库】元素类工具优先接**语义 key**(见 selectors.json 注册表),而非让 claude 猜 CSS:
//   - key -> {frame:shell|vm|auto, candidates:[{by,value,name?}]},引擎按候选逐个试、命中即用、失效自愈;
//   - frame='vm'/'auto' 时自动 frameLocator 穿透业务 iframe(<vm_id>.work.n.cn),根治 iframe 定位问题;
//   - 未覆盖的元素才退回原始 selector(作用在自动下钻的内容 frame)。
//   - 更新选择器:改 selectors.json(git 拉取/更新),无需改本文件。gui_list_keys 可列出全部 key。
//
// 暴露工具(Claude 侧名字为 mcp__gui__<tool>):
//   gui_connect                连接 CDP,返回顶层标题/URL + 内容 frame URL
//   gui_list_keys              列出注册表所有语义 key(key/frame/desc),用例定位前先看有哪些
//   gui_probe                  注册表没覆盖时:扫当前页可交互元素,返回按稳定性排序的候选选择器
//   gui_goto(url)              导航顶层页
//   gui_click({key|selector})  点击
//   gui_fill({key|selector,text}) 填入文本
//   gui_get_text({key|selector})  取元素文本
//   gui_wait_for({key|selector,timeout_ms}) 等元素可见
//   gui_assert_text({key|selector,expected,contains}) 断言文本(相等或包含)
//   gui_screenshot(path)       截图存证据,返回路径

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CDP_URL = process.env.CDP_URL || "http://127.0.0.1:9222";
const DEFAULT_TIMEOUT = Number(process.env.GUI_TIMEOUT_MS || 10000);

// ---- 语义选择器注册表(数据与代码分离,更新只改 JSON)----
const SELECTORS_PATH = join(dirname(fileURLToPath(import.meta.url)), "selectors.json");
const { registry: REGISTRY, vmIframe: VM_IFRAME } = JSON.parse(readFileSync(SELECTORS_PATH, "utf-8"));

let browser = null;
let page = null;

async function ensureConnected() {
  if (browser && browser.isConnected() && page && !page.isClosed()) return;
  browser = await chromium.connectOverCDP(CDP_URL);
  const contexts = browser.contexts();
  const ctx = contexts[0] || (await browser.newContext());
  const pages = ctx.pages();
  // 选主窗口:优先 work.n.cn 的页面,否则第一个
  page = pages.find((p) => (p.url() || "").includes("work.n.cn")) || pages[0];
  if (!page) page = await ctx.newPage();
}

// 真实业务 UI 在跨域子 iframe(<vm_id>.work.n.cn)里,顶层 work.n.cn/claw 只是空壳(0 文本)。
// 原始 selector(未走注册表)一律作用在“内容 frame”上:优先 work.n.cn 子域 iframe,退而第一个子 frame,
// 再退回顶层(兼容无 iframe 的简单页面)。每次现取,避免 SPA 重渲染后 frame 引用失效。
function contentFrame() {
  const main = page.mainFrame();
  const frames = page.frames();
  const embed = frames.find((f) => f !== main && /\.work\.n\.cn/i.test(f.url() || ""));
  return embed || frames.find((f) => f !== main) || main;
}

// gui_connect 时等内容 frame 就位(冷启动后 iframe 可能晚几秒才挂载);无 iframe 的页面会很快回退顶层。
async function waitForContentFrame(timeoutMs = 8000) {
  const start = Date.now();
  for (;;) {
    const f = contentFrame();
    if (f !== page.mainFrame()) return f;              // 已下钻到子 frame
    if (Date.now() - start > timeoutMs) return f;      // 超时就用顶层,别死等
    await new Promise((r) => setTimeout(r, 500));
  }
}

// ---- 语义选择器引擎(移植自 nami-work-test/lib/dom.js)----
// candidate -> Playwright Locator(scope 可以是 Page 或 FrameLocator)
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

// 依据 entry.frame 决定在哪些 scope 里找(顺序即优先级):shell=顶层文档,vm=业务 iframe(frameLocator 穿透)
function scopesFor(frame) {
  const shell = { name: "shell", scope: page };
  const vm = { name: "vm", scope: page.frameLocator(VM_IFRAME) };
  if (frame === "shell") return [shell];
  if (frame === "vm") return [vm];
  return [shell, vm]; // auto:先顶层后 iframe
}

// 解析语义 key -> 命中的 Locator(逐候选 × scope 尝试,timeout 内轮询,全不中抛带诊断的错)
async function resolveKey(key, { timeout = DEFAULT_TIMEOUT, requireVisible = true } = {}) {
  const entry = REGISTRY[key];
  if (!entry) throw new Error(`未定义语义 key "${key}"(selectors.json 无此项;先调 gui_list_keys 看有哪些)`);
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
      } catch { /* 该候选构造/查询失败,试下一个 */ }
    }
    if (Date.now() >= end) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  const tried = plan.map(({ s, cand }) => `${s.name}:${cand.by}=${cand.value || cand.name}`).join(" | ");
  throw new Error(`未命中 key "${key}"(${entry.desc || ""});已试(含 iframe): ${tried} → 选择器可能变了,更新 selectors.json 的 "${key}".candidates`);
}

// 统一取元素:优先语义 key(走注册表引擎),否则原始 selector(作用在内容 frame)
async function resolveTarget(args, { requireVisible = true } = {}) {
  if (args.key) return await resolveKey(args.key, { requireVisible });
  if (args.selector) return { loc: contentFrame().locator(args.selector).first(), hit: { scope: "content", by: "css", value: args.selector } };
  throw new Error("需要提供 key(语义,优先)或 selector(原始 CSS)之一");
}

// ---- 页面探测(移植自 nami-work-test/lib/snapshot.js)----
// 在浏览器 context 内跑:扫可见可交互元素,给每个生成按稳定性打分排序的候选选择器。
// 用途:注册表没覆盖的新模块,claude 当场探一下,拿 best 候选直接用(或回报给人补进 selectors.json)。
const DISCOVER_SCRIPT = function () {
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
  // 补 computed cursor:pointer 的元素:Vue/SPA 里大量可点击是无语义标签的 div/li(如导航项),
  // 只靠标签选择器会漏。
  for (const el of document.querySelectorAll("body *")) {
    if (set.has(el)) continue;
    try { if (getComputedStyle(el).cursor === "pointer") set.add(el); } catch { /* 忽略 */ }
  }
  // 去噪:内层元素若只是"外层同一可点击项的文本包装"(祖先在集合里且 innerText 完全相同)就丢掉,
  // 只留最外层那个(它往往带更稳的 class,如导航 li.sidebar-nav__item)。空文本的 input 不受影响。
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

// 工具定义(inputSchema 用 JSON Schema,Claude 据此调用)
const TARGET_PROPS = {
  key: { type: "string", description: "语义 key(优先;可先用 gui_list_keys 查看所有 key)" },
  selector: { type: "string", description: "原始 CSS selector(注册表未覆盖该元素时的兜底)" },
};
const TOOLS = [
  { name: "gui_connect", description: "连接到 namiclaw 的 CDP 调试端口,返回顶层标题/URL 及自动下钻到的内容 frame URL(in_iframe=true 表示已进入 <vm_id>.work.n.cn 业务 iframe)。GUI 用例第一步必须先调它。", inputSchema: { type: "object", properties: {} } },
  { name: "gui_list_keys", description: "列出语义选择器注册表里的所有 key(含 frame 与描述)。定位元素前先调它,优先用语义 key 而不是猜 CSS。", inputSchema: { type: "object", properties: {} } },
  { name: "gui_probe", description: "注册表没覆盖某元素时用:扫描当前页(顶层 shell + 业务 iframe)的可见可交互元素,返回每个元素的候选选择器(按稳定性打分排序,best 最优)。可用 contains 过滤文本。拿到候选后:一次性用就传给 gui_click 的 selector;要复用就把 best 回报给人补进 selectors.json。", inputSchema: { type: "object", properties: { contains: { type: "string", description: "只返回可见文本包含该串的元素(缩小结果)" }, limit: { type: "number", description: "每个 frame 最多返回几个(默认 40)" } } } },
  { name: "gui_goto", description: "把顶层页面导航到指定 URL。", inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "gui_click", description: "点击元素(优先传语义 key,兜底传原始 selector,二选一)。", inputSchema: { type: "object", properties: { ...TARGET_PROPS } } },
  { name: "gui_fill", description: "向输入框填入文本(会先清空)。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, text: { type: "string" } }, required: ["text"] } },
  { name: "gui_get_text", description: "返回元素的可见文本。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS } } },
  { name: "gui_wait_for", description: "等待元素在指定毫秒内变为可见;超时抛错。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, timeout_ms: { type: "number" } } } },
  { name: "gui_assert_text", description: "断言元素文本等于(默认)或包含 expected;不满足则判定失败。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, expected: { type: "string" }, contains: { type: "boolean" } }, required: ["expected"] } },
  { name: "gui_screenshot", description: "对当前顶层页面截图并保存到 path,返回保存路径(用作 evidence)。", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
];

const ok = (text) => ({ content: [{ type: "text", text: String(text) }] });
const fail = (text) => ({ content: [{ type: "text", text: String(text) }], isError: true });

async function dispatch(name, args) {
  if (name !== "gui_connect" && name !== "gui_list_keys") await ensureConnected();
  switch (name) {
    case "gui_connect": {
      await ensureConnected();
      const f = await waitForContentFrame();
      return ok(JSON.stringify({ connected: true, title: await page.title(), url: page.url(), frame_url: f.url(), in_iframe: f !== page.mainFrame() }));
    }
    case "gui_list_keys": {
      const keys = Object.entries(REGISTRY).map(([k, v]) => ({ key: k, frame: v.frame, desc: v.desc }));
      return ok(JSON.stringify({ count: keys.length, keys }));
    }
    case "gui_probe": {
      await ensureConnected();
      const limit = args.limit || 40;
      const kw = (args.contains || "").trim();
      const cf = contentFrame();
      // 扫顶层 shell + 内容 iframe(若有);标注每组的 frame 归属,方便回填 selectors.json 时定 frame
      const scopes = [{ frame: "shell", target: page.mainFrame() }];
      if (cf !== page.mainFrame()) scopes.push({ frame: "vm", target: cf });
      const groups = [];
      for (const { frame, target } of scopes) {
        let els = [];
        try { els = await target.evaluate(DISCOVER_SCRIPT); } catch (e) { groups.push({ frame, url: target.url(), error: e.message, elements: [] }); continue; }
        if (kw) els = els.filter((e) => (e.text || "").includes(kw));
        groups.push({ frame, url: target.url(), total: els.length, elements: els.slice(0, limit) });
      }
      return ok(JSON.stringify({ hint: "best=最优候选;复用就把它按 {frame,by,value} 补进 selectors.json 的某个 key", groups }));
    }
    case "gui_goto":
      await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      return ok(JSON.stringify({ url: page.url(), title: await page.title() }));
    case "gui_click": {
      const { loc, hit } = await resolveTarget(args);
      await loc.click({ timeout: DEFAULT_TIMEOUT });
      return ok(JSON.stringify({ clicked: args.key || args.selector, via: hit }));
    }
    case "gui_fill": {
      const { loc, hit } = await resolveTarget(args);
      await loc.fill(args.text, { timeout: DEFAULT_TIMEOUT });
      return ok(JSON.stringify({ filled: args.key || args.selector, via: hit }));
    }
    case "gui_get_text": {
      const { loc, hit } = await resolveTarget(args, { requireVisible: false });
      const t = (await loc.textContent()) ?? "";
      return ok(JSON.stringify({ text: t, via: hit }));
    }
    case "gui_wait_for": {
      const timeout = args.timeout_ms || DEFAULT_TIMEOUT;
      if (args.key) await resolveKey(args.key, { timeout, requireVisible: true });
      else await contentFrame().locator(args.selector).first().waitFor({ state: "visible", timeout });
      return ok(`visible ${args.key || args.selector}`);
    }
    case "gui_assert_text": {
      const { loc, hit } = await resolveTarget(args, { requireVisible: false });
      const actual = (await loc.textContent()) ?? "";
      const pass = args.contains ? actual.includes(args.expected) : actual.trim() === args.expected;
      const res = { pass, actual: actual.trim().slice(0, 200), expected: args.expected, mode: args.contains ? "contains" : "equals", via: hit };
      return pass ? ok(JSON.stringify(res)) : fail(JSON.stringify(res));
    }
    case "gui_screenshot":
      await page.screenshot({ path: args.path, fullPage: false });
      return ok(JSON.stringify({ evidence: args.path }));
    default:
      return fail(`unknown tool ${name}`);
  }
}

const server = new Server({ name: "qalab-gui", version: "1.0.0" }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));
server.setRequestHandler(CallToolRequestSchema, async (req) => {
  try {
    return await dispatch(req.params.name, req.params.arguments || {});
  } catch (e) {
    return fail(`${req.params.name} error: ${e.message}`);
  }
});

const transport = new StdioServerTransport();
await server.connect(transport);
