#!/usr/bin/env node
// GUI MCP server —— 把 namiclaw(纳米Work,Electron)的 CDP 操作封装成 Claude Code 可调用的固定工具。
// Claude 只负责"用哪个 selector、断言什么",不现写 Playwright,保证稳定性与证据链。
//
// 依赖:npm i (在本目录) → @modelcontextprotocol/sdk + playwright-core
// 前提:namiclaw 已带 --remote-debugging-port=9222 启动(由 runner 的 ensureNamiclaw 保证)。
//
// 暴露工具(Claude 侧名字为 mcp__gui__<tool>):
//   gui_connect            连接到 CDP,返回页面标题/URL
//   gui_goto(url)          导航当前页
//   gui_click(selector)    点击
//   gui_fill(selector,text)填入文本
//   gui_get_text(selector) 取元素文本
//   gui_wait_for(selector,timeout_ms) 等元素可见
//   gui_assert_text(selector,expected,contains) 断言文本(相等或包含)
//   gui_screenshot(path)   截图存证据,返回路径

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { chromium } from "playwright-core";

const CDP_URL = process.env.CDP_URL || "http://127.0.0.1:9222";
const DEFAULT_TIMEOUT = Number(process.env.GUI_TIMEOUT_MS || 10000);

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

// 工具定义(inputSchema 用 JSON Schema,Claude 据此调用)
const TOOLS = [
  { name: "gui_connect", description: "连接到 namiclaw 的 CDP 调试端口,返回当前页面标题和 URL。GUI 用例第一步必须先调它。", inputSchema: { type: "object", properties: {} } },
  { name: "gui_goto", description: "把当前页面导航到指定 URL。", inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  { name: "gui_click", description: "点击匹配 CSS selector 的元素。", inputSchema: { type: "object", properties: { selector: { type: "string" } }, required: ["selector"] } },
  { name: "gui_fill", description: "向匹配 selector 的输入框填入文本(会先清空)。", inputSchema: { type: "object", properties: { selector: { type: "string" }, text: { type: "string" } }, required: ["selector", "text"] } },
  { name: "gui_get_text", description: "返回匹配 selector 的元素的可见文本。", inputSchema: { type: "object", properties: { selector: { type: "string" } }, required: ["selector"] } },
  { name: "gui_wait_for", description: "等待匹配 selector 的元素在指定毫秒内变为可见;超时抛错。", inputSchema: { type: "object", properties: { selector: { type: "string" }, timeout_ms: { type: "number" } }, required: ["selector"] } },
  { name: "gui_assert_text", description: "断言 selector 元素文本等于(默认)或包含 expected;不满足则判定失败。", inputSchema: { type: "object", properties: { selector: { type: "string" }, expected: { type: "string" }, contains: { type: "boolean" } }, required: ["selector", "expected"] } },
  { name: "gui_screenshot", description: "对当前页面截图并保存到 path,返回保存路径(用作 evidence)。", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
];

const ok = (text) => ({ content: [{ type: "text", text: String(text) }] });
const fail = (text) => ({ content: [{ type: "text", text: String(text) }], isError: true });

async function dispatch(name, args) {
  if (name !== "gui_connect") await ensureConnected();
  switch (name) {
    case "gui_connect": {
      await ensureConnected();
      return ok(JSON.stringify({ connected: true, title: await page.title(), url: page.url() }));
    }
    case "gui_goto":
      await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      return ok(JSON.stringify({ url: page.url(), title: await page.title() }));
    case "gui_click":
      await page.click(args.selector, { timeout: DEFAULT_TIMEOUT });
      return ok(`clicked ${args.selector}`);
    case "gui_fill":
      await page.fill(args.selector, args.text, { timeout: DEFAULT_TIMEOUT });
      return ok(`filled ${args.selector}`);
    case "gui_get_text": {
      const t = await page.textContent(args.selector, { timeout: DEFAULT_TIMEOUT });
      return ok(JSON.stringify({ text: t }));
    }
    case "gui_wait_for":
      await page.waitForSelector(args.selector, { state: "visible", timeout: args.timeout_ms || DEFAULT_TIMEOUT });
      return ok(`visible ${args.selector}`);
    case "gui_assert_text": {
      const actual = (await page.textContent(args.selector, { timeout: DEFAULT_TIMEOUT })) ?? "";
      const pass = args.contains ? actual.includes(args.expected) : actual.trim() === args.expected;
      const res = { pass, actual: actual.trim(), expected: args.expected, mode: args.contains ? "contains" : "equals" };
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
