#!/usr/bin/env node
// qalab 本地执行 runner —— 轮询平台待执行队列,调用 Claude Code(headless)执行,回写 pass/fail。
// 纯 Node(v18+ 内置 fetch),无外部依赖。本机 python 在 git-bash 下无法 fork,故 runner 用 node。
//
// 用法:
//   BASE_URL=https://qalab.claw.qihoo.net RUNNER_TOKEN=xxx RUNNER_ID=win-01 node runner.mjs
//   加 --dry 只跑握手(拉取 + 回写假结果),不真正调 Claude,用于先验证与平台连通。

import { spawn, execFile } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createGuiCore } from "./gui-mcp/gui-core.mjs";
import { runScript } from "./step-executor.mjs";

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
const DRY          = process.argv.includes("--dry");

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

// 从平台拉某项目/子产品的合并注册表(DB 单源),缓存 by `${project_id}|${sub}`。三种情形都不清空内置兜底:
//   ① DB 说空(registry 无 key)→ 返回旧缓存或 null,**不写空缓存**(避免污染),runner 跳过 setRegistry、保留内置 57 key;
//   ② API 不可达(异常)→ 用缓存或 null;③ 拿到非空注册表 → 缓存并返回。
// data 取法:api() 已解包 {code,msg,data} 返回 data 本身(见 fetchPending);`res?.data || res` 仅为防御。
const _regCache = new Map();  // `${project_id}|${sub}` -> {version, registry, vmIframe}
async function fetchRegistry(projectId, sub = "") {
  if (!projectId) return null;
  const ck = `${projectId}|${sub}`;
  try {
    const res = await api("GET", `/api/selectors?project_id=${projectId}&sub_product=${encodeURIComponent(sub)}`);
    const data = res?.data || res;   // api() 已解包则直接是 data
    // 空 DB(registry 为空/无 key)→ 保留内置兜底或旧缓存,绝不用 {} 覆盖(否则该项目所有 key 定位全 fail)。
    if (!data || !data.registry || !Object.keys(data.registry).length) {
      return _regCache.get(ck) || null;
    }
    _regCache.set(ck, data);
    return data;
  } catch (e) {
    log(`拉注册表失败(${ck}):${e.message};回落${_regCache.has(ck) ? "缓存" : "内置文件"}`);
    return _regCache.get(ck) || null;   // 有缓存用缓存,否则 null→gui-core 用内置文件
  }
}

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
- 严格按 payload.steps 操作,对照 payload.expected 判定;禁止联网搜索,只在本地执行。
- GUI 用例:**只用 mcp__gui__* 工具**——先 gui_connect,再 gui_list_keys 看有哪些语义 key;
  定位元素**优先传 key**(gui_click/gui_fill/gui_get_text/gui_wait_for/gui_assert_text 都接 {key} 或 {selector}),
  注册表没覆盖的元素:先 gui_probe 探当前页拿候选选择器,再用其 best 当 {selector};gui_screenshot 存证。
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

function runClaude(payload, kind) {
  return new Promise((resolve) => {
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
    // 安全:用例 payload(用户可控 —— steps/expected 等自由文本,经平台入队流入)通过 stdin 传入,
    // **不进命令行 argv**。否则在 Windows(执行 claude.cmd 必须 shell:true)下,payload 里的
    // " & | % 等元字符会被 cmd.exe 解释导致命令注入(能编辑用例的成员即可在执行机上 RCE)。
    // 移到 stdin 后,argv 只剩固定 flag 与固定 SYSTEM_PROMPT(攻击者不可控),shell 引用不完美也无法被利用。
    const args = [
      "-p",                                       // 不带参数值:prompt 从 stdin 读取(已实测支持)
      // 流式输出:claude 边执行边吐 JSON 事件,runner 逐条打进度(不再是"执行黑盒",能看到卡在哪步)。
      "--output-format", "stream-json",
      "--verbose",                                // stream-json 在 -p 下必须配 --verbose
      "--append-system-prompt", SYSTEM_PROMPT,
      // 白名单必须是**一个**空格分隔的值;写成 "Bash","mcp__gui__*" 两个 arg 会让 --allowedTools
      // 只收到 "Bash"、另一个游离,约束失效→claude 回退到可用任意工具(含 WebSearch),
      // 导致跑偏、不聚焦执行、不输出结论 JSON(实测踩过)。按 kind 收敛见上方 allowed。
      "--allowedTools", allowed,
      // 硬禁内置工具(见上方 disallowed):这是拦住 claude 跑偏去翻代码的关键,比 prompt 软约束可靠。
      "--disallowedTools", ...disallowed,
      // 只加载 gui 这一个 MCP server(见 MCP_CONFIG),屏蔽执行机上用户全局 MCP;否则 claude 启动会
      // 连带 spawn 一堆无关 server(context7/figma/playwright…),拖慢启动甚至长挂(Mac 实测踩过)。
      "--mcp-config", MCP_CONFIG,
      "--strict-mcp-config",
      "--permission-mode", "acceptEdits",         // 无人值守:预授权,避免卡权限确认
    ];
    const child = spawn(CLAUDE_BIN, args, { shell: process.platform === "win32" });
    let err = "", buf = "", finalText = "", lastText = "", settled = false;
    // 单次结算:error / close / 超时 三条路径只认第一个,并清理定时器(避免重复 resolve)。
    const done = (r) => { if (settled) return; settled = true; clearTimeout(timer); resolve(r); };
    // 无人值守硬超时:claude 卡在被测页/工具时杀掉并回写 fail,避免该 run 永久 running、后续全停摆。
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* 已退出 */ }
      done({ verdict: "fail", reason: `claude 执行超时(>${CLAUDE_TIMEOUT_MS}ms)已终止`, duration_ms: Date.now() - started });
    }, CLAUDE_TIMEOUT_MS);
    // spawn 失败(claude 未安装/PATH 不对)走异步 'error' 事件,不监听会 crash 整个 runner。
    // 转成一次 fail 结论回写,而非拖垮进程。
    child.on("error", (e) => done({ verdict: "fail", reason: `无法启动 claude(${CLAUDE_BIN}): ${e.message}`, duration_ms: Date.now() - started }));

    // 逐个 stream 事件 → 实时进度日志(看清"执行到哪步、每步多久")。
    const onEvent = (ev) => {
      if (ev.type === "system" && ev.subtype === "init") {
        log(`  [+${sec()}s] claude 就绪 MCP=${JSON.stringify((ev.mcp_servers || []).map((s) => s.name))}`);
      } else if (ev.type === "assistant") {
        for (const b of ev.message?.content || []) {
          if (b.type === "tool_use") log(`  [+${sec()}s] → ${b.name} ${JSON.stringify(b.input || {}).slice(0, 160)}`);
          else if (b.type === "text" && b.text?.trim()) { lastText = b.text; log(`  [+${sec()}s] 💭 ${b.text.trim().replace(/\s+/g, " ").slice(0, 120)}`); }
        }
      } else if (ev.type === "user") {
        for (const b of ev.message?.content || []) {   // 工具返回只在出错时打(否则太吵)
          if (b.type === "tool_result" && b.is_error) {
            const tx = Array.isArray(b.content) ? b.content.map((c) => c.text || "").join(" ") : String(b.content || "");
            log(`  [+${sec()}s] ⚠ 工具报错 ${tx.replace(/\s+/g, " ").slice(0, 160)}`);
          }
        }
      } else if (ev.type === "result") {
        finalText = ev.result ?? "";
        log(`  [+${sec()}s] claude 结束 turns=${ev.num_turns} is_error=${ev.is_error}`);
      }
    };
    // stdout 是按行的 JSON 事件;跨 chunk 缓冲,只处理完整行。
    child.stdout.on("data", (d) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (line.trim()) { try { onEvent(JSON.parse(line)); } catch { /* 半行/非 JSON,忽略 */ } }
      }
    });
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => {
      const duration_ms = Date.now() - started;
      // 结论取自 result 事件的最终文本(取不到再退到最后一段 assistant 文本);解析出约定的 verdict JSON。
      const verdict = parseVerdict(finalText || lastText);
      if (!verdict) {
        const tail = String(finalText || lastText || err || "").replace(/\s+/g, " ").slice(-500);
        return done({ verdict: "fail", reason: `无法解析Claude输出(exit ${code}): ${tail}`, duration_ms });
      }
      done({ ...verdict, duration_ms });
    });
    // 用例 payload 从 stdin 喂入(见上方安全说明);写完即关闭,claude 读到 EOF 开始执行。
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
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

// ---- 主循环 ----
async function tick() {
  const pending = await fetchPending();
  if (!pending?.length) return;
  log(`拉到 ${pending.length} 条待执行`);
  for (const item of pending) {
    try {
      await claim(item.run_id);
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
        // 执行前从平台拉该项目的合并注册表(DB 单源)换入 gui-core;失败/无则不换,沿用内置文件(回落)。
        const reg = await fetchRegistry(item.payload?.project_id, "");
        if (reg && reg.registry) guiCore.setRegistry(reg.registry, reg.vmIframe);
        const script = item.payload?.script;
        // 有结构化 script → StepExecutor 确定性执行(不经 LLM);无/需降级 → 回退 claude 兜底。
        if (Array.isArray(script) && script.length) {
          const r = await runScript(guiCore, script, (m) => log(m), judgeWithClaude);
          if (r.needClaude) { log(`  script 需降级:${r.reason}`); result = await runClaude(item.payload, item.kind); }
          else result = r;
        } else {
          result = await runClaude(item.payload, item.kind);
        }
      } else if (item.kind === "api" || item.kind === "cli") {
        result = await runClaude(item.payload, item.kind);   // api/cli:走 claude(+Bash)
      } else {
        // 未知 kind:不猜,直接 fail(避免又当 gui 塞给 claude)
        result = { verdict: "fail", reason: `未知执行类型 kind=${item.kind},runner 不支持`, duration_ms: 1 };
      }

      await report(item.run_id, {
        verdict: result.verdict,
        reason: result.reason ?? "",
        evidence_url: result.evidence ?? null,
        duration_ms: result.duration_ms ?? null,
      });
      // 回写日志带上 reason + 耗时:无人值守时不必翻 UI 就能看出为什么 fail(解析失败/断言不过/超时)。
      const reasonTail = result.reason ? ` reason=${String(result.reason).replace(/\s+/g, " ").slice(0, 300)}` : "";
      log(`回写 run_id=${item.run_id} -> ${result.verdict} (${result.duration_ms ?? "?"}ms)${reasonTail}`);
    } catch (e) {
      log(`run_id=${item.run_id} 执行异常:`, e.message);
      try { await report(item.run_id, { verdict: "fail", reason: `runner异常: ${e.message}` }); } catch {}
    }
  }
}

async function main() {
  log(`runner 启动 base=${BASE_URL} runner=${RUNNER_ID} dry=${DRY}`);
  if (!RUNNER_TOKEN) log("警告: 未设置 RUNNER_TOKEN");
  for (;;) {
    try { await tick(); } catch (e) { log("轮询异常:", e.message); }
    await sleep(POLL_MS);
  }
}

// 进程级兜底:任何漏网的未捕获异常/Promise 拒绝都只记日志,绝不让 runner 静默退出
// (无人值守进程一旦崩溃,后续用例全部停摆且不回写)。
process.on("uncaughtException", (e) => log("未捕获异常(已忽略,继续轮询):", e.message));
process.on("unhandledRejection", (e) => log("未处理拒绝(已忽略,继续轮询):", e?.message || e));

main();
