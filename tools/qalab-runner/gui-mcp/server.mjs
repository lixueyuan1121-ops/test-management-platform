#!/usr/bin/env node
// GUI MCP server —— 把 gui-core 的操作包成 Claude Code 可调用的 MCP 工具(mcp__gui__*)。
// 核心定位/操作逻辑在 gui-core.mjs(与 runner 的 StepExecutor 共用,保证行为一致);本文件只做 MCP 协议壳。
//
// 依赖:npm i (在本目录) → @modelcontextprotocol/sdk + playwright-core
// 前提:namiclaw 已带 --remote-debugging-port=9222 启动(由 runner 的 ensureNamiclaw 保证)。
//
// 暴露工具:gui_connect / gui_list_keys / gui_probe / gui_goto / gui_click / gui_hover / gui_fill /
//           gui_get_text / gui_wait_for / gui_assert_text / gui_screenshot
// 语义 key 用法见 selectors.json 与 gui-mcp/README.md;更新选择器只改 selectors.json。

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { createGuiCore } from "./gui-core.mjs";

const gui = createGuiCore();

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
  { name: "gui_hover", description: "鼠标悬停到元素(触发悬浮态:菜单/浮层/tooltip)。用于『悬停才显示』的元素:先 gui_hover 承载元素,再 gui_wait_for 等浮层出现,然后 gui_click/gui_assert_text。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS } } },
  { name: "gui_fill", description: "向输入框填入文本(会先清空)。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, text: { type: "string" } }, required: ["text"] } },
  { name: "gui_get_text", description: "返回元素的可见文本。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS } } },
  { name: "gui_wait_for", description: "等待元素在指定毫秒内变为可见;超时抛错。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, timeout_ms: { type: "number" } } } },
  { name: "gui_assert_text", description: "断言元素文本等于(默认)或包含 expected;不满足则判定失败。target 优先传 key,兜底 selector。", inputSchema: { type: "object", properties: { ...TARGET_PROPS, expected: { type: "string" }, contains: { type: "boolean" } }, required: ["expected"] } },
  { name: "gui_screenshot", description: "对当前顶层页面截图并保存到 path,返回保存路径(用作 evidence)。", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
];

const ok = (v) => ({ content: [{ type: "text", text: typeof v === "string" ? v : JSON.stringify(v) }] });
const fail = (v) => ({ content: [{ type: "text", text: typeof v === "string" ? v : JSON.stringify(v) }], isError: true });

async function dispatch(name, args) {
  switch (name) {
    case "gui_connect": return ok(await gui.connect());
    case "gui_list_keys": return ok(gui.listKeys());
    case "gui_probe": return ok({ hint: "best=最优候选;复用就把它按 {frame,by,value} 补进 selectors.json 的某个 key", ...(await gui.probe(args)) });
    case "gui_goto": return ok(await gui.goto(args.url));
    case "gui_click": return ok(await gui.click(args));
    case "gui_hover": return ok(await gui.hover(args));
    case "gui_fill": return ok(await gui.fill(args));
    case "gui_get_text": return ok(await gui.getText(args));
    case "gui_wait_for": { await gui.waitFor(args); return ok(`visible ${args.key || args.selector}`); }
    case "gui_assert_text": {
      const res = await gui.assertText(args);
      return res.pass ? ok(res) : fail(res);   // 保持原契约:断言失败 isError=true
    }
    case "gui_screenshot": return ok(await gui.screenshot(args.path));
    default: return fail(`unknown tool ${name}`);
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
