# WorkBuddy 对话测评执行器 Implementation Plan（系列 1/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在执行机侧新增一个通过 CDP 驱动 WorkBuddy Electron 客户端的对话执行器，能发对话、选模型档、抓出与纳米Work 同结构的四块 trace（thinking/tool_calls/artifacts/answer）+ 耗时/消耗/模型元信息，并按 `target_engine` 分流接入既有回写链路。

**Architecture:** WorkBuddy 与纳米Work 同为 Electron 客户端，故复用"CDP attach Electron"的连接范式，但**独立新写**一套 pool/runner/trace（不改动生产在跑的 `desktop-pool.js`/`desktop-runner.js`/`ws-trace.js`，零风险隔离）。关键差异：WorkBuddy 对话请求走主进程 HTTP、渲染进程 CDP 截不到流，故 trace 走 **DOM 抓取**而非截帧，但规整成同结构 JSON，让后端判定层零改动复用。对外接口（`runOne`/`runConversationTurns`/result 形状/trace 的 `buildTrace`+`reset`）与 `DesktopRunner` 对齐，使 `bin/ai-eval.js` 的 `reportRun` 无缝复用。

**Tech Stack:** Node.js（CommonJS）、Playwright（`connectOverCDP`）、既有 `tools/qalab-runner/eval/` CLI 框架。本仓库**无测试框架**（无 jest/pytest），验证一律用可 `node` 直接运行的一次性脚本 + 真机探针（对齐本仓库既有验证惯例）。

**Spec:** `docs/superpowers/specs/2026-09-07-eval-multi-product-workbuddy-design.md`

## Global Constraints

- **零改动纳米Work 现有链路**:不修改 `src/desktop-pool.js`/`src/desktop-runner.js`/`src/dialog-runner.js`/`src/ws-trace.js`/`src/work-frame.js`。WorkBuddy 全部走新增文件。（`bin/ai-eval.js` 与 `config/default.config.js` 是共享入口/配置，只允许**增量新增**分支/配置段，不改纳米路径行为。）
- **WorkBuddy = 腾讯 Electron 客户端**:`/Applications/WorkBuddy.app`（bundle `com.tencent.workbuddy.mac`，v5.5.3）。启动带调试端口靠环境变量 `WORKBUDDY_REMOTE_DEBUGGING_PORT=<port>`（**不是** `--remote-debugging-port` 命令行参数）。**严禁**把 WorkBuddy 当作网页/`ai.wps.cn`/`textarea` 处理——那是错误信息。
- **已真机坐实的选择器（2026-09-07 spike，可直接用）**:输入框 `[contenteditable="true"][role="textbox"]`；发送键 `button.cr-send-button`（或输入框内 `Enter`）；模型下拉触发 `button.cr-model-selector__trigger`（aria-label=`Select model`）；模型选项 `.cr-model-selector__item`（名 `.cr-model-selector__item-info`，倍率 `.cr-model-selector__item-credits`，当前选中 `.cr-model-selector__item--selected`）；语音键（定位参照）`button.cr-voice-trigger`；完成 footer `.conversation-finished-footer`（含「共消耗 X / 模型名 / 时间」）。
- **未坐实、必须 Task 1 真机 dump 后填**:回答正文、思考区、工具/MCP 卡、产物卡的精确选择器（spike 用闲聊 prompt 未触发思考/工具/产物）。**不得编造**，Task 1 产出后再填入 config 与抓取器。
- **trace 目标结构（对齐 `ws-trace.js::buildTrace`，不可偏离）**:顶层 `{ session_id, run_id, thinking, tool_calls, artifacts, answer, ws_captured, ws_connected }`；`tool_calls[]` 每项 `{ tool_call_id, name, original_tool_name, is_mcp, mcp_server, args, result_text, reached_result }`；`artifacts` 为数组（纳米侧亦为 DOM 另抓，无固定 schema，WorkBuddy 定义 `{ name, kind, share_link }`）。WorkBuddy 因非 WS，额外置 `ws_captured=false` 并加 `dom_captured=true` 标来源。
- **result 形状（对齐 `desktop-runner.js::_buildResult` :350-373）**:`reportRun` 实际读 `success/shareLink/artifactShareLink/answer/reportedDuration/beanCost/cost/completeReason/durationMs`，缺一不可。
- **验证需真机**:WorkBuddy 客户端已在本机 `/Applications/WorkBuddy.app` 且已登录（spike 时确认）。发对话消耗少量算力豆。每个真机步骤跑前确认 prompt 内容。
- **不消耗额外积分的探针优先**:纯 DOM 读取/选择器验证不发对话；仅端到端验证才发。

---

## 文件结构

| 文件 | 责任 | 创建/修改 |
|---|---|---|
| `tools/qalab-runner/eval/src/workbuddy-pool.js` | CDP 连接骨架:spawn WorkBuddy(带 env 调试端口)→ waitPort → connectOverCDP → 取单 page → close。对外 `init()`/`getMainPage()`/`getContext()`/`close()`。 | 创建 |
| `tools/qalab-runner/eval/src/workbuddy-dom-trace.js` | DOM trace 抓取器:`captureTurn(page, opts)` 从 DOM 抓 thinking/tool_calls/artifacts/answer + footer 元信息;`buildTrace(runId)` 返回同结构;`reset()`。 | 创建 |
| `tools/qalab-runner/eval/src/workbuddy-runner.js` | 对话驱动:开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+元信息、抓 trace。对外 `runOne(testCase)`/`runConversationTurns(turns, onTurnDone)`,返回 `_buildResult` 同形状 result。 | 创建 |
| `tools/qalab-runner/eval/config/default.config.js` | 新增 `config.workbuddy` 选择器段(仿 `config.platform`) + `config.workbuddyDesktop`(仿 `config.desktop`,executablePath/env/cdpPort)。 | 修改(仅新增段) |
| `tools/qalab-runner/eval/bin/ai-eval.js` | `runOnce` 按 `target_engine` 把 pending 拆分:workbuddy 组走 WorkbuddyPool+WorkbuddyRunner,namiwork 组走原路径。复用 `reportRun`/`failWholeGroup`/`groupIntoConversations`。 | 修改(仅新增分支) |

---

## Task 1: 真机 DOM 侦察——坐实未验证的选择器

**目的:** spike 只坐实了输入/发送/模型/footer,未坐实回答正文/思考区/工具卡/产物卡。本任务发一条**触发思考+工具+产物**的 prompt,dump 完整 DOM,产出精确选择器清单,供后续任务填入。这是"发现"任务,产出物是一份选择器记录,不是长驻代码。

**Files:**
- Create(临时): `/tmp/wb-recon/recon.js`（throwaway 探针,任务结束删除）
- 产出: 一段选择器清单(记录在本任务的验证输出里 / 贴给复核者)

**Interfaces:**
- Produces: 供 Task 2/3/4 使用的选择器常量值——`answerContainerSelector`、`answerTextSelector`、`thinkingSelector`、`toolCardSelector`、`toolNameSelector`、`toolResultSelector`、`artifactCardSelector`、`footerSelector`(`.conversation-finished-footer` 已知)、`costText/modelText/durationText` 在 footer 内的子结构。

- [ ] **Step 1: 写侦察探针**

创建 `/tmp/wb-recon/recon.js`:启动 WorkBuddy(env 调试端口)→ attach → 新建任务 → 发一条触发型 prompt → 等完成 → dump 回答区/思考区/工具卡/产物卡的 DOM 结构(标签+类名+文本样例)。

```js
const { spawn } = require('child_process');
const http = require('http');
const { chromium } = require('/Users/lixueyuan/code/test-management-platform-fresh/tools/qalab-runner/eval/node_modules/playwright');
const EXE = '/Applications/WorkBuddy.app/Contents/MacOS/Electron';
const PORT = 9335;
const PROMPT = '搜索一下 2026 年诺贝尔物理学奖得主，并整理成一个简短的 Markdown 文档';  // 触发 联网工具 + 产物
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function waitPort(port, t) { const dl = Date.now()+t; return new Promise((res,rej)=>{const tick=()=>http.get(`http://127.0.0.1:${port}/json/version`,r=>{let b='';r.on('data',d=>b+=d);r.on('end',()=>res(b));}).on('error',()=>Date.now()>dl?rej(new Error('端口超时')):setTimeout(tick,500));tick();});}
(async () => {
  const child = spawn(EXE, [], { detached:true, stdio:'ignore', env:{...process.env, WORKBUDDY_REMOTE_DEBUGGING_PORT:String(PORT)} });
  child.unref();
  await waitPort(PORT, 75000);
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
  const page = browser.contexts()[0].pages()[0];
  await sleep(2500);
  const nt = page.getByText('新建任务', { exact:false }).first();
  if (await nt.count()) { await nt.click().catch(()=>{}); await sleep(1200); }
  const input = page.locator('[contenteditable="true"][role="textbox"]').first();
  await input.click(); await input.type(PROMPT, { delay: 15 }); await sleep(400);
  await input.press('Enter');
  // 等到 footer 出现（本轮完成信号），最多 120s
  await page.locator('.conversation-finished-footer').last().waitFor({ timeout: 120000 }).catch(()=>{});
  await sleep(2000);
  // dump：回答/思考/工具/产物候选容器的 tag+class+文本样例
  const dump = await page.evaluate(() => {
    const probe = (sels) => sels.flatMap(s => Array.from(document.querySelectorAll(s)).slice(0,4).map(el => ({
      sel: s, tag: el.tagName, cls: (el.className||'').toString().slice(0,100), text: (el.innerText||'').trim().slice(0,80),
    })));
    return {
      answer: probe(['[class*="conversation"] [class*="markdown"]','[class*="message"]','[class*="answer"]','[class*="bubble"]','[class*="assistant"]']),
      thinking: probe(['[class*="think"]','[class*="reason"]','[class*="cot"]','[class*="chain"]']),
      tools: probe(['[class*="tool"]','[class*="mcp"]','[class*="action"]','[class*="step"]','[class*="plugin"]','[class*="search"]']),
      artifacts: probe(['[class*="artifact"]','[class*="file-card"]','[class*="attachment"]','a[download]','[class*="document"]']),
      footer: probe(['.conversation-finished-footer']),
    };
  });
  console.log(JSON.stringify(dump, null, 2));
  await browser.close(); process.exit(0);
})().catch(e => { console.error('[FAIL]', e.message); process.exit(1); });
```

- [ ] **Step 2: 跑前确认 prompt**

向用户确认要发的 prompt（`搜索…整理成 Markdown 文档`，触发联网工具+产物；消耗少量积分）。用户确认后再跑。

- [ ] **Step 3: 运行侦察，读 DOM dump**

Run: `node /tmp/wb-recon/recon.js`
Expected: 打印 answer/thinking/tools/artifacts/footer 各候选的 tag+class+文本样例。从中挑出**最稳定的业务类名**（非 CSS Module 哈希 `_xxx_hash_1`）作为选择器。

- [ ] **Step 4: 记录选择器清单**

把坐实的选择器整理成清单（键=Interfaces 里列的名字，值=选中的选择器 + 一句"为何选它"）。这份清单是 Task 4（config）和 Task 3（trace 抓取）的直接输入。若某块（如 thinking）该 prompt 仍未触发，记为"待补"并在 Task 3 用多候选+DOM dump 兜底策略覆盖。

- [ ] **Step 5: 清理探针**

```bash
pkill -9 -f "WorkBuddy.app" 2>/dev/null; rm -rf /tmp/wb-recon
```

---

## Task 2: WorkbuddyPool——CDP 连接骨架

**Files:**
- Create: `tools/qalab-runner/eval/src/workbuddy-pool.js`
- Test: `tools/qalab-runner/eval/scripts/verify-workbuddy-pool.js`（可 node 直接跑的真机验证）

**Interfaces:**
- Consumes: `config.workbuddyDesktop`（Task 4 定义:`{ executablePath, envPort, cdpHost, cdpPort, launchTimeout, readyTimeout, killExisting }`）。
- Produces:
  - `class WorkbuddyPool { constructor(desktopConfig, logger) }`
  - `async init()` → void（spawn+attach+取 page 就绪）
  - `getMainPage()` → Playwright Page
  - `getContext()` → Playwright BrowserContext
  - `async close({ keepClient = true } = {})` → void

- [ ] **Step 1: 写 WorkbuddyPool（连接骨架，独立新写，不改 desktop-pool.js）**

参照 `desktop-pool.js` 的 `_spawnClient`(107-118)/`_waitPort`(76-84)/`init` CDP 段(141-158) 的**模式**，但用 env 端口、单 page（WorkBuddy 单 page 单 frame，无 iframe/无 clawDeviceService）。

```js
// src/workbuddy-pool.js
// WorkBuddy Electron 客户端的 CDP 连接池（独立于 DesktopPool，零改动纳米链路）。
// WorkBuddy 单 page 单 frame（本地 asar React UI），无 work.n.cn iframe、无 clawDeviceService。
const { spawn, execFileSync } = require('child_process');
const http = require('http');
const { chromium } = require('playwright');

class WorkbuddyPool {
  constructor(desktopConfig = {}, logger = null) {
    const d = desktopConfig;
    this.executablePath = d.executablePath || '/Applications/WorkBuddy.app/Contents/MacOS/Electron';
    this.envPort = d.envPort || 'WORKBUDDY_REMOTE_DEBUGGING_PORT';  // WorkBuddy 用环境变量开调试端口
    this.cdpHost = d.cdpHost || '127.0.0.1';
    this.cdpPort = d.cdpPort || 9335;
    this.launchTimeout = d.launchTimeout || 75000;   // WorkBuddy 首屏偏慢，给足
    this.readyTimeout = d.readyTimeout || 30000;
    this.killExisting = d.killExisting !== false;
    this.logger = logger;
    this.browser = null; this.context = null; this.mainPage = null; this._launched = false;
  }
  get cdpUrl() { return `http://${this.cdpHost}:${this.cdpPort}`; }
  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }
  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  _fetchJson(url, timeout = 2500) {
    return new Promise((resolve, reject) => {
      const req = http.get(url, (res) => { let b = ''; res.on('data', d => b += d); res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } }); });
      req.on('error', reject); req.setTimeout(timeout, () => req.destroy(new Error('timeout')));
    });
  }
  async _portReady() { try { await this._fetchJson(`${this.cdpUrl}/json/version`); return true; } catch { return false; } }
  async _waitPort(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) { if (await this._portReady()) return true; await this._sleep(500); }
    throw new Error(`WorkBuddy 调试端口 ${this.cdpPort} 未就绪超时`);
  }
  _killExisting() {
    try { execFileSync('pkill', ['-9', '-f', this.executablePath], { stdio: 'ignore' }); } catch (_) {}
  }
  _spawnClient() {
    const child = spawn(this.executablePath, [], {
      detached: true, stdio: 'ignore',
      env: { ...process.env, [this.envPort]: String(this.cdpPort) },  // 关键：env 开调试端口
    });
    child.unref(); this._launched = true;
    this._log(`   已启动 WorkBuddy：${this.executablePath}（${this.envPort}=${this.cdpPort}）`);
  }
  async init() {
    // 1) 端口未就绪则（可选杀旧后）启动
    if (!(await this._portReady())) {
      if (this.killExisting) { this._killExisting(); await this._sleep(1000); }
      this._spawnClient();
      await this._waitPort(this.launchTimeout);
    } else {
      this._log('   WorkBuddy 调试端口已就绪，直接 attach');
    }
    // 2) CDP 连接
    this.browser = await chromium.connectOverCDP(this.cdpUrl);
    // 与纳米同款：纠正 collocated 标记，让 setInputFiles 走本地路径（附件上传绕 50MB）。私有字段，失败忽略。
    try {
      const impl = this.browser._connection && this.browser._connection.toImpl(this.browser);
      if (impl && impl._isBrowserCollocatedWithServer === false) impl._isBrowserCollocatedWithServer = true;
    } catch (_) {}
    const contexts = this.browser.contexts();
    if (!contexts.length) throw new Error('CDP 连接成功但无 context（WorkBuddy 窗口未创建）');
    this.context = contexts[0];
    await this.context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
    // 3) 取主 page（WorkBuddy 单 page）+ 等输入框就绪
    const page = this.context.pages()[0];
    if (!page) throw new Error('WorkBuddy context 下无 page');
    await page.locator('[contenteditable="true"][role="textbox"]').first().waitFor({ state: 'visible', timeout: this.readyTimeout }).catch(() => {});
    this.mainPage = page;
    this._log(`   已连接 WorkBuddy 主窗口：${page.url().slice(0, 60)}`);
  }
  getMainPage() { return this.mainPage; }
  getContext() { return this.context; }
  async close({ keepClient = true } = {}) {
    try { if (this.browser) await this.browser.close(); } catch (_) {}  // 只断 CDP 连接，不关客户端
    if (!keepClient && this._launched) { this._killExisting(); }
  }
}
module.exports = { WorkbuddyPool };
```

- [ ] **Step 2: 写真机验证脚本**

```js
// scripts/verify-workbuddy-pool.js —— 真机验证：能 attach + 拿到就绪 page（不发对话，零积分）
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const logger = { info: (m) => console.log(m), warn: (m) => console.warn(m), error: (m) => console.error(m) };
(async () => {
  const pool = new WorkbuddyPool({ cdpPort: 9335 }, logger);
  await pool.init();
  const page = pool.getMainPage();
  const hasInput = await page.locator('[contenteditable="true"][role="textbox"]').count();
  console.log(`[assert] mainPage url = ${page.url().slice(0,60)}`);
  console.log(`[assert] 输入框数量 = ${hasInput}（应 >= 1）`);
  if (!hasInput) { console.error('FAIL: 未找到输入框'); process.exit(1); }
  await pool.close();  // 只断连
  console.log('PASS: WorkbuddyPool attach + page 就绪');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
```

- [ ] **Step 3: 跑验证**

Run: `cd tools/qalab-runner/eval && node scripts/verify-workbuddy-pool.js`
Expected: 打印 `PASS`，输入框数量 ≥ 1。若客户端未登录会卡在输入框 waitFor（30s 后仍打印 url 但输入框 0）→ 需先手动登录 WorkBuddy。

- [ ] **Step 4: 收尾客户端**

```bash
pkill -9 -f "WorkBuddy.app" 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/eval/src/workbuddy-pool.js tools/qalab-runner/eval/scripts/verify-workbuddy-pool.js
git commit -m "feat(eval): WorkBuddy CDP 连接池(独立于纳米 DesktopPool)"
```

---

## Task 3: WorkbuddyDomTrace——DOM 抓取器（产出同结构 trace）

**Files:**
- Create: `tools/qalab-runner/eval/src/workbuddy-dom-trace.js`
- Test: `tools/qalab-runner/eval/scripts/verify-workbuddy-trace.js`（纯函数单测，node 直接跑，不需真机）

**Interfaces:**
- Consumes: Task 1 坐实的选择器（thinking/tool/artifact/answer/footer）；`ws-trace.js` 导出的 `_isMcp`/`_mcpServer`/`sanitizeDialogText`（复用 MCP 前缀判定与文本清洗，保持与纳米同语义）。
- Produces:
  - `class WorkbuddyDomTrace { constructor() }`
  - `async captureTurn(page, { baselineFooterCount = 0 } = {})` → void（抓当前轮 DOM 存内部）
  - `buildTrace(runId)` → `{ session_id, run_id, thinking, tool_calls, artifacts, answer, ws_captured:false, ws_connected:true, dom_captured:true, reported_duration, bean_cost, model }`
  - `reset()` → void
  - `parseFooter(text)` → `{ beanCost, model, reportedDuration }`（纯函数，导出供单测）

- [ ] **Step 1: 写 parseFooter 的失败测试（纯函数，可离线测）**

```js
// scripts/verify-workbuddy-trace.js
const assert = require('assert');
const { parseFooter } = require('../src/workbuddy-dom-trace');
// spike 实测 footer 文本形如："共消耗\n8.39\n均衡 (Deepseek-V4-Pro)\n14:11"
const r = parseFooter('共消耗\n8.39\n均衡 (Deepseek-V4-Pro)\n14:11');
assert.strictEqual(r.beanCost, '8.39', `beanCost 应 8.39，实得 ${r.beanCost}`);
assert.strictEqual(r.model, '均衡 (Deepseek-V4-Pro)', `model 应 均衡(...)，实得 ${r.model}`);
console.log('PASS: parseFooter');
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd tools/qalab-runner/eval && node scripts/verify-workbuddy-trace.js`
Expected: FAIL —— `Cannot find module` 或 `parseFooter is not a function`（文件还没建）。

- [ ] **Step 3: 写 workbuddy-dom-trace.js**

```js
// src/workbuddy-dom-trace.js
// WorkBuddy DOM 轨迹抓取器：WorkBuddy 对话走主进程 HTTP（渲染进程 CDP 截不到流），
// 故从 DOM 抓 thinking/tool_calls/artifacts/answer + footer 元信息，规整成与 ws-trace.js::buildTrace 同结构，
// 让后端判定层零改动复用。对外接口（buildTrace/reset）与 ws-trace 的 collector 对齐，供 reportRun 无缝调用。
const { _isMcp, _mcpServer, sanitizeDialogText } = require('./ws-trace');

// 解析 conversation-finished-footer 文本：「共消耗 / <bean> / <model> / <time>」
// 纯函数，导出供离线单测。footer 各行由 innerText 换行分隔。
function parseFooter(text) {
  const out = { beanCost: '', model: '', reportedDuration: '' };
  if (!text) return out;
  const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    if (/^[\d.]+$/.test(lines[i])) out.beanCost = lines[i];                 // 纯数字行 = 算力消耗
    else if (/[（(].*[)）]|Deepseek|GLM|Hy\d|均衡|快速|极致/i.test(lines[i])) out.model = lines[i]; // 含模型名/档位
  }
  return out;
}

// 选择器：Task 1 真机 dump 后填入（此处给出占位 + 多候选兜底策略；实现时以 Task 1 清单为准）
const SEL = {
  answerContainer: 'REPLACE_FROM_TASK1',   // 例如 '.conversation-message.assistant'
  thinking: 'REPLACE_FROM_TASK1',
  toolCard: 'REPLACE_FROM_TASK1',
  toolName: 'REPLACE_FROM_TASK1',
  toolResult: 'REPLACE_FROM_TASK1',
  artifactCard: 'REPLACE_FROM_TASK1',
  footer: '.conversation-finished-footer',  // 已坐实
};

class WorkbuddyDomTrace {
  constructor(sel = SEL) { this.sel = sel; this._data = this._empty(); }
  _empty() { return { session_id: null, thinking: '', tool_calls: [], artifacts: [], answer: '', beanCost: '', model: '', reportedDuration: '' }; }
  reset() { this._data = this._empty(); }

  // 抓「本轮」DOM：baselineFooterCount = 发送前已有的 footer 数，取其后的新增作为本轮。
  async captureTurn(page, { baselineFooterCount = 0 } = {}) {
    const sel = this.sel;
    const d = await page.evaluate((sel) => {
      const txt = (el) => el ? (el.innerText || '').trim() : '';
      const footers = Array.from(document.querySelectorAll(sel.footer));
      const footer = footers[footers.length - 1];               // 最后一个 footer = 本轮
      // 回答：本轮 footer 之前最近的一个回答容器（DOM 顺序）
      const answers = Array.from(document.querySelectorAll(sel.answerContainer));
      const answer = answers.length ? txt(answers[answers.length - 1]) : '';
      const thinking = txt(document.querySelector(sel.thinking));
      const tools = Array.from(document.querySelectorAll(sel.toolCard)).map(card => ({
        name: txt(card.querySelector(sel.toolName)) || '',
        result_text: txt(card.querySelector(sel.toolResult)) || '',
      }));
      const artifacts = Array.from(document.querySelectorAll(sel.artifactCard)).map(a => ({
        name: txt(a) || '', href: a.getAttribute && (a.getAttribute('href') || null),
      }));
      return { answer, thinking, tools, artifacts, footerText: txt(footer) };
    }, sel);

    const footer = parseFooter(d.footerText);
    this._data.answer = sanitizeDialogText(d.answer || '');
    this._data.thinking = sanitizeDialogText(d.thinking || '');
    // tool_calls 规整成与 ws-trace 同 8 字段结构（WorkBuddy DOM 无 toolCallId/originalToolName，name 兼任、逐一标 mcp）
    this._data.tool_calls = (d.tools || []).map((t, i) => {
      const original = t.name || '';
      return {
        tool_call_id: `_dom_${i}`, name: t.name || '', original_tool_name: original,
        is_mcp: _isMcp(original), mcp_server: _mcpServer(original),
        args: undefined, result_text: t.result_text || '', reached_result: !!t.result_text,
      };
    });
    this._data.artifacts = (d.artifacts || []).map(a => ({ name: a.name, kind: 'file', share_link: a.href || null }));
    this._data.beanCost = footer.beanCost;
    this._data.model = footer.model;
    this._data.reportedDuration = footer.reportedDuration;
  }

  buildTrace(runId) {
    return {
      session_id: this._data.session_id, run_id: runId || null,
      thinking: this._data.thinking, tool_calls: this._data.tool_calls,
      artifacts: this._data.artifacts, answer: this._data.answer,
      ws_captured: false, ws_connected: true, dom_captured: true,
      reported_duration: this._data.reportedDuration, bean_cost: this._data.beanCost, model: this._data.model,
    };
  }
}
module.exports = { WorkbuddyDomTrace, parseFooter };
```

> **实现注意**:`SEL` 里的 `REPLACE_FROM_TASK1` 必须用 Task 1 坐实的真实选择器替换。若某块 Task 1 仍未触发到（如无思考），保留一个"多候选 querySelector 兜底 + 抓不到留空"的实现,并在该块加 `console.warn` 暴露"未抓到"（对齐纳米附件抓取的既有教训:抓不到要暴露而非静默）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd tools/qalab-runner/eval && node scripts/verify-workbuddy-trace.js`
Expected: PASS —— parseFooter 正确拆出 beanCost=8.39、model=均衡(Deepseek-V4-Pro)。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/eval/src/workbuddy-dom-trace.js tools/qalab-runner/eval/scripts/verify-workbuddy-trace.js
git commit -m "feat(eval): WorkBuddy DOM trace 抓取器(规整成同结构 trace)"
```

---

## Task 4: config.workbuddy 选择器段 + workbuddyDesktop 连接段

**Files:**
- Modify: `tools/qalab-runner/eval/config/default.config.js`（仅新增两段，不改现有 platform/desktop）

**Interfaces:**
- Consumes: Task 1 坐实的选择器清单。
- Produces: `config.workbuddy`（选择器段，供 WorkbuddyRunner/WorkbuddyDomTrace 读）、`config.workbuddyDesktop`（连接段，供 WorkbuddyPool 读）。

- [ ] **Step 1: 新增 config.workbuddyDesktop（连接段）**

在 `config/default.config.js` 的 `desktop` 段之后，新增（值用已坐实的 + env 可覆盖）:

```js
  // WorkBuddy 桌面客户端 CDP 连接（独立于纳米 desktop 段）。env 覆盖便于多机/多端口。
  workbuddyDesktop: {
    executablePath: process.env.WORKBUDDY_EXE || '/Applications/WorkBuddy.app/Contents/MacOS/Electron',
    envPort: 'WORKBUDDY_REMOTE_DEBUGGING_PORT',
    cdpHost: '127.0.0.1',
    cdpPort: parseInt(process.env.WORKBUDDY_CDP_PORT, 10) || 9335,  // 与纳米 9222 错开，避免同机冲突
    launchTimeout: 75000,
    readyTimeout: 30000,
    killExisting: true,
  },
```

- [ ] **Step 2: 新增 config.workbuddy（选择器段）**

用 Task 1 坐实的值替换 `<TASK1>` 占位（已坐实的直接填）:

```js
  // WorkBuddy 页面选择器段（仿 config.platform；WorkBuddy 单 page 无 iframe）。
  workbuddy: {
    expectedAgentName: 'WorkBuddy',
    newTaskSelector: 'text=新建任务',                              // 开新对话（tab/按钮，spike 见）
    inputSelector: '[contenteditable="true"][role="textbox"]',    // 已坐实
    sendBtnSelector: 'button.cr-send-button',                     // 已坐实
    // 模型下拉（已坐实）：触发 + 选项 + 当前选中
    modelTriggerSelector: 'button.cr-model-selector__trigger',
    modelOptionSelector: '.cr-model-selector__item',
    modelOptionNameSelector: '.cr-model-selector__item-info',
    modelSelectedHint: 'cr-model-selector__item--selected',
    // 回答/思考/工具/产物（<TASK1> 坐实后填）
    answerContainerSelector: '<TASK1>',
    thinkingSelector: '<TASK1>',
    toolCardSelector: '<TASK1>',
    toolNameSelector: '<TASK1>',
    toolResultSelector: '<TASK1>',
    artifactCardSelector: '<TASK1>',
    // 完成/元信息（footer 已坐实）
    footerSelector: '.conversation-finished-footer',
  },
```

- [ ] **Step 3: 校验 config 可加载**

Run: `cd tools/qalab-runner/eval && node -e "const c=require('./config/default.config.js'); console.log('workbuddy:', !!c.workbuddy, 'wbDesktop:', !!c.workbuddyDesktop, 'input:', c.workbuddy.inputSelector); if(!c.workbuddy||!c.workbuddyDesktop) process.exit(1)"`
Expected: 打印 `workbuddy: true wbDesktop: true input: [contenteditable="true"][role="textbox"]`，退出码 0。

- [ ] **Step 4: 确认未破坏纳米配置**

Run: `cd tools/qalab-runner/eval && node -e "const c=require('./config/default.config.js'); console.log('nami chatUrl:', c.platform.chatUrl, 'nami exe:', c.desktop.executablePath.slice(0,20)); if(c.platform.chatUrl!=='https://work.n.cn/launcher') process.exit(1)"`
Expected: 纳米 chatUrl 仍为 `https://work.n.cn/launcher`，未被改动。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/eval/config/default.config.js
git commit -m "feat(eval): 新增 config.workbuddy 选择器段 + workbuddyDesktop 连接段"
```

---

## Task 5: WorkbuddyRunner——对话驱动（runOne / runConversationTurns）

**Files:**
- Create: `tools/qalab-runner/eval/src/workbuddy-runner.js`
- Test: `tools/qalab-runner/eval/scripts/verify-workbuddy-runone.js`（真机，发一条对话）

**Interfaces:**
- Consumes: `config.workbuddy`（选择器）、`config.execution`（超时/dialogOptions，与纳米共用）、`WorkbuddyDomTrace`、Task 2 的 `page`（由 pool.getMainPage() 传入）。
- Produces:
  - `class WorkbuddyRunner { constructor(page, workbuddyConfig, executionConfig, logger) }`
  - `async runOne(testCase)` → result（`_buildResult` 同形状）
  - `async runConversationTurns(turns, onTurnDone)` → result[]
  - `getDomTrace()` → WorkbuddyDomTrace 实例（供 reportRun 取 trace）
  - testCase 结构与纳米一致:`{ caseId, run_id, question, attachments, attachmentPaths, conversationId, turnIndex, account }`

- [ ] **Step 1: 写 WorkbuddyRunner（对话驱动 + 结果组装）**

对齐 `desktop-runner.js` 的 `runOne`/`runConversationTurns`/`_buildResult` 签名与形状，但驱动逻辑用 WorkBuddy 选择器。

```js
// src/workbuddy-runner.js
// WorkBuddy 对话驱动器：CDP 已连的 page 上开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+trace。
// 对外接口对齐 DesktopRunner（runOne/runConversationTurns/result 形状），使 bin/ai-eval.js 的 reportRun 无缝复用。
const { WorkbuddyDomTrace } = require('./workbuddy-dom-trace');

function looksIncomplete(answer) {
  const t = (answer || '').trim();
  if (!t) return true;
  return /思考中|生成中|Thinking/i.test(t.slice(-20));
}

class WorkbuddyRunner {
  constructor(page, workbuddyConfig = {}, executionConfig = {}, logger = null) {
    this.page = page; this.wb = workbuddyConfig; this.execution = executionConfig; this.logger = logger;
    this.trace = new WorkbuddyDomTrace(this._traceSel());
  }
  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }
  _traceSel() {
    const w = this.wb;
    return { answerContainer: w.answerContainerSelector, thinking: w.thinkingSelector,
      toolCard: w.toolCardSelector, toolName: w.toolNameSelector, toolResult: w.toolResultSelector,
      artifactCard: w.artifactCardSelector, footer: w.footerSelector };
  }
  getDomTrace() { return this.trace; }

  async _openCleanConversation() {
    const nt = this.page.locator(this.wb.newTaskSelector).first();
    if (await nt.count()) { await nt.click().catch(() => {}); await this.page.waitForTimeout(1200); }
    // 确认输入框可见
    await this.page.locator(this.wb.inputSelector).first().waitFor({ state: 'visible', timeout: 15000 });
    return true;
  }

  // 选模型档：点开 cr-model-selector__trigger，选名字匹配 dialogOptions.model 的 item。未指定则不动。
  async _applyDialogOptions() {
    const model = (this.execution.dialogOptions || {}).model;
    if (!model) return;
    try {
      await this.page.locator(this.wb.modelTriggerSelector).first().click();
      await this.page.waitForTimeout(600);
      const opt = this.page.locator(this.wb.modelOptionSelector, { hasText: model }).first();
      if (await opt.count()) { await opt.click(); await this.page.waitForTimeout(400); }
      else { this._warn(`   模型档「${model}」未在下拉中找到，用当前默认`); await this.page.keyboard.press('Escape').catch(() => {}); }
    } catch (e) { this._warn(`   选模型档失败(用默认): ${(e.message||'').split('\n')[0]}`); }
  }

  async _sendOne(testCase, baselineFooterCount) {
    await this._applyDialogOptions();
    const input = this.page.locator(this.wb.inputSelector).first();
    await input.click();
    await input.type(testCase.question, { delay: 12 });
    await this.page.waitForTimeout(300);
    // 附件（若有）：setInputFiles 到隐藏 file input（WorkBuddy 若无附件能力则跳过；Task 系列后续覆盖）
    await input.press('Enter');
  }

  // 等本轮完成：footer 数量 > baseline 即本轮 footer 已出现（对齐 spike 观察：footer=本轮收口信号）
  async _waitComplete(baselineFooterCount) {
    const timeout = this.execution.responseTimeout || 180000;
    try {
      await this.page.locator(this.wb.footerSelector).nth(baselineFooterCount).waitFor({ timeout });
      await this.page.waitForTimeout(1500); // 让答案/元信息渲染稳定
      return { completed: true, reason: 'footer' };
    } catch (e) { return { completed: false, reason: 'timeout' }; }
  }

  async _footerCount() { return await this.page.locator(this.wb.footerSelector).count(); }

  _buildResult(testCase, trace, meta) {
    const answerText = trace.answer || '';
    const completed = !!meta.completed;
    const incomplete = !meta.errorMsg && (!completed || looksIncomplete(answerText));
    const success = !meta.errorMsg && !incomplete && answerText.trim().length > 0;
    return {
      caseId: testCase.caseId, row: testCase.row, account: testCase.account || 'workbuddy',
      conversationId: testCase.conversationId, turnIndex: testCase.turnIndex, question: testCase.question,
      answer: meta.errorMsg ? `[执行失败] ${meta.errorMsg}` : success ? answerText : `[未完成:${meta.completeReason}]`,
      shareLink: null, artifactShareLink: (trace.artifacts[0] && trace.artifacts[0].share_link) || null,
      hasArtifact: trace.artifacts.length > 0,
      reportedDuration: trace.reported_duration || null, reportedDurationRaw: null,
      beanCost: trace.bean_cost || null, cost: null, costRaw: null,
      durationMs: (meta.endTime || Date.now()) - (meta.startTime || Date.now()),
      startTime: meta.startTime, endTime: meta.endTime,
      success, incomplete, completeReason: meta.completeReason || 'unknown',
      missingFields: [], reloadRecoveredFields: [],
    };
  }

  async runOne(testCase) {
    const startTime = Date.now();
    this.trace.reset();
    try {
      await this._openCleanConversation();
      const baseline = await this._footerCount();
      await this._sendOne(testCase, baseline);
      const done = await this._waitComplete(baseline);
      await this.trace.captureTurn(this.page, { baselineFooterCount: baseline });
      const trace = this.trace.buildTrace(testCase.run_id);
      return this._buildResult(testCase, trace, { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now() });
    } catch (e) {
      const msg = (e.message || '').split('\n')[0];
      return this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() });
    }
  }

  async runConversationTurns(turns, onTurnDone) {
    const sorted = turns.slice().sort((a, b) => (a.turnIndex || 0) - (b.turnIndex || 0));
    const results = [];
    let aborted = false, abortMsg = '';
    for (let i = 0; i < sorted.length; i++) {
      const testCase = sorted[i]; const startTime = Date.now();
      if (aborted) {
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'skipped', errorMsg: abortMsg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {}); continue;
      }
      try {
        this.trace.reset();
        if (i === 0) { await this._openCleanConversation(); }
        const baseline = await this._footerCount();
        await this._sendOne(testCase, baseline);        // 后续轮不新建对话，在同一对话追加
        const done = await this._waitComplete(baseline);
        await this.trace.captureTurn(this.page, { baselineFooterCount: baseline });
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
      } catch (e) {
        const msg = (e.message || '').split('\n')[0];
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
        if (i === 0) { aborted = true; abortMsg = '首轮失败,多轮上下文未建立,后续轮跳过'; }
      }
    }
    return results;
  }
}
module.exports = { WorkbuddyRunner };
```

- [ ] **Step 2: 写真机 runOne 验证脚本**

```js
// scripts/verify-workbuddy-runone.js —— 真机：连客户端 → runOne 发一条 → 打印 result + trace
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const config = require('../config/default.config.js');
const logger = { info: console.log, warn: console.warn, error: console.error };
(async () => {
  const pool = new WorkbuddyPool(config.workbuddyDesktop, logger);
  await pool.init();
  const runner = new WorkbuddyRunner(pool.getMainPage(), config.workbuddy, config.execution || {}, logger);
  const result = await runner.runOne({ caseId: 'VERIFY-1', run_id: 0, question: '你好，请用一句话介绍你自己', conversationId: '__v1', turnIndex: 0, account: 'workbuddy' });
  const trace = runner.getDomTrace().buildTrace(0);
  console.log('=== result ===', JSON.stringify({ success: result.success, answer: result.answer.slice(0,60), beanCost: result.beanCost, durationMs: result.durationMs }, null, 2));
  console.log('=== trace ===', JSON.stringify({ answer: (trace.answer||'').slice(0,60), thinking: (trace.thinking||'').slice(0,40), tool_calls: trace.tool_calls.length, artifacts: trace.artifacts.length, model: trace.model, bean_cost: trace.bean_cost }, null, 2));
  if (!result.success) { console.error('FAIL: runOne 未成功'); process.exit(1); }
  await pool.close();
  console.log('PASS: WorkbuddyRunner.runOne');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
```

- [ ] **Step 3: 跑前确认 prompt，运行真机验证**

向用户确认发 `你好，请用一句话介绍你自己`（已在 spike 用过，最省积分）。确认后:
Run: `cd tools/qalab-runner/eval && node scripts/verify-workbuddy-runone.js`
Expected: PASS —— result.success=true，answer 有正文，beanCost 有值，trace.model 显示档位。

- [ ] **Step 4: 收尾**

```bash
pkill -9 -f "WorkBuddy.app" 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/eval/src/workbuddy-runner.js tools/qalab-runner/eval/scripts/verify-workbuddy-runone.js
git commit -m "feat(eval): WorkBuddy 对话驱动器(runOne/多轮/选模型档,对齐 DesktopRunner 接口)"
```

---

## Task 6: bin/ai-eval.js 分流——按 target_engine 拆 pending

**Files:**
- Modify: `tools/qalab-runner/eval/bin/ai-eval.js`（`runOnce` 内，仅新增分支，不改纳米路径）

**Interfaces:**
- Consumes: `fetchPending` 返回的 run item（含 `target_engine`）、`WorkbuddyPool`、`WorkbuddyRunner`、既有 `reportRun`/`failWholeGroup`/`groupIntoConversations`。
- Produces: 无对外新接口；行为——workbuddy 的 run 走 WorkbuddyPool+WorkbuddyRunner，其余走原纳米路径。

- [ ] **Step 1: 顶部 require 新模块**

在 `bin/ai-eval.js` 引入区加:

```js
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
```

- [ ] **Step 2: runOnce 内按 engine 拆分 pending**

在 `bin/ai-eval.js:782`（`const pending = await client.fetchPending(...)`）之后、`:786`（`new DesktopPool`）之前，插入拆分:

```js
      // 按被测引擎拆分：workbuddy 走独立执行器，其余(namiwork/空)走原纳米路径。
      // 同一 conversation_group 必然同引擎（后端整组同 target_engine），故按 run 顶层字段直接分。
      const wbPending = pending.filter(it => (it.target_engine || '').toLowerCase() === 'workbuddy');
      const namiPending = pending.filter(it => (it.target_engine || '').toLowerCase() !== 'workbuddy');
      // 先处理 workbuddy 批（若有）：独立 pool/runner，复用 reportRun/failWholeGroup/groupIntoConversations。
      if (wbPending.length) {
        await runWorkbuddyBatch(wbPending);   // 见 Step 3
      }
      // 无纳米任务则直接返回，避免白建纳米 DesktopPool。
      if (!namiPending.length) { logger.info('本轮仅 workbuddy 任务，已处理'); return 0; }
      // 下方原逻辑改用 namiPending（把原来对 pending 的引用替换为 namiPending）
```

> **实现注意**:原 `:820` 的 `groupIntoConversations(pending)` 改为 `groupIntoConversations(namiPending)`；`:783` 的空判断已在拆分前，保留。

- [ ] **Step 3: 实现 runWorkbuddyBatch（复用 reportRun/failWholeGroup）**

在 `reportRun`/`failWholeGroup` 定义之后（它们在 `runOnce` 闭包内，:826/:855），新增:

```js
      // WorkBuddy 批处理：独立 pool/runner，复用 reportRun（传 runner 的 DomTrace 作 ws-like）+ failWholeGroup。
      const runWorkbuddyBatch = async (items) => {
        logger.info(`[workbuddy] 处理 ${items.length} 条`);
        const wbPool = new WorkbuddyPool(config.workbuddyDesktop, logger);
        try { await wbPool.init(); }
        catch (e) { logger.error(`[workbuddy] 客户端连接失败，整批 failed: ${e.message}`); for (const it of items) { try { await client.claim(it.run_id); await client.report(it.run_id, { status: 'failed', reason: `WorkBuddy 连接失败: ${e.message}` }); } catch (_) {} } return; }
        try {
          const runner = new WorkbuddyRunner(wbPool.getMainPage(), config.workbuddy, config.execution || {}, logger);
          const convs = groupIntoConversations(items);
          for (const conv of convs) {
            const head = conv[0]; const headP = head.payload || {};
            // dialogOptions 就地改（与纳米同纪律）
            config.execution = config.execution || {};
            config.execution.dialogOptions = (headP.dialog_options && typeof headP.dialog_options === 'object') ? headP.dialog_options : config.execution.dialogOptions;
            try { await client.claim(head.run_id); }
            catch (e) { logger.warn(`[workbuddy] claim 首轮 ${head.run_id} 失败，整组跳过: ${e.message}`); continue; }
            for (const it of conv.slice(1)) { try { await client.claim(it.run_id); } catch (_) {} }
            const testCases = conv.map(it => { const p = it.payload || {}; return { caseId: `RUN-${it.run_id}`, run_id: it.run_id, row: it.run_id, question: p.prompt || '', attachments: p.attachments || [], attachmentPaths: [], conversationId: p.conversation_group || `__run_${it.run_id}`, turnIndex: p.turn_index || 0, account: 'workbuddy' }; });
            // ws-like：把 runner 的 DomTrace 包成 reportRun 认的 { buildTrace, reset }
            const wsLike = { buildTrace: (rid) => runner.getDomTrace().buildTrace(rid), reset: () => runner.getDomTrace().reset() };
            const onTurnDone = async (result, testCase) => { await reportRun(testCase.run_id, result, wsLike); };
            if (testCases.length === 1) { const r = await runner.runOne(testCases[0]); await onTurnDone(r, testCases[0]); }
            else { await runner.runConversationTurns(testCases, onTurnDone); }
          }
        } finally { await wbPool.close(); }
      };
```

> **实现注意**:`runWorkbuddyBatch` 必须定义在 `reportRun`（:826）与 `failWholeGroup`（:855）**之后**、Step 2 的调用点会在它们**之前**执行——故用函数声明 `const runWorkbuddyBatch = async ...` 会有 TDZ 问题。解决:把 Step 2 的 `if (wbPending.length) await runWorkbuddyBatch(...)` 调用移到 `reportRun`/`failWholeGroup`/`runWorkbuddyBatch` 都定义之后（即 :862 `for (const conv of conversations)` 之前），或改用 `function runWorkbuddyBatch(items){...}` 函数声明（提升）。**采用后者**（函数声明提升，最简）。

- [ ] **Step 4: 真机端到端验证（模拟一条 workbuddy pending）**

因需真实平台 run，用一次性脚本模拟 fetchPending 返回一条 workbuddy run，验证分流 + 执行 + reportRun 组装（用 mock client 拦截回写，不真写平台）:

```js
// scripts/verify-workbuddy-dispatch.js —— 真机：模拟一条 workbuddy run 走完整分流+执行+回写(mock)
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { groupIntoConversations } = require('../src/conversation-group');
const config = require('../config/default.config.js');
const logger = { info: console.log, warn: console.warn, error: console.error };
const reported = [];
const client = { claim: async () => {}, report: async (id, b) => reported.push({ id, b }), uploadTrace: async () => {} };
(async () => {
  const items = [{ run_id: 9001, target_engine: 'workbuddy', payload: { prompt: '你好，请用一句话介绍你自己', turn_index: 0 } }];
  const pool = new WorkbuddyPool(config.workbuddyDesktop, logger); await pool.init();
  const runner = new WorkbuddyRunner(pool.getMainPage(), config.workbuddy, config.execution || {}, logger);
  for (const conv of groupIntoConversations(items)) {
    const it = conv[0]; const p = it.payload;
    const tc = { caseId: `RUN-${it.run_id}`, run_id: it.run_id, question: p.prompt, conversationId: `__run_${it.run_id}`, turnIndex: 0, account: 'workbuddy' };
    const r = await runner.runOne(tc);
    const trace = runner.getDomTrace().buildTrace(it.run_id);
    await client.report(it.run_id, { status: r.success ? 'done' : 'failed', answer: r.answer, bean_cost: r.beanCost, session_id: trace.session_id });
  }
  await pool.close();
  console.log('=== reported ===', JSON.stringify(reported, null, 2));
  if (!reported.length || reported[0].b.status !== 'done') { console.error('FAIL'); process.exit(1); }
  console.log('PASS: 分流+执行+回写组装');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
```

Run（确认 prompt 后）: `cd tools/qalab-runner/eval && node scripts/verify-workbuddy-dispatch.js`
Expected: PASS —— reported[0].b.status='done'，answer 有正文。

- [ ] **Step 5: 收尾 + Commit**

```bash
pkill -9 -f "WorkBuddy.app" 2>/dev/null || true
git add tools/qalab-runner/eval/bin/ai-eval.js tools/qalab-runner/eval/scripts/verify-workbuddy-dispatch.js
git commit -m "feat(eval): ai-eval 按 target_engine 分流,workbuddy 走独立执行器"
```

---

## 验证总览（本仓库无测试框架，全部为可运行脚本）

| 层 | 验证脚本 | 真机? | 判据 |
|---|---|---|---|
| 选择器坐实 | Task1 `/tmp/wb-recon/recon.js` | 是（发1条触发型） | dump 出可用选择器 |
| 连接池 | `verify-workbuddy-pool.js` | 是（不发对话） | attach + 输入框就绪 |
| trace 纯函数 | `verify-workbuddy-trace.js` | 否 | parseFooter 正确 |
| 配置加载 | `node -e` 内联 | 否 | 两段存在 + 纳米未破坏 |
| 对话驱动 | `verify-workbuddy-runone.js` | 是（发1条） | result.success + trace 四块 |
| 分流回写 | `verify-workbuddy-dispatch.js` | 是（发1条） | reported done + answer |

**端到端最终验证（Plan 1 完成标志）**:WorkBuddy 客户端能被 CDP 驱动发一条对话、抓出含 answer+model+beanCost 的 result，经分流走完 reportRun 组装出 `status=done` 的回写体（mock）。真实平台联调（下发真 run → 回写真库）留待 Plan 2（后端派单）完成后合并验证。

---

## Self-Review 记录

- **Spec 覆盖**:本 plan 对应 spec §5（执行层）全部——§5.1 复用/新增划分（Task 2/3/5，采独立新写而非抽基类，理由:零风险隔离生产纳米链路，spec §12 已说第三产品再抽公共层）、§5.2 分流（Task 6）、§5.3 DOM trace 四块+耗时（Task 1/3）、§5.4 回写复用（Task 6 复用 reportRun）。spec §4/§6/§7/§8（数据模型/派单挑机/判定/对比视图）属 Plan 2、3，不在本 plan。
- **占位符扫描**:`config.workbuddy` 的回答/思考/工具/产物选择器标记为 `<TASK1>`/`REPLACE_FROM_TASK1`——这是**有意的显式依赖**（Task 1 真机 dump 后填），非遗漏；Task 1 是其前置任务，产出即为这些值。其余均为坐实值或真实代码。
- **类型一致**:`runOne(testCase)`/`runConversationTurns(turns, onTurnDone)` 签名、result 字段（success/answer/beanCost/reportedDuration/shareLink/artifactShareLink/durationMs/completeReason）、trace 的 `buildTrace(runId)`/`reset()` 跨 Task 2/3/5/6 一致，且与 `desktop-runner.js`/`reportRun` 实读字段对齐。
