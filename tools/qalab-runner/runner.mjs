#!/usr/bin/env node
import { randomUUID, createHash } from "node:crypto";
import { createRecordingPump } from "./recording-pump.mjs";
import { loadRegistry, registrySnapshot } from "./selector-registry.mjs";
// qalab 本地执行 runner —— 轮询平台待执行队列,调用 Claude Code(headless)执行,回写 pass/fail。
// 纯 Node(v18+ 内置 fetch),无外部依赖。本机 python 在 git-bash 下无法 fork,故 runner 用 node。
//
// 用法:
//   BASE_URL=https://qalab.claw.qihoo.net RUNNER_TOKEN=xxx RUNNER_ID=win-01 node runner.mjs
//   加 --dry 只跑握手(拉取 + 回写假结果),不真正调 Claude,用于先验证与平台连通。

import { spawn, execFile } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createGuiCore } from "./gui-mcp/gui-core.mjs";
import { runScript } from "./step-executor.mjs";
import { run as apiRun } from "./api-executor.mjs";
import { pollPerfOnce, uploadLocalSessions } from "./perf-collect.mjs";
import { existsSync } from "node:fs";
import { resetOrBlock } from "./reset-home.mjs";
import { summarizeBatch } from "./runner-summary.mjs";
import { rawEventToStep, dedupeSteps } from "./record-capture.mjs";
import { NAV_SYSTEM_PROMPT, parseNavOk } from "./precond-nav.mjs";
import { selfUpdate } from "./self-update.mjs";
import { runWithTrace } from "./exec-trace.mjs";

// 极简 .env 加载器(零依赖):把同目录 .env 的键值填入 process.env(不覆盖已有环境变量)。
(function loadDotenv() {
  try {
    const envPath = join(dirname(fileURLToPath(import.meta.url)), ".env");
    let raw = readFileSync(envPath, "utf-8");
    if (raw.charCodeAt(0) === 0xFEFF) raw = raw.slice(1);   // 去 UTF-8 BOM(Windows 编辑器常见)
    for (const line of raw.split(/\r?\n/)) {
      if (!line.trim() || line.trim().startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq < 0) continue;
      const key = line.slice(0, eq).trim();
      // trim 去首尾空白与残留的 \r;再剥一层引号。防 Windows CRLF/复制粘贴带隐藏字符
      // 导致 token 末尾混入 \r → "Bearer xxx\r" → 平台 401(实测踩过)。
      let val = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
      if (key && process.env[key] === undefined) process.env[key] = val;
    }
  } catch { /* 没有 .env 就用真实环境变量 / 默认值 */ }
})();

// 是否为 Windows —— 决定 GUI 用例的客户端冷启动方式(PowerShell vs 直接 spawn)。
const IS_WIN = process.platform === "win32";

const BASE_URL     = (process.env.BASE_URL     || "https://qalab.claw.qihoo.net").replace(/\/$/, "");
const RUNNER_TOKEN = process.env.RUNNER_TOKEN  || "";
const RUNNER_ID    = process.env.RUNNER_ID     || "win-01";
const POLL_MS      = Number(process.env.POLL_MS || 5000);
const CLAUDE_BIN   = process.env.CLAUDE_BIN    || "claude";
// NAMICLAW_EXE 不设默认值:空=这台机器没有被测客户端,不该跑 gui 用例(而非兜底成某个
// 平台的固定路径,否则在没有该客户端的机器上会去 spawn 不存在的路径而崩溃)。
const NAMICLAW_EXE = process.env.NAMICLAW_EXE  || "";
const CDP_PORT     = Number(process.env.CDP_PORT || 9222);
// claude 单次执行硬超时:卡在被测页/工具时杀掉并回写 fail,避免 run 永久卡 running(无人值守)。
const CLAUDE_TIMEOUT_MS = Number(process.env.CLAUDE_TIMEOUT_MS || 240000);
// 只加载本目录 .mcp.json 的 gui server,屏蔽执行机上用户全局 MCP(context7/figma/playwright…)。
// 绝对路径(相对 runner.mjs),不依赖启动 cwd。
const MCP_CONFIG   = join(dirname(fileURLToPath(import.meta.url)), ".mcp.json");
// 空 MCP 配置(judge 纯判别用):配合 --strict-mcp-config 隔离执行机全局 MCP/插件,不启动任何 server。
const EMPTY_MCP_CONFIG = join(dirname(fileURLToPath(import.meta.url)), "gui-mcp", "empty-mcp.json");
const DRY          = process.argv.includes("--dry");
const RESET_BETWEEN_CASES = (process.env.RESET_BETWEEN_CASES ?? "1") !== "0";  // 用例间 reload 复位(默认开)

// perf 采集引擎目录:分发包内 nami-perfdog 与 runner 同目录 → 优先自身;开发环境回落源码路径。
const __rdir = dirname(fileURLToPath(import.meta.url));
const PERFDOG_DIR  = process.env.PERFDOG_DIR || (existsSync(join(__rdir, "nami-perfdog.mjs")) ? __rdir : "D:/git/test/nami-perfdog");
const SESSIONS_DIR = join(PERFDOG_DIR, "sessions");
const REPORT_SET_ID = process.env.REPORT_SET_ID ? Number(process.env.REPORT_SET_ID) : null;

const H = { "Content-Type": "application/json", "Authorization": `Bearer ${RUNNER_TOKEN}` };
const log = (...a) => console.log(new Date().toISOString(), ...a);

// gui-core 单例:StepExecutor 用它确定性执行 gui/e2e 步骤(与 gui-mcp server 同一套定位引擎)。
const guiCore = createGuiCore({ cdpUrl: `http://127.0.0.1:${CDP_PORT}` });

// ---- 平台 API(契约见 app/routers/exec_queue.py:{code,msg,data} 信封)----
async function api(method, path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method, headers: H, body: body ? JSON.stringify(body) : undefined,
  });
  const env = await res.json().catch(() => ({}));
  if (!res.ok || (env.code !== 0 && env.code !== undefined && ![200, 201].includes(env.code))) {
    throw new Error(`${method} ${path} -> HTTP ${res.status} code=${env.code} msg=${env.msg}`);
  }
  return env.data;
}

const fetchPending = () => api("GET", `/api/exec-queue?runner=${encodeURIComponent(RUNNER_ID)}&limit=5`);
const claim        = (id) => api("POST", `/api/exec-queue/${id}/claim?runner=${encodeURIComponent(RUNNER_ID)}`);
const report       = (id, r) => api("PATCH", `/api/exec-queue/${id}?runner=${encodeURIComponent(RUNNER_ID)}`, r);

// ---- 设备探测队列(与 exec 队列并列,同一 runner token/契约)----
// GET /api/probe/pending?runner= 拉本机 pending 探测(平台侧拉取即置 running);
// PATCH /api/probe/{id}?runner= 回写 {result} 或 {error}。镜像上面的 exec 封装。
const fetchProbes  = () => api("GET", `/api/probe/pending?runner=${encodeURIComponent(RUNNER_ID)}`);
const reportProbe  = (id, r) => api("PATCH", `/api/probe/${id}?runner=${encodeURIComponent(RUNNER_ID)}`, r);
// 录制会话(与 exec/probe 队列并列):拉本机待录/录制中会话,增量上报捕获步骤。
const RECORD_CONSUMER = randomUUID();
const fetchRecords = () => api("GET", `/api/record/pending?runner=${encodeURIComponent(RUNNER_ID)}&consumer_id=${RECORD_CONSUMER}`);
const reportRecordEvents = (id, body) => api("POST", `/api/record/${id}/events?runner=${encodeURIComponent(RUNNER_ID)}`, body);
// 上传探测整页截图(PNG 二进制)到独立端点:multipart/form-data(不用 api() 封装——那是 JSON)。
// 只带 Authorization,不设 Content-Type——让 fetch 按 FormData 自动补 multipart boundary。
// Node 18+ 内置 FormData/Blob/fetch。截图不塞 result TEXT(MySQL 5.6 TEXT 64KB 会截断 base64)。
async function uploadProbeShot(id, buffer) {
  const fd = new FormData();
  fd.append("file", new Blob([buffer], { type: "image/png" }), `probe-${id}.png`);
  const res = await fetch(`${BASE_URL}/api/probe/${id}/screenshot?runner=${encodeURIComponent(RUNNER_ID)}`, {
    method: "POST", headers: { Authorization: `Bearer ${RUNNER_TOKEN}` }, body: fd,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json().catch(() => ({}));
}

// 上传某执行步的截图(PNG Buffer)到 exec 独立端点,返回可访问 URL(失败抛错,调用方吞掉不阻断回写)。
async function uploadExecShot(runId, idx, buffer) {
  const fd = new FormData();
  fd.append("file", new Blob([buffer], { type: "image/png" }), `exec-${runId}-${idx}.png`);
  const res = await fetch(`${BASE_URL}/api/exec-queue/${runId}/screenshot?idx=${idx}&runner=${encodeURIComponent(RUNNER_ID)}`, {
    method: "POST", headers: { Authorization: `Bearer ${RUNNER_TOKEN}` }, body: fd,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const j = await res.json().catch(() => ({}));
  return j?.data?.screenshot_url || j?.screenshot_url || null;   // api() 未用,这里手解包
}

async function uploadExecTrace(runId, path) {
  const fd = new FormData();
  fd.append("file", new Blob([readFileSync(path)], { type: "application/zip" }), `exec-${runId}-trace.zip`);
  const response = await fetch(`${BASE_URL}/api/exec-queue/${runId}/trace?runner=${encodeURIComponent(RUNNER_ID)}`, {
    method: "POST", headers: { Authorization: `Bearer ${RUNNER_TOKEN}` }, body: fd,
  });
  if (!response.ok) throw new Error(`Trace 上传 HTTP ${response.status}`);
  const body = await response.json();
  const url = body.data?.trace_url || body.trace_url;
  if (!url) throw new Error("Trace 上传未返回下载地址");
  await unlink(path).catch(() => {});
  return url;
}

// 把 result.report 里各步的 shotBuf 逐张上传换成 shot(URL),返回可回写的纯 JSON report(无 Buffer)。
// 上传失败的步只是没有截图 URL,不影响其余步与回写。无 report/无截图 → 返回 report 原样(去掉 Buffer)。
async function uploadReportShots(runId, report) {
  if (!Array.isArray(report)) return null;
  const out = [];
  for (const step of report) {
    const { shotBuf, ...rest } = step || {};
    if (shotBuf && shotBuf.length) {
      try {
        const url = await uploadExecShot(runId, rest.no || out.length + 1, shotBuf);
        if (url) rest.shot = url;
      } catch (e) { log(`  截图上传失败 run=${runId} step=${rest.no}: ${e.message}`); }
    }
    out.push(rest);
  }
  return out;
}

const fetchRegistry = (projectId, sub = "") => loadRegistry(api, projectId, sub);

// ---- 确保 namiclaw 带 CDP 调试端口在跑(GUI 用例前置)----
// namiclaw 有单实例锁:必须先杀光旧实例,再带 --remote-debugging-port 冷启动,否则端口不开。
// Windows 用 PowerShell Start-Process(脱离 git-bash fork 问题);Mac/Linux 用 spawn detached。
function cdpAlive() {
  return fetch(`http://127.0.0.1:${CDP_PORT}/json/version`, { signal: AbortSignal.timeout(3000) })
    .then((r) => r.ok).catch(() => false);
}

function psExec(script) {
  return new Promise((resolve, reject) => {
    execFile("powershell.exe", ["-NoProfile", "-Command", script], { windowsHide: true },
      (err, stdout, stderr) => (err ? reject(new Error(stderr || err.message)) : resolve(stdout)));
  });
}

// 冷启动被测客户端(跨平台)。Mac 上 NAMICLAW_EXE 指向 .app/Contents/MacOS/ 内的可执行文件。
async function coldStartClient() {
  if (!NAMICLAW_EXE) throw new Error("未配置 NAMICLAW_EXE,无法启动 GUI 客户端(这台机器可能不该跑 gui 用例)");
  if (IS_WIN) {
    await psExec(
      `Get-Process namiclaw -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue;` +
      `Start-Sleep -Seconds 2;` +
      `Start-Process -FilePath '${NAMICLAW_EXE}' -ArgumentList '--remote-debugging-port=${CDP_PORT}'`
    );
  } else {
    // Mac/Linux:先杀旧实例(按可执行名),再 detached 冷启动带调试端口
    const name = NAMICLAW_EXE.split("/").pop();
    await new Promise((r) => execFile("pkill", ["-f", name], () => r()));
    await sleep(2000);
    // spawn 失败(路径不存在等)走异步 'error' 事件,不监听会以未捕获异常 crash 整个 runner
    // (try/catch 抓不到 EventEmitter 的 error)。这里转成 Promise,让上层 tick 的 catch 回写 fail。
    await new Promise((resolve, reject) => {
      const child = spawn(NAMICLAW_EXE, [`--remote-debugging-port=${CDP_PORT}`], { detached: true, stdio: "ignore" });
      child.once("error", reject);
      child.once("spawn", () => { child.unref(); resolve(); });
    });
  }
}

async function ensureNamiclaw() {
  if (await cdpAlive()) return;                    // 已就绪,直接用
  log("客户端 CDP 未就绪,冷启动中…");
  await coldStartClient();
  for (let i = 0; i < 15; i++) {                   // 最多等 30s
    await sleep(2000);
    if (await cdpAlive()) { log(`客户端 CDP 就绪(${(i + 1) * 2}s)`); return; }
  }
  throw new Error("namiclaw CDP 启动超时,9222 未就绪");
}

// ---- 调 Claude Code headless 执行一条用例,解析结构化结论 ----
// 输出契约放最前 + 给正反例:headless claude 天然倾向写 markdown 报告,弱约束会导致
// 「用例其实执行对了,但没输出约定 JSON」→ runner 解析失败误判 fail(实测踩过)。
const SYSTEM_PROMPT = `你是被测客户端的自动化测试执行器。从 stdin 读取一条用例 JSON(payload)并执行。

【最重要:输出格式】执行完毕后,你的**最后一行**必须是且只能是一个 JSON 对象,不要 markdown、不要代码块、不要解释性文字、不要 emoji。格式:
{"verdict":"pass","reason":"简述判定依据","evidence":""}
verdict 只能是 "pass" 或 "fail"。evidence 放截图/日志本地路径,没有就空字符串。
正例(最后一行): {"verdict":"pass","reason":"code 字段为 0,符合预期","evidence":""}
反例(禁止): 用 **PASS** ✅、"The test passes"、分点报告等自然语言表达结论。

【执行规则】
- **payload.precondition(前置条件/起始位置)若非空,先执行它再跑步骤**:它描述本用例应从哪里开始
  (如"在左侧栏会话记录里选一个含文件产物的会话进入")以及跑前需先做的准备步骤。你要先用 gui 工具
  把页面带到这个起点(找不到精确目标就选最符合描述的:如"合适的会话"取列表里第一个符合条件的),
  到位后再按 payload.steps 执行。precondition 为空则直接从 steps 开始(默认已在主界面)。
- 严格按 payload.steps 操作,对照 payload.expected 判定;禁止联网搜索,只在本地执行。
- GUI 用例:**只用 mcp__gui__* 工具**——先 gui_connect,再 gui_list_keys 看有哪些语义 key;
  定位元素**优先传 key**(gui_click/gui_fill/gui_get_text/gui_wait_for/gui_assert_text 都接 {key} 或 {selector}),
  注册表没覆盖的元素:先 gui_probe 探当前页拿候选选择器,再用其 best 当 {selector};gui_screenshot 存证。
  发送消息前必须先 gui_capture_response，提交后 gui_wait_response，不能把旧回答当作本轮完成。
  禁止自己写 Playwright、禁止用鼠标坐标。
- api 用例:用 curl / fetch 验证接口与响应。
- cli 用例:起进程并校验退出码 / 输出。
- 能用确定性断言就断言,不要"看一眼觉得对";判定不了或超时一律 verdict=fail。
- **只测被测客户端/接口本身,不研究"功能如何实现"**:禁止用 Bash/git/grep/ls/Read 去翻本地代码仓库或平台源码;
  你的工作目录可能恰好是某个代码仓库,忽略它,绝不 cd 进去或读它的文件。
- 读不出可自动化执行的步骤(如用例是功能描述/需求,而非"点哪、断言啥")→ **立即 verdict=fail**,
  reason 写"用例不可自动化执行:<原因>";**不要反问、不要研究代码、不要空等**。
- 工具边界:GUI/E2E 用例只用 mcp__gui__*;api 用例用 Bash 跑 curl/fetch;cli 用例用 Bash 起进程。别越界。
再次强调:最后一行必须是纯 JSON,这是机器解析的唯一依据。`;

// ---- claude CLI 调用底座(runClaude / makeJudge 共用,DRY)----
// 封装 claude headless 的正确 spawn 姿势:-p + prompt 走 stdin(不进 argv,防注入)、
// stream-json 流式解析、--allowedTools 单值白名单 + --disallowedTools 硬禁内置工具、
// 只挂本目录 gui MCP、无人值守硬超时 SIGKILL、spawn error 转 resolve(不 crash runner)。
// **只负责跑 claude 拿最终文本**,不解释语义(verdict / selector 由调用方各自解析)——
// 这样 runClaude(整条执行判 verdict)与 makeJudge(挑元素拿 selector)复用同一 spawn 逻辑而互不耦合。
//
// 入参:stdinData(喂给 claude 的 prompt 文本,走 stdin);opts:
//   { systemPrompt, allowedTools, disallowedTools:[], mcpConfig, timeoutMs, onEvent? }
// onEvent(ev):可选,每个 stream-json 事件回调一次(runClaude 用它做实时进度日志)。
// 返回(始终 resolve,不 reject):
//   { text, lastText, err, code, timedOut, spawnError, duration_ms }
//   text=result 事件最终文本;lastText=最后一段 assistant 文本(result 取不到时的兜底)。
function runClaudeRaw(stdinData, opts = {}) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timeoutMs = opts.timeoutMs || CLAUDE_TIMEOUT_MS;
    // GUI execution and precondition navigation share the current project's registry.
    // Pure judges keep the upstream empty MCP configuration.
    let registryDir = null;
    let mcpConfigPath = opts.mcpConfig === null ? EMPTY_MCP_CONFIG : (opts.mcpConfig || MCP_CONFIG);
    if (mcpConfigPath === MCP_CONFIG) {
      registryDir = mkdtempSync(join(tmpdir(), "qalab-gui-registry-"));
      const registryPath = join(registryDir, "selectors.json");
      writeFileSync(registryPath, JSON.stringify({ registry: guiCore.registry, vmIframe: guiCore.vmIframe, coreKeys: guiCore.coreKeys }), { mode: 0o600 });
      const mcpConfig = JSON.parse(readFileSync(MCP_CONFIG, "utf8"));
      mcpConfig.mcpServers.gui.args = [join(__rdir, "gui-mcp", "server.mjs")];
      mcpConfig.mcpServers.gui.env = {
        ...mcpConfig.mcpServers.gui.env,
        GUI_REGISTRY_FILE: registryPath, QALAB_CDP_URL: `http://127.0.0.1:${CDP_PORT}`,
      };
      mcpConfigPath = join(registryDir, "mcp.json");
      writeFileSync(mcpConfigPath, JSON.stringify(mcpConfig), { mode: 0o600 });
    }
    const args = [
      "-p",                                       // 不带参数值:prompt 从 stdin 读取(已实测支持)
      // 流式输出:claude 边执行边吐 JSON 事件,可逐条打进度(不再是"执行黑盒",能看到卡在哪步)。
      "--output-format", "stream-json",
      "--verbose",                                // stream-json 在 -p 下必须配 --verbose
      ...(opts.systemPrompt ? ["--append-system-prompt", opts.systemPrompt] : []),
      // 白名单必须是**一个**空格分隔的值;拆成多个 arg 会让 --allowedTools 只收到第一个、其余游离,
      // 约束失效→claude 回退到可用任意工具(含 WebSearch),导致跑偏(实测踩过)。
      ...(opts.allowedTools ? ["--allowedTools", opts.allowedTools] : []),
      // 硬禁内置工具:这是拦住 claude 跑偏去翻代码的关键,比 prompt 软约束可靠。
      ...(Array.isArray(opts.disallowedTools) && opts.disallowedTools.length
        ? ["--disallowedTools", ...opts.disallowedTools] : []),
      // MCP 配置(关键:隔离执行机全局 MCP/插件,否则 claude 启动会连 context7/playwright 等一堆 server +
      // 跑 SessionStart hook + 载几十个 skill,judge 每次白耗 30s 在 bootstrap 上,实测踩过)。
      // opts.mcpConfig===null(judge 纯判别)→ 用空 MCP 文件 + --strict-mcp-config,既隔离全局又不启动 gui server;
      // undefined(runClaude 整条执行)→ 用默认 MCP_CONFIG(含 gui server)+ strict。
      // 一律走"文件路径"(不传内联 JSON:win32 shell 会把 JSON 当路径,实测踩过);路径不含空格(目录已改名)。
      "--mcp-config", mcpConfigPath,
      "--strict-mcp-config",
      "--permission-mode", "acceptEdits",         // 无人值守:预授权,避免卡权限确认
    ];
    const child = spawn(CLAUDE_BIN, args, { shell: process.platform === "win32" });
    let err = "", buf = "", finalText = "", lastText = "", settled = false;
    // 单次结算:error / close / 超时 三条路径只认第一个,并清理定时器(避免重复 resolve)。
    const done = (extra) => {
      if (settled) return; settled = true; clearTimeout(timer);
      if (registryDir) { try { rmSync(registryDir, { recursive: true, force: true }); } catch {} }
      resolve({ text: finalText, lastText, err, duration_ms: Date.now() - started, ...extra });
    };
    // 无人值守硬超时:claude 卡在被测页/工具时杀掉,避免该 run 永久 running、后续全停摆。
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* 已退出 */ }
      done({ timedOut: true });
    }, timeoutMs);
    // spawn 失败(claude 未安装/PATH 不对)走异步 'error' 事件,不监听会 crash 整个 runner。
    child.on("error", (e) => done({ spawnError: e.message }));

    // stdout 是按行的 JSON 事件;跨 chunk 缓冲,只处理完整行。记录 result / 末段 assistant 文本,
    // 并把每个事件回调给调用方(实时进度日志各自实现)。
    child.stdout.on("data", (d) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (!line.trim()) continue;
        let ev;
        try { ev = JSON.parse(line); } catch { continue; }   // 半行/非 JSON,忽略
        if (ev.type === "result") finalText = ev.result ?? "";
        else if (ev.type === "assistant") {
          for (const b of ev.message?.content || []) {
            if (b.type === "text" && b.text?.trim()) lastText = b.text;
          }
        }
        if (typeof opts.onEvent === "function") { try { opts.onEvent(ev); } catch { /* 日志回调异常不影响解析 */ } }
      }
    });
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => done({ code }));
    // prompt 从 stdin 喂入(不进 argv,防 Windows 下 shell 元字符注入);写完即关闭,claude 读到 EOF 开跑。
    child.stdin.write(stdinData);
    child.stdin.end();
  });
}

// 前置条件导航器:有 precondition 的用例,先让 claude 把界面**导航到起始位置**(不跑测试步),
// 到位后由调用方把结构化 script 交给 StepExecutor 执行 —— 这样 precondition 用例也能走
// 多候选自愈 + 选择器回填 + 跑通即固化,而不是像纯 claude 路径那样完全不回填。
// claude 的 gui-mcp 与 runner 的 guiCore 都 connectOverCDP 同一 Electron(9222),导航状态共享,故可接力。
// NAV_SYSTEM_PROMPT / parseNavOk 抽到 precond-nav.mjs(纯逻辑,便于单测)。

async function runClaudePrecondition(payload, log) {
  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);
  const DENY = ["Bash", "BashOutput", "KillShell", "Read", "Glob", "Grep", "LS", "Edit", "MultiEdit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "TodoWrite"];
  // 只喂 precondition + title(不喂 steps/expected,避免 claude 顺手把测试也跑了)。
  const input = JSON.stringify({ precondition: payload?.precondition || "", title: payload?.title || "" });
  const onEvent = (ev) => {
    if (ev.type === "assistant") for (const b of ev.message?.content || []) {
      if (b.type === "tool_use") log(`  [+${sec()}s] [前置导航] → ${b.name} ${JSON.stringify(b.input || {}).slice(0, 120)}`);
      else if (b.type === "text" && b.text?.trim()) log(`  [+${sec()}s] [前置导航] 💭 ${b.text.trim().replace(/\s+/g, " ").slice(0, 100)}`);
    }
  };
  const raw = await runClaudeRaw(input, {
    systemPrompt: NAV_SYSTEM_PROMPT, allowedTools: "mcp__gui__*", disallowedTools: DENY,
    timeoutMs: CLAUDE_TIMEOUT_MS, onEvent,
  });
  const duration_ms = raw.duration_ms;
  if (raw.timedOut) return { ok: false, reason: "前置导航超时", duration_ms };
  if (raw.spawnError) return { ok: false, reason: `无法启动 claude: ${raw.spawnError}`, duration_ms };
  const nav = parseNavOk(raw.text || raw.lastText);
  if (!nav) return { ok: false, reason: "claude 未返回可解析的到位结论", duration_ms };
  return { ...nav, duration_ms };
}

async function runClaude(payload, kind) {
  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);   // 相对起始的秒数,标在每条进度前
  // 按 kind 给最小工具集:gui/e2e 只给 gui-mcp(不给 Bash,杜绝 claude 跑去翻代码/执行命令);
  // api/cli 才给 Bash(curl/fetch/起进程)。工具越权是之前 claude 跑偏去研究平台源码的口子。
  const allowed = (kind === "api" || kind === "cli") ? "Bash" : "mcp__gui__*";
  // **硬禁内置工具**(关键):--allowedTools 只是"额外允许",不排除 Bash/Read/Grep 等内置工具——
  // 光靠 SYSTEM_PROMPT 软约束拦不住,claude 会去 grep/Read 翻本地仓库源码而不调 gui(实测踩过)。
  // gui/e2e 一个内置工具都不给;api/cli 保留 Bash 但禁掉一切"翻代码/联网/改文件"的工具。
  const READ_CODE_TOOLS = ["Read", "Glob", "Grep", "LS", "Edit", "MultiEdit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "TodoWrite"];
  const disallowed = (kind === "api" || kind === "cli")
    ? READ_CODE_TOOLS                                       // 留 Bash,禁翻代码/联网/改文件
    : ["Bash", "BashOutput", "KillShell", ...READ_CODE_TOOLS]; // gui/e2e:内置工具全禁,只剩 mcp__gui__*
  // 逐个 stream 事件 → 实时进度日志(看清"执行到哪步、每步多久")。
  const onEvent = (ev) => {
    if (ev.type === "system" && ev.subtype === "init") {
      log(`  [+${sec()}s] claude 就绪 MCP=${JSON.stringify((ev.mcp_servers || []).map((s) => s.name))}`);
    } else if (ev.type === "assistant") {
      for (const b of ev.message?.content || []) {
        if (b.type === "tool_use") log(`  [+${sec()}s] → ${b.name} ${JSON.stringify(b.input || {}).slice(0, 160)}`);
        else if (b.type === "text" && b.text?.trim()) log(`  [+${sec()}s] 💭 ${b.text.trim().replace(/\s+/g, " ").slice(0, 120)}`);
      }
    } else if (ev.type === "user") {
      for (const b of ev.message?.content || []) {   // 工具返回只在出错时打(否则太吵)
        if (b.type === "tool_result" && b.is_error) {
          const tx = Array.isArray(b.content) ? b.content.map((c) => c.text || "").join(" ") : String(b.content || "");
          log(`  [+${sec()}s] ⚠ 工具报错 ${tx.replace(/\s+/g, " ").slice(0, 160)}`);
        }
      }
    } else if (ev.type === "result") {
      log(`  [+${sec()}s] claude 结束 turns=${ev.num_turns} is_error=${ev.is_error}`);
    }
  };
  // 安全:用例 payload(用户可控 —— steps/expected 等自由文本,经平台入队流入)通过 stdin 传入,
  // **不进命令行 argv**。否则在 Windows(执行 claude.cmd 必须 shell:true)下,payload 里的
  // " & | % 等元字符会被 cmd.exe 解释导致命令注入(能编辑用例的成员即可在执行机上 RCE)。
  const raw = await runClaudeRaw(JSON.stringify(payload), {
    systemPrompt: SYSTEM_PROMPT, allowedTools: allowed, disallowedTools: disallowed,
    timeoutMs: CLAUDE_TIMEOUT_MS, onEvent,
  });
  const duration_ms = raw.duration_ms;
  // 无人值守硬超时:claude 卡在被测页/工具时已杀掉,回写 fail,避免该 run 永久 running、后续全停摆。
  if (raw.timedOut) return { verdict: "fail", reason: `claude 执行超时(>${CLAUDE_TIMEOUT_MS}ms)已终止`, duration_ms };
  // spawn 失败(claude 未安装/PATH 不对):转一次 fail 结论回写,而非拖垮进程。
  if (raw.spawnError) return { verdict: "fail", reason: `无法启动 claude(${CLAUDE_BIN}): ${raw.spawnError}`, duration_ms };
  // 结论取自 result 事件的最终文本(取不到再退到最后一段 assistant 文本);解析出约定的 verdict JSON。
  const verdict = parseVerdict(raw.text || raw.lastText);
  if (!verdict) {
    const tail = String(raw.text || raw.lastText || raw.err || "").replace(/\s+/g, " ").slice(-500);
    return { verdict: "fail", reason: `无法解析Claude输出(exit ${raw.code}): ${tail}`, duration_ms };
  }
  return { ...verdict, duration_ms };
}

// judge 步专用:让 claude 只对**一个主观问题**做判定(如"AI 回复是否合理"),喂前面步骤捕获的 context。
// 与 runClaude 不同:禁一切工具(纯文本判断)、短超时、只回 {pass,reason}。供 StepExecutor 的 judge 步调用。
function judgeWithClaude(question, context) {
  return new Promise((resolve) => {
    const sys = "你是测试判定器。仅根据给出的问题与上下文做主观判定,不使用任何工具、不联网。"
      + '你的**最后一行**必须是且只能是一个 JSON:{"verdict":"pass","reason":"简述依据"};verdict 只能 pass 或 fail。';
    const input = `问题(判定点):${question}\n\n上下文(前面步骤捕获的页面文本/证据):\n${context || "(无)"}`;
    // 禁所有可能让它跑偏的工具(judge 是纯判断,不需要任何工具)
    const DENY = ["Bash", "BashOutput", "KillShell", "Read", "Glob", "Grep", "LS", "Edit", "MultiEdit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task", "TodoWrite"];
    const args = ["-p", "--output-format", "json", "--append-system-prompt", sys,
      "--disallowedTools", ...DENY, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
      "--permission-mode", "acceptEdits"];
    const child = spawn(CLAUDE_BIN, args, { shell: process.platform === "win32" });
    let out = "", settled = false;
    const done = (r) => { if (settled) return; settled = true; clearTimeout(timer); resolve(r); };
    const timer = setTimeout(() => { try { child.kill("SIGKILL"); } catch {} done({ pass: false, reason: "judge 超时" }); }, 90000);
    child.on("error", (e) => done({ pass: false, reason: `judge 无法启动 claude: ${e.message}` }));
    child.stdout.on("data", (d) => (out += d));
    child.on("close", () => {
      const v = parseVerdict(out);
      done(v ? { pass: v.verdict === "pass", reason: v.reason || "" } : { pass: false, reason: "judge 未返回可解析结论" });
    });
    child.stdin.write(input);
    child.stdin.end();
  });
}

// Claude --output-format json 会把最终文本包在信封里;从中抽出我们约定的那条结论 JSON。
function parseVerdict(raw) {
  let text = raw;
  try { const j = JSON.parse(raw); text = j.result ?? j.text ?? raw; } catch { /* 非 JSON 信封,按裸文本处理 */ }
  text = String(text);
  // 括号配平扫描出所有 JSON 对象子串,从后往前取第一个「能解析且 verdict∈{pass,fail}」的。
  // 比单条正则健壮:容忍前置解释文字、markdown ```json 代码块、reason 内含花括号、pretty 多行。
  const candidates = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== "{") continue;
    let depth = 0;
    for (let j = i; j < text.length; j++) {
      if (text[j] === "{") depth++;
      else if (text[j] === "}" && --depth === 0) { candidates.push(text.slice(i, j + 1)); break; }
    }
  }
  for (let k = candidates.length - 1; k >= 0; k--) {
    try {
      const obj = JSON.parse(candidates[k]);
      if (obj && (obj.verdict === "pass" || obj.verdict === "fail")) return obj;
    } catch { /* 该候选非合法 JSON,继续往前试 */ }
  }
  return null;
}

// ---- 设备探测处理(与 exec 主循环并列)----
// 每轮拉本机 pending 探测,按 params.mode 分派:
//   discover → guiCore.probe(扫当前页元素,产候选选择器);
//   verify   → guiCore.verifyKeys(校验当前作用域已登记的 key 是否还命中当前页)。
// 探测前先按 project_id/sub_product 拉合并注册表换入 gui-core(空 DB→null 则保留内置兜底,不换)。
// 单条探测抛错(无真实设备/CDP 未起时 gui.probe/verifyKeys 会抛)→ 回写 error,不影响其余探测与主循环。
async function handleProbes() {
  let list = [];
  try {
    const res = await fetchProbes();
    list = res?.data || res || [];   // api() 已解包 data;res?.data 仅为防御(见 fetchRegistry)
  } catch (e) {
    log("拉探测队列失败:", e.message);
    return;
  }
  if (!list.length) return;
  log(`拉到 ${list.length} 条待探测`);
  for (const p of list) {
    try {
      // 探测同样需要被测客户端带 CDP 在跑(与 exec 主循环的 gui/e2e 分支一致):没启动则冷启动并等 CDP 就绪。
      // 未配 NAMICLAW_EXE / CDP 超时会抛错 → 走下方 catch 回写 error,网页立即显示具体原因(而非干等 60s 超时)。
      await ensureNamiclaw();
      // 冷启动后页面还在加载:connect() = ensureConnected + waitForContentFrame(等业务 iframe 出现,≤8s),
      // 加载完再扫元素,避免探到空白页/加载中元素。客户端已开着时 iframe 早就绪,首轮即命中、几乎零等待。
      await guiCore.connect();
      const reg = await fetchRegistry(p.project_id, p.sub_product || "");
      guiCore.setRegistry(reg?.registry, reg?.vmIframe, reg?.coreKeys);
      if ((p.params || {}).mode === "validate_selection") {
        await reportProbe(p.id, { result: await guiCore.validateSelection(p.params) });
      } else if ((p.params || {}).mode === "verify") {
        // verify:校验 key 是否还命中当前页。core=true 巡检核心 key 集(失效即在 failed 里告警)。
        if ((p.params || {}).core) {
          const out = await guiCore.verifyCoreKeys((p.params || {}).keys);
          await reportProbe(p.id, { result: out });
        } else {
          const keys = Array.isArray(p.params?.keys) ? p.params.keys : Object.keys((reg && reg.registry) || {});
          const out = await guiCore.verifyKeys(keys);
          await reportProbe(p.id, { result: out });
        }
      } else {
        // discover:扫当前页元素拿候选选择器 + 整页截图(供网页叠框标注)。
        const out = await guiCore.probe({ ...(p.params || {}), screenshot: true });
        // 坐标数据(小)进 result TEXT;截图(大)走独立二进制端点,不塞 result(避免撑爆 MySQL 5.6 的 64KB)。
        await reportProbe(p.id, { result: { groups: out.groups, pageSize: out.pageSize } });
        const shot = out.screenshotBuffer;
        if (shot && shot.length) {
          try { await uploadProbeShot(p.id, shot); log(`  截图已上传 id=${p.id} (${(shot.length / 1024).toFixed(0)}KB)`); }
          catch (e) { log(`  截图上传失败 id=${p.id}: ${e.message}`); }
        }
      }
      log(`回写探测 id=${p.id} mode=${(p.params || {}).mode || "discover"} -> done`);
    } catch (e) {
      log(`探测 id=${p.id} 异常:`, e.message);
      try { await reportProbe(p.id, { error: String(e.message || e) }); } catch {}
    }
  }
}

// ---- 录制队列(与 exec/probe 并列)----
// 首次见到某会话 → startRecording(注入捕获);每轮 drain 页面缓冲 → rawEventToStep 规整 → 去抖 → 增量上报。
// 会话不再出现在 recording 列表(被 stop/删) → stopRecording 并清本地跟踪。
const recordingPump = createRecordingPump({ gui: guiCore, upload: reportRecordEvents,
  directory: join(__rdir, ".record-outbox", createHash("sha256").update(BASE_URL + RUNNER_ID).digest("hex").slice(0, 16)), consumerId: RECORD_CONSUMER });
async function handleRecordings() {
  try {
    const res = await fetchRecords();
    const list = res?.data || res || [];
    if (!list.length) { await recordingPump.release(); return false; }
    await ensureNamiclaw();
    const session = list[0];
    if (recordingPump.activeId !== session.id) {
      const reg = await fetchRegistry(session.project_id, session.sub_product || "");
      guiCore.setRegistry(reg.registry, reg.vmIframe, reg.coreKeys);
    }
    return await recordingPump.advance(session);
  } catch (e) {
    log("录制等待重试:", e.message);
    return true; // 状态未确认时不运行会改动同一页面的执行/探测任务。
  }
}

// perf 采集:与 exec/probe 并列的第三条队列(独立 try,异常不影响其他轮询)。
async function handlePerf() {
  await pollPerfOnce({ api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID });
}

// ---- 主循环 ----
async function tick() {
  const pending = await fetchPending();
  if (!pending?.length) return;
  log(`拉到 ${pending.length} 条待执行`);
  const batch = [];   // 本批各条结果(供结束语汇总)
  for (const item of pending) {
    let claimed = false;
    let heartbeatTimer;
    try {
      await claim(item.run_id);
      claimed = true;
      const heartbeat = () => api("POST", `/api/exec-queue/${item.run_id}/heartbeat?runner=${encodeURIComponent(RUNNER_ID)}`)
        .catch(e => log(`run_id=${item.run_id} 心跳失败: ${e.message}`));
      await heartbeat();
      heartbeatTimer = setInterval(heartbeat, 60000);
      heartbeatTimer.unref();
      log(`执行 run_id=${item.run_id} kind=${item.kind} case=${item.case_id}`);

      let result;
      if (DRY) {
        result = { verdict: "pass", reason: "dry-run 握手验证", duration_ms: 1 };
      } else if (item.kind === "manual") {
        // 纵深防御:manual=不可自动化,平台本不该派发(enqueue 已拒);万一漏下发到这,
        // 直接判 fail 说明,绝不塞给 claude 当 GUI 跑(否则空耗 + 误导)。
        result = { verdict: "fail", reason: "该用例为人工/不可自动化(manual),不应下发到执行机;请在平台改判类型或取消下发", duration_ms: 1 };
      } else if (item.kind === "gui" || item.kind === "e2e") {
        await ensureNamiclaw();                          // GUI/E2E:先确保客户端带 CDP 在跑
        // 执行前从平台拉该项目的合并注册表(DB 单源)换入 gui-core;失败/无则按当前项目回落缓存或内置文件，清除上个项目配置。
        const reg = item.payload?.selector_registry ? registrySnapshot(item.payload.selector_registry)
          : await fetchRegistry(item.payload?.project_id, item.payload?.sub_product || "");
        guiCore.setRegistry(reg?.registry, reg?.vmIframe, reg?.coreKeys);
        result = await runWithTrace(guiCore, async () => {
          let result;
          // 用例前硬复位(reload):清上一条遗留的选中/弹窗/输入残留等瞬态,保证从初始主界面开始。
          // 复位失败或复位后掉登录 → 记 blocked(fail_kind=selector,环境阻塞,不计功能失败率),不空跑脏态用例。
          // restartClientFn 作为兜底注入:reload 复位全失败 / 多轮自愈后仍未回稳时,退出客户端重新启动再复位一次。
          const restartClientFn = async () => {
            await guiCore.close?.();       // 断开 CDP 连接,让 ensureNamiclaw 重建
            await coldStartClient();       // 杀旧进程 + 重新启动带 CDP 参数
            for (let i = 0; i < 15; i++) {
              await sleep(2000);
              if (await cdpAlive()) { log(`  客户端 CDP 就绪(${(i + 1) * 2}s)`); return; }
            }
            throw new Error("重启后 CDP 超时,9222 未就绪");
          };
          const gate = RESET_BETWEEN_CASES ? await resetOrBlock(guiCore, log, { restartClientFn }) : { ok: true };
          if (!gate.ok) {
            result = gate.result;
          } else {
            const script = item.payload?.script;
            const hasPrecond = !!(item.payload?.precondition && String(item.payload.precondition).trim());
            const hasScript = Array.isArray(script) && script.length;
            // 保留前置导航：先导航到起始位置，再由共享执行器执行结构化步骤。
            // 导航未返回到位结论时沿用远端的尽力执行策略，由步骤断言决定结果。
            if (hasPrecond && hasScript) {
              const nav = await runClaudePrecondition(item.payload, log);
              log(`  ⇢ 前置导航${nav.ok ? "到位" : "未确认到位(仍尝试执行 script)"}:${nav.reason || ""}`);
            }
            if (hasScript) {
              const r = await runScript(guiCore, script, (m) => log(m), judgeWithClaude);
              if (r.needClaude) { log(`  script 需降级:${r.reason}`); result = await runClaude(item.payload, item.kind); }
              else result = r;
            } else {
              result = await runClaude(item.payload, item.kind);
            }
          }
          return result;
        }, { runId: item.run_id, directory: join(__rdir, "evidence"), mode: process.env.GUI_TRACE || "failures" });
      } else if (item.kind === "api") {
        // api:有结构化 script → 确定性执行器(不经 LLM);无/降级 → claude(+Bash)兜底。
        const script = item.payload?.script;
        if (Array.isArray(script) && script.length) {
          const r = await apiRun(script, item.payload?.api_env || {}, (m) => log(m));
          if (r.needClaude) { log(`  api script 需降级:${r.reason}`); result = await runClaude(item.payload, item.kind); }
          else result = r;
        } else {
          result = await runClaude(item.payload, item.kind);
        }
      } else if (item.kind === "cli") {
        result = await runClaude(item.payload, item.kind);   // cli:仍走 claude(+Bash)
      } else {
        // 未知 kind:不猜,直接 fail(避免又当 gui 塞给 claude)
        result = { verdict: "fail", reason: `未知执行类型 kind=${item.kind},runner 不支持`, duration_ms: 1 };
      }

      if (result.tracePath) {
        result.report ||= [];
        if (!result.report.length) result.report.push({ action: "diagnostics", desc: "执行追踪", ok: result.verdict === "pass" });
        try { result.report.at(-1).trace_url = await uploadExecTrace(item.run_id, result.tracePath); }
        catch (e) { result.report.at(-1).trace_error = `${e.message}；文件保留在执行机 ${result.tracePath}`; }
      }
      // 有逐步报告(gui/e2e StepExecutor 产)→ 先把每步截图 Buffer 上传换成 URL,随回写落库。
      let reportJson = null;
      if (Array.isArray(result.report) && result.report.length) {
        try { reportJson = await uploadReportShots(item.run_id, result.report); }
        catch (e) { log(`  报告截图处理失败 run_id=${item.run_id}: ${e.message}`); }
      }

      await report(item.run_id, {
        verdict: result.verdict,
        fail_kind: result.fail_kind ?? null,   // selector=选择器/环境阻塞(后端映射 blocked);business=功能失败;pass 时 null
        reason: result.reason ?? "",
        evidence_url: result.evidence ?? null,
        duration_ms: result.duration_ms ?? null,
        report: reportJson,
      });
      // 定位候选建议上报:本条用例定位失败后发现的候选只进入评审，不修改本条结果,
      // 把铸造的候选连同证据推给平台进「自学习待确认」评审队列。失败不影响回写主流程。
      try {
        const heals = guiCore.drainHeals?.() || [];
        if (heals.length) {
          await api("POST", "/api/selectors/learned", {
            project_id: item.payload?.project_id, sub_product: item.payload?.sub_product || "",
            runner: RUNNER_ID, run_id: item.run_id, items: heals,
          });
          log(`  自学习上报 ${heals.length} 个 key: ${heals.map((h) => h.key).join(",")}`);
        }
      } catch (e) { log(`  自学习上报失败(不影响回写): ${e.message}`); }
      // 回写日志带上 reason + 耗时:无人值守时不必翻 UI 就能看出为什么 fail(解析失败/断言不过/超时)。
      const reasonTail = result.reason ? ` reason=${String(result.reason).replace(/\s+/g, " ").slice(0, 300)}` : "";
      log(`回写 run_id=${item.run_id} -> ${result.verdict} (${result.duration_ms ?? "?"}ms)${reasonTail}`);
      batch.push(result);
    } catch (e) {
      log(`run_id=${item.run_id} 执行异常:`, e.message);
      // runner 侧异常(连接/客户端/网络类)属环境阻塞,归 selector(不计功能失败率)。
      if (claimed) {
        try { await report(item.run_id, { verdict: "fail", fail_kind: "selector", reason: `runner异常: ${e.message}` }); } catch {}
      }
      batch.push({ verdict: "fail", fail_kind: "selector" });
    } finally {
      clearInterval(heartbeatTimer);
    }
  }
  // 本批结束语:一批跑完给个明确收尾状态(此前静默结束,无人值守时看不出跑没跑完)。
  log(summarizeBatch(batch).text);
}

async function main() {
  log(`runner 启动 base=${BASE_URL} runner=${RUNNER_ID} dry=${DRY}`);
  if (!RUNNER_TOKEN) log("警告: 未设置 RUNNER_TOKEN");
  log(`perf 采集就绪 perfdog=${PERFDOG_DIR}`);
  for (;;) {
    if (await handleRecordings()) { await sleep(POLL_MS); continue; }
    try { await tick(); } catch (e) { log("轮询异常:", e.message); }
    // exec 轮询之后并列处理设备探测队列(独立 try,探测异常不影响下一轮 exec 轮询)。
    try { await handleProbes(); } catch (e) { log("探测轮询异常:", e.message); }
    // 并列处理录制队列(独立 try)。
    // 再并列处理 perf 采集队列(独立 try,采集异常不影响下一轮其他轮询)。
    try { await handlePerf(); } catch (e) { log("perf 轮询异常:", e.message); }
    await sleep(POLL_MS);
  }
}

// 进程级兜底:任何漏网的未捕获异常/Promise 拒绝都只记日志,绝不让 runner 静默退出
// (无人值守进程一旦崩溃,后续用例全部停摆且不回写)。
process.on("uncaughtException", (e) => log("未捕获异常(已忽略,继续轮询):", e.message));
process.on("unhandledRejection", (e) => log("未处理拒绝(已忽略,继续轮询):", e?.message || e));

// 入口:--update 自升级(run.sh/run.cmd 启动前调;updated → exit 75 通知外层重启);
// upload 子命令直传本地 session;否则常驻三队列轮询。
const _argv = process.argv.slice(2).filter((a) => !a.startsWith("--"));
if (process.argv.includes("--update")) {
  const dir = dirname(fileURLToPath(import.meta.url));
  selfUpdate({ baseUrl: BASE_URL, token: RUNNER_TOKEN, dir, log })
    .then((r) => process.exit(r === "updated" ? 75 : 0))
    .catch((e) => { log("[update] 异常(跳过):", e.message); process.exit(0); });
} else if (_argv[0] === "upload") {
  const ctx = { api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID };
  uploadLocalSessions(ctx, _argv[1]).then(() => process.exit(0)).catch((e) => { log("upload 失败:", e.message); process.exit(1); });
} else {
  main();
}
