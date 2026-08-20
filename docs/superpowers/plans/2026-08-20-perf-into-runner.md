# perf 采集并入 qalab-runner 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 qalab-runner 一个进程同时认领 exec/probe/perf 三类任务,perf-agent 退役。

**Architecture:** 采集核心从 perf-agent.mjs 迁移到共享模块 `perf-collect.mjs`(依赖注入 ctx,不自带配置/网络);runner.mjs 主循环新增第三个并列 `handlePerf()`,入口新增 `upload` 子命令;分发从 perf-agent.zip 迁移到 qalab-runner.zip。

**Tech Stack:** 纯 Node v18+(内置 fetch/child_process/fs,零第三方依赖);测试用 `node:test` + `node:assert/strict`;分发用 Windows .bat + PowerShell Compress-Archive。

**Spec:** `docs/superpowers/specs/2026-08-20-perf-into-runner-design.md`

## Global Constraints

- **零第三方依赖**:runner.mjs / perf-collect.mjs 只用 Node 内置模块(不新增 npm 包)。
- **不改后端**:`/api/perf/*` 接口契约不变;runner 侧鉴权沿用 `Authorization: Bearer <RUNNER_TOKEN>`。
- **API 返回约定**:runner 的 `api(method, path, body)` 返回已解包的 `data`(与 perf-agent 的 `api` 一致);perf-collect 通过 `ctx.api` 复用它。
- **采集引擎定位**:`PERFDOG_DIR` 默认"自身目录有 `nami-perfdog.mjs` 则用自身,否则回落 `D:/git/test/nami-perfdog`";`SESSIONS_DIR = join(PERFDOG_DIR, 'sessions')`。
- **测试模式**:跟随 `api-executor.test.mjs` —— `import { test } from "node:test"`、`import assert from "node:assert/strict"`、桩用 `stubFetch` 模式;跑测试用 `node --test`。
- **Windows 编码**:所有 .bat 用 UTF-8(无 BOM)+ CRLF;.env 加载已处理 BOM/CRLF/引号。
- **交互协议逐行等价**:runPerfdog 的 perfdog stdin/stdout ↔ 平台 prompt/signal 协议,迁移时保持与 perf-agent.mjs 完全等价,仅把全局量改为 ctx 注入。
- **commit**:每个 Task 末尾 commit,message 用中文。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `tools/qalab-runner/perf-collect.mjs` | perf 采集核心:队列轮询编排 + perfdog 交互 + session 读写 + upload | 新增 |
| `tools/qalab-runner/perf-collect.test.mjs` | perf-collect 纯函数/编排单测 | 新增 |
| `tools/qalab-runner/runner.mjs` | 主循环加 handlePerf + 配置 + upload 子命令 | 改 |
| `tools/qalab-runner/.env.example` | 补 PERFDOG_DIR / REPORT_SET_ID | 改 |
| `tools/qalab-runner/DEPLOY.md` | 增补"性能采集"章节 | 改 |
| `pack-runner.bat` | 打包 qalab-runner + nami-perfdog → qalab-runner.zip | 新增 |
| `pack-agent.bat` | 旧 perf-agent 打包 | 删 |
| `tools/perf-agent/` | 旧 perf 执行机 | 删 |
| `frontend/public/perf-agent.zip` | 旧下载包 | 删(由 qalab-runner.zip 替代) |
| `start-all.bat` | 若拉起 perf-agent 则改为只拉 qalab-runner | 改 |

---

### Task 1: perf-collect 纯函数(decimate / ndjson / session 读取)

**Files:**
- Create: `tools/qalab-runner/perf-collect.mjs`
- Test: `tools/qalab-runner/perf-collect.test.mjs`

**Interfaces:**
- Produces:
  - `decimate(samples, keep = 2000) -> Array`(按 `metric` 分组等距抽稀,每组 ≤ keep,输出按 `t` 升序)
  - `ndjson(path) -> Array`(逐行 JSON.parse,跳过空行/坏行;文件不存在返回 `[]`)
  - `listSessionDirs(sessionsDir) -> string[]`(sessionsDir 下的子目录绝对路径;不存在返回 `[]`)
  - `readSession(dir) -> {dir, meta, samples, events} | null`(读 meta.json/samples.ndjson/events.ndjson;无 meta.json 返回 null;samples 经 decimate)

- [ ] **Step 1: 写失败测试**

`tools/qalab-runner/perf-collect.test.mjs`:
```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { decimate } from "./perf-collect.mjs";

test("decimate 不足 keep 全保留、按 t 升序", () => {
  const s = [
    { metric: "cpu", t: 3, v: 1 },
    { metric: "cpu", t: 1, v: 2 },
    { metric: "mem", t: 2, v: 3 },
  ];
  const out = decimate(s, 2000);
  assert.equal(out.length, 3);
  assert.deepEqual(out.map((x) => x.t), [1, 2, 3]);
});

test("decimate 超过 keep 每组抽到 keep 点", () => {
  const s = [];
  for (let i = 0; i < 5000; i++) s.push({ metric: "cpu", t: i, v: i });
  for (let i = 0; i < 100; i++) s.push({ metric: "fps", t: i, v: i });
  const out = decimate(s, 2000);
  assert.equal(out.filter((x) => x.metric === "cpu").length, 2000);
  assert.equal(out.filter((x) => x.metric === "fps").length, 100);
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd tools/qalab-runner && node --test perf-collect.test.mjs`
Expected: FAIL(`Cannot find module './perf-collect.mjs'` 或 `decimate is not a function`)

- [ ] **Step 3: 实现纯函数**

`tools/qalab-runner/perf-collect.mjs`(迁移自 perf-agent.mjs 第 64-97,导出;不含配置/网络):
```javascript
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

export const ndjson = (p) => (existsSync(p)
  ? readFileSync(p, "utf8").split(/\r?\n/).filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
  : []);

export function decimate(samples, keep = 2000) {
  const byMetric = new Map();
  for (const s of samples) {
    if (!byMetric.has(s.metric)) byMetric.set(s.metric, []);
    byMetric.get(s.metric).push(s);
  }
  const out = [];
  for (const arr of byMetric.values()) {
    if (arr.length <= keep) { out.push(...arr); continue; }
    const step = arr.length / keep;
    for (let i = 0; i < keep; i++) out.push(arr[Math.floor(i * step)]);
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

export function listSessionDirs(sessionsDir) {
  if (!existsSync(sessionsDir)) return [];
  return readdirSync(sessionsDir).map((d) => join(sessionsDir, d))
    .filter((p) => { try { return statSync(p).isDirectory(); } catch { return false; } });
}

export function readSession(dir) {
  const metaPath = join(dir, "meta.json");
  if (!existsSync(metaPath)) return null;
  const meta = JSON.parse(readFileSync(metaPath, "utf8"));
  return { dir, meta, samples: decimate(ndjson(join(dir, "samples.ndjson"))), events: ndjson(join(dir, "events.ndjson")) };
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd tools/qalab-runner && node --test perf-collect.test.mjs`
Expected: PASS(2 tests)

- [ ] **Step 5: commit**

```bash
git add tools/qalab-runner/perf-collect.mjs tools/qalab-runner/perf-collect.test.mjs
git commit -m "feat(runner): perf-collect 纯函数(抽稀/ndjson/session 读取)"
```

---

### Task 2: perf-collect 采集编排(runPerfdog / pollPerfOnce / uploadLocalSessions)

**Files:**
- Modify: `tools/qalab-runner/perf-collect.mjs`
- Test: `tools/qalab-runner/perf-collect.test.mjs`

**Interfaces:**
- Consumes(注入的 ctx):`{ api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID }`;`api(method, path, body) -> data`(已解包)。
- Produces:
  - `runPerfdog(ctx, args, beforeDirs, runId, interactive) -> Promise<{code, dir}>`(spawn `node nami-perfdog.mjs run ...`,交互模式管道 stdin/stdout ↔ 平台 prompt/signal;返回新增 session 目录)
  - `pollPerfOnce(ctx, { runPerfdog } = {}) -> Promise<void>`(拉 `/api/perf/queue` → claim → 采集 → 回传;`runPerfdog` 可注入用于测试,默认用模块内实现)
  - `uploadLocalSessions(ctx, target) -> Promise<void>`(本地 session 直传,原 cmdUpload)

- [ ] **Step 1: 写 pollPerfOnce 编排测试(桩 api + 桩 runPerfdog,不真跑 perfdog)**

追加到 `tools/qalab-runner/perf-collect.test.mjs`:
```javascript
import { pollPerfOnce } from "./perf-collect.mjs";

function makeCtx(routes, calls) {
  const api = async (method, path, body) => {
    calls.push(`${method} ${path}`);
    const key = `${method} ${path.split("?")[0]}`;
    const r = routes[key];
    return typeof r === "function" ? r(body) : r;
  };
  return { api, log: () => {}, RUNNER_ID: "win-01", PERFDOG_DIR: ".", SESSIONS_DIR: "./sessions", REPORT_SET_ID: null };
}

test("pollPerfOnce 空队列直接返回、不认领", async () => {
  const calls = [];
  const ctx = makeCtx({ "GET /api/perf/queue": [] }, calls);
  await pollPerfOnce(ctx, { runPerfdog: async () => ({ code: 0, dir: null }) });
  assert.deepEqual(calls, ["GET /api/perf/queue?runner=win-01&limit=5"]);
});

test("pollPerfOnce 认领长监控→采集→回传", async () => {
  const calls = [];
  const routes = {
    "GET /api/perf/queue": [{ run_id: 7, scenario: "长监控", variant: "v1", duration: "40s" }],
    "POST /api/perf/queue/7/claim": {},
    "PATCH /api/perf/queue/7/prompt": { status: "running", signal_seq: 0 },
    "PATCH /api/perf/queue/7": {},
  };
  const ctx = makeCtx(routes, calls);
  const stubRun = async () => ({ code: 0, dir: null }); // dir=null → 走 readSession 兜底,测编排到回传前
  // 桩 readSession:长监控回传路径。用 meta 直接构造。
  await pollPerfOnce({ ...ctx, _readSession: () => ({ meta: { outcome: "ok" }, samples: [], events: [] }) }, { runPerfdog: stubRun });
  assert.ok(calls.includes("POST /api/perf/queue/7/claim?runner=win-01"));
  assert.ok(calls.some((c) => c.startsWith("PATCH /api/perf/queue/7?runner=win-01")));
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd tools/qalab-runner && node --test perf-collect.test.mjs`
Expected: FAIL(`pollPerfOnce is not a function`)

- [ ] **Step 3: 迁移实现(从 perf-agent.mjs 第 99-230 逐行迁移 + 注入改造)**

在 `perf-collect.mjs` 追加。迁移映射:全局 `RUNNER_ID`→`ctx.RUNNER_ID`、`api(...)`→`ctx.api(...)`、`PERFDOG_DIR`→`ctx.PERFDOG_DIR`、`SESSIONS_DIR`→`ctx.SESSIONS_DIR`、`REPORT_SET_ID`→`ctx.REPORT_SET_ID`、`log`→`ctx.log`、`listSessionDirs()`→`listSessionDirs(ctx.SESSIONS_DIR)`、`readSession`→可被 `ctx._readSession` 覆盖(默认用模块 readSession)。`enc = encodeURIComponent`。

- `runPerfdog(ctx, args, beforeDirs, runId, interactive)`:逐行迁移 perf-agent.mjs 第 102-155(spawn cwd 用 `ctx.PERFDOG_DIR`;交互轮询里的 `api`/`RUNNER_ID` 换 `ctx.`;`listSessionDirs()` → `listSessionDirs(ctx.SESSIONS_DIR)`)。
- `pollPerfOnce(ctx, { runPerfdog: injectedRun } = {})`:迁移 perf-agent.mjs 第 158-193。开头 `const runFn = injectedRun || runPerfdog;` `const readFn = ctx._readSession || readSession;`;拉队列/claim/回传全部 `ctx.api`;`const before = new Set(listSessionDirs(ctx.SESSIONS_DIR));`;采集调 `runFn(ctx, runArgs, before, job.run_id, interactive)`;回传前 `const sess = dir ? readFn(dir) : null;`。
- `uploadLocalSessions(ctx, target)`:迁移 perf-agent.mjs 第 196-230(`SESSIONS_DIR`→`ctx.SESSIONS_DIR`、`REPORT_SET_ID`→`ctx.REPORT_SET_ID`、`api`→`ctx.api`、`RUNNER_ID`→`ctx.RUNNER_ID`、`log`→`ctx.log`)。

关键骨架(pollPerfOnce):
```javascript
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
const enc = encodeURIComponent;

export function runPerfdog(ctx, args, beforeDirs, runId, interactive) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", ["nami-perfdog.mjs", "run", ...args], {
      cwd: ctx.PERFDOG_DIR, stdio: interactive ? ["pipe", "pipe", "inherit"] : "inherit",
    });
    child.on("error", reject);
    let pump = null;
    if (interactive) {
      let lastSeq = 0, curPrompt = null, buf = "";
      const isPrompt = (line) => /[▶⏹]/.test(line) || /回车|按\s*Enter/i.test(line);
      child.stdout.on("data", (d) => {
        const text = d.toString(); process.stdout.write(text); buf += text;
        const lines = buf.split(/\r?\n/); buf = lines.pop();
        for (const ln of [...lines, buf]) {
          const s = ln.trim();
          if (s && isPrompt(s) && s !== curPrompt) {
            curPrompt = s;
            ctx.api("PATCH", `/api/perf/queue/${runId}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: s }).catch(() => {});
          }
        }
      });
      pump = setInterval(async () => {
        try {
          const d = await ctx.api("PATCH", `/api/perf/queue/${runId}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: curPrompt });
          if (d.status === "canceled") { try { child.stdin.end(); } catch {} child.kill(); return; }
          if ((d.signal_seq || 0) > lastSeq) { lastSeq = d.signal_seq; curPrompt = null; try { child.stdin.write("\n"); } catch {} }
        } catch { /* 下轮再试 */ }
      }, 1500);
    }
    child.on("exit", (code) => {
      if (pump) clearInterval(pump);
      const after = listSessionDirs(ctx.SESSIONS_DIR);
      const fresh = after.filter((d) => !beforeDirs.has(d));
      const pool = fresh.length ? fresh : after;
      const pick = pool.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
      resolve({ code, dir: pick });
    });
  });
}

export async function pollPerfOnce(ctx, { runPerfdog: injectedRun } = {}) {
  const runFn = injectedRun || runPerfdog;
  const readFn = ctx._readSession || readSession;
  const jobs = await ctx.api("GET", `/api/perf/queue?runner=${enc(ctx.RUNNER_ID)}&limit=5`);
  if (!jobs.length) return;
  for (const job of jobs) {
    ctx.log(`认领 #${job.run_id} ${job.scenario}/${job.variant}${job.duration ? " duration=" + job.duration : ""}`);
    await ctx.api("POST", `/api/perf/queue/${job.run_id}/claim?runner=${enc(ctx.RUNNER_ID)}`);
    const runArgs = ["--scenario", job.scenario, "--variant", job.variant];
    if (job.scenario === "长监控" && job.duration) {
      let dur = String(job.duration); if (/^\d+$/.test(dur)) dur += "s";
      runArgs.push("--duration", dur);
    }
    if (job.proc) runArgs.push("--proc", job.proc);
    const interactive = job.scenario !== "长监控";
    if (interactive) ctx.log(`  ⏳ 交互场景「${job.scenario}」:请在平台「采集控制」页按提示操作并点【继续】`);
    const before = new Set(listSessionDirs(ctx.SESSIONS_DIR));
    try {
      const { dir } = await runFn(ctx, runArgs, before, job.run_id, interactive);
      const check = await ctx.api("PATCH", `/api/perf/queue/${job.run_id}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: null }).catch(() => ({ status: null }));
      if (check.status === "canceled") { ctx.log(`采集 #${job.run_id} 已取消,跳过回传`); continue; }
      const sess = dir ? readFn(dir) : null;
      if (!sess) throw new Error("未找到采集产物 session");
      await ctx.api("PATCH", `/api/perf/queue/${job.run_id}?runner=${enc(ctx.RUNNER_ID)}`, {
        outcome: sess.meta.outcome, meta: sess.meta, samples: sess.samples, events: sess.events,
      });
      ctx.log(`回传 #${job.run_id} ✓(${sess.samples.length} samples)`);
    } catch (e) {
      ctx.log(`执行 #${job.run_id} 失败:${e.message}`);
      await ctx.api("PATCH", `/api/perf/queue/${job.run_id}?runner=${enc(ctx.RUNNER_ID)}`, { outcome: "failed", error: e.message });
    }
  }
}

export async function uploadLocalSessions(ctx, target) {
  let dirs;
  if (target && target !== "--all") {
    dirs = [target.includes("/") || target.includes("\\") ? target : join(ctx.SESSIONS_DIR, target)];
  } else {
    dirs = listSessionDirs(ctx.SESSIONS_DIR);
    if (target !== "--all") dirs = dirs.filter((d) => !existsSync(join(d, ".uploaded")));
  }
  if (!dirs.length) { ctx.log("无待上传 session"); return; }
  let ok = 0;
  for (const dir of dirs) {
    const sess = readSession(dir);
    if (!sess) { ctx.log("跳过(无 meta.json):", dir); continue; }
    try {
      const data = await ctx.api("POST", `/api/perf/queue/upload?runner=${enc(ctx.RUNNER_ID)}`, {
        runner: ctx.RUNNER_ID, report_set_id: ctx.REPORT_SET_ID,
        scenario: sess.meta.scenario, variant: sess.meta.variant, proc: sess.meta.proc || null,
        duration: sess.meta.duration || null, outcome: sess.meta.outcome,
        meta: sess.meta, samples: sess.samples, events: sess.events,
      });
      writeFileSync(join(dir, ".uploaded"), String(data.id)); ok++;
      ctx.log(`upload ✓ ${sess.meta.scenario}/${sess.meta.variant} → run#${data.id}`);
    } catch (e) { ctx.log(`upload ✗ ${dir}:${e.message}`); }
  }
  ctx.log(`完成 ${ok}/${dirs.length}`);
}
```
> 注:`statSync` 已在 Task 1 的 import 中引入;`spawn`/`writeFileSync` 在本 Task 追加 import。

- [ ] **Step 4: 跑测试验证通过**

Run: `cd tools/qalab-runner && node --test perf-collect.test.mjs`
Expected: PASS(4 tests)

- [ ] **Step 5: 语法自检**

Run: `cd tools/qalab-runner && node --check perf-collect.mjs`
Expected: 无输出(语法 OK)

- [ ] **Step 6: commit**

```bash
git add tools/qalab-runner/perf-collect.mjs tools/qalab-runner/perf-collect.test.mjs
git commit -m "feat(runner): perf-collect 采集编排(轮询/perfdog 交互/upload)"
```

---

### Task 3: runner.mjs 集成 handlePerf + 配置

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`(配置区 40-54;import 区 14-16;main() 500-505)

**Interfaces:**
- Consumes: `perf-collect.mjs` 的 `pollPerfOnce(ctx)`;runner 现有 `api`、`log`、`RUNNER_ID`。
- Produces: `handlePerf()`(供 main 循环调用);配置 `PERFDOG_DIR`/`SESSIONS_DIR`/`REPORT_SET_ID`。

- [ ] **Step 1: 加 import 与配置**

`runner.mjs` import 区(第 16 行后)追加:
```javascript
import { pollPerfOnce, uploadLocalSessions } from "./perf-collect.mjs";
import { existsSync } from "node:fs";
```
配置区(第 54 行 `DRY` 后)追加:
```javascript
// perf 采集引擎目录:分发包内 nami-perfdog 与 runner 同目录 → 优先自身;开发环境回落源码路径。
const __rdir = dirname(fileURLToPath(import.meta.url));
const PERFDOG_DIR  = process.env.PERFDOG_DIR || (existsSync(join(__rdir, "nami-perfdog.mjs")) ? __rdir : "D:/git/test/nami-perfdog");
const SESSIONS_DIR = join(PERFDOG_DIR, "sessions");
const REPORT_SET_ID = process.env.REPORT_SET_ID ? Number(process.env.REPORT_SET_ID) : null;
```
> `dirname`/`fileURLToPath`/`join` 已在 runner.mjs 顶部 import(第 12-13)。

- [ ] **Step 2: 加 handlePerf() 并接入主循环**

在 `handleProbes` 定义之后追加:
```javascript
// perf 采集:与 exec/probe 并列的第三条队列(独立 try,异常不影响其他轮询)。
async function handlePerf() {
  await pollPerfOnce({ api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID });
}
```
`main()` 循环(第 503 `handleProbes` 之后、`sleep` 之前)加:
```javascript
    try { await handlePerf(); } catch (e) { log("perf 轮询异常:", e.message); }
```
`main()` 启动日志(第 498 后)加一行:
```javascript
  log(`perf 采集就绪 perfdog=${PERFDOG_DIR}`);
```

- [ ] **Step 3: 语法自检**

Run: `cd tools/qalab-runner && node --check runner.mjs`
Expected: 无输出

- [ ] **Step 4: 启动冒烟(桩后端,验证 perf 轮询已接入且不崩)**

Run(临时桩,验证三队列都轮询、无异常退出):
```bash
cd tools/qalab-runner
BASE_URL=http://127.0.0.1:1 RUNNER_TOKEN=x RUNNER_ID=win-01 POLL_MS=1000 timeout 3 node runner.mjs --dry 2>&1 | head -20
```
Expected: 日志出现 `perf 采集就绪 perfdog=...`;出现 `perf 轮询异常`(连不上桩地址属正常)但进程不崩、持续轮询到 timeout 结束。

- [ ] **Step 5: commit**

```bash
git add tools/qalab-runner/runner.mjs
git commit -m "feat(runner): 主循环接入 perf 采集(handlePerf 第三队列)"
```

---

### Task 4: runner.mjs 入口加 upload 子命令

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`(文件末尾入口:`main()` 调用处)

**Interfaces:**
- Consumes: `uploadLocalSessions(ctx, target)`。
- Produces: CLI `node runner.mjs upload [目录|--all]`。

- [ ] **Step 1: 读末尾入口,改为子命令分发**

读 `runner.mjs` 末尾(第 508 行后的 `main()` 调用)。将末尾入口改为:
```javascript
// 入口:upload 子命令直传本地 session;否则常驻三队列轮询。
const _argv = process.argv.slice(2).filter((a) => !a.startsWith("--"));
if (_argv[0] === "upload") {
  const ctx = { api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID };
  uploadLocalSessions(ctx, _argv[1]).then(() => process.exit(0)).catch((e) => { log("upload 失败:", e.message); process.exit(1); });
} else {
  main();
}
```
> 若原文件是顶层 `await main()`,改为上述 if/else(main() 内已是无限循环,无需 await)。

- [ ] **Step 2: 语法自检**

Run: `cd tools/qalab-runner && node --check runner.mjs`
Expected: 无输出

- [ ] **Step 3: upload 空目录冒烟**

Run:
```bash
cd tools/qalab-runner
PERFDOG_DIR=/tmp/no-such-dir RUNNER_TOKEN=x node runner.mjs upload 2>&1 | head -5
```
Expected: 打印 `无待上传 session` 并退出码 0(不轮询、不连后端)。

- [ ] **Step 4: commit**

```bash
git add tools/qalab-runner/runner.mjs
git commit -m "feat(runner): 入口加 upload 子命令(本地 session 直传)"
```

---

### Task 5: 退役 perf-agent + 分发迁移

**Files:**
- Delete: `tools/perf-agent/`(整目录)、`pack-agent.bat`、`frontend/public/perf-agent.zip`
- Create: `pack-runner.bat`
- Modify: `tools/qalab-runner/.env.example`、`tools/qalab-runner/DEPLOY.md`、`start-all.bat`

- [ ] **Step 1: .env.example 补 perf 配置**

`tools/qalab-runner/.env.example` 末尾追加:
```
# 性能采集引擎目录(留空=用 runner 自身目录下的 nami-perfdog;分发包已内置)
PERFDOG_DIR=
# upload 直传时归入的报告集 id(可选)
REPORT_SET_ID=
```

- [ ] **Step 2: 新建 pack-runner.bat**

`pack-runner.bat`(UTF-8 无 BOM + CRLF;结构照 pack-agent.bat):
```bat
@echo off
setlocal
title Pack qalab-runner
set "ROOT=%~dp0"
set "RUNNER=%ROOT%tools\qalab-runner"
set "PERFDOG=D:\git\test\nami-perfdog"
set "STAGE=%TEMP%\qalab-runner-pack\qalab-runner"
set "OUT=%ROOT%frontend\public\qalab-runner.zip"

echo [1/4] Cleaning staging...
if exist "%TEMP%\qalab-runner-pack" rmdir /s /q "%TEMP%\qalab-runner-pack"
mkdir "%STAGE%"

echo [2/4] Copying runner (exclude .env / node_modules)...
robocopy "%RUNNER%" "%STAGE%" /E /XD node_modules .git /XF .env >nul

echo [3/4] Copying perfdog collector...
copy /y "%PERFDOG%\nami-perfdog.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\report-logic.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\纳米性能测试.bat"  "%STAGE%\" >nul 2>nul
if not exist "%STAGE%\vendor" mkdir "%STAGE%\vendor"
copy /y "%PERFDOG%\vendor\*"         "%STAGE%\vendor\" >nul 2>nul

echo [4/4] Zipping...
if exist "%OUT%" del /q "%OUT%"
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP%\qalab-runner-pack\qalab-runner' -DestinationPath '%OUT%' -Force"
if exist "%OUT%" ( echo Done. Bundle: %OUT% ) else ( echo FAILED - zip not produced. )
pause
```

- [ ] **Step 3: 删除 perf-agent 与旧打包/旧包**

```bash
git rm -r tools/perf-agent
git rm pack-agent.bat
git rm frontend/public/perf-agent.zip
```

- [ ] **Step 4: start-all.bat 去掉 perf-agent 窗口**

读 `start-all.bat`;若其中有启动 `tools\perf-agent`(如 `node perf-agent.mjs`)的窗口,删除该段(qalab-runner 已接管 perf)。若 start-all.bat 不涉及 perf-agent,跳过本步。

- [ ] **Step 5: DEPLOY.md 增补性能采集章节**

`tools/qalab-runner/DEPLOY.md` 末尾追加一节(要点):
```markdown
## 六、性能采集(perf)

runner 已内置性能采集,与用例执行共用同一进程/配置/设备 token。

- **前置**:被测应用在本机运行;`nami-perfdog` 已随分发包内置(`PERFDOG_DIR` 留空即用自身目录)。
- **长监控**(无人值守):平台「性能测试→任务下发」选「长监控」+ 时长,runner 自动认领采集回传。
- **交互场景**(冷启动/对话/热启动/杀进程/首次安装):下发后到平台「性能测试→采集控制」页,按提示操作应用并点【继续】,采完自动回传。
- **本地补传**:`node runner.mjs upload [目录|--all]` 直传本地已采集 session。
- 采集期间该 runner 不认领用例/探测任务(一台机同一时刻只做一件事)。
```

- [ ] **Step 6: 产物验证(端到端打包)**

Run(在 Windows cmd 双击或):`pack-runner.bat`
Expected: 产出 `frontend/public/qalab-runner.zip`。解压到临时目录后:
```bash
cd <解压目录>/qalab-runner && node --check runner.mjs && ls nami-perfdog.mjs
```
Expected: 语法 OK + `nami-perfdog.mjs` 存在(PERFDOG_DIR 默认自身目录生效)。

- [ ] **Step 7: commit**

```bash
git add -A
git commit -m "chore(runner): perf-agent 退役,分发迁移到 qalab-runner.zip"
```

- [ ] **Step 8: 工具广场下载迁移(运营步骤,非代码)**

工具广场的下载卡片是数据库登记数据(无代码引用 perf-agent.zip)。以平台管理员登录 → 「工具广场管理」→ 找到「性能测试执行机 agent」工具 → 编辑:名称/描述改为 qalab-runner 统一执行机,下载链接由 `/perf-agent.zip` 改为 `/qalab-runner.zip`。(此步在部署新 dist 后操作;记录于交付说明。)

---

### Task 6: 端到端验收(手动,需被测机 + perfdog + 纳米 Work)

**Files:** 无(纯验证)

- [ ] **Step 1: 重建前端并部署**,确保 `qalab-runner.zip` 进入 dist(`cd frontend && npm run build`,确认 `dist/qalab-runner.zip` 存在)。
- [ ] **Step 2: 配置并启动 runner**:被测机解压 qalab-runner.zip,`.env` 填 `BASE_URL=https://qalab.claw.qihoo.net` + 设备 token + RUNNER_ID;`node runner.mjs` 看到 `perf 采集就绪`。
- [ ] **Step 3: 长监控端到端**:平台下发长监控 40s → runner 认领采集回传 → 「性能报告」出现曲线。
- [ ] **Step 4: 交互端到端**:下发冷启动 → 「采集控制」页出现提示、点【继续】推进 → completed → 报告可见。
- [ ] **Step 5: 回归验证**:下发一条 GUI 用例 → runner 仍能认领执行回写(exec 未回归);触发一次探测 → probe 未回归。
- [ ] **Step 6: upload**:本机 `node runner.mjs upload` 将一条本地 session 直传成功。

---

## 自查

**Spec coverage**:§3 决策表 5 项 → 全部场景(Task 2 长监控+交互)、perf-agent 退役(Task 5)、串行阻塞(Task 3 main 串行)、共享模块(Task 1/2)、分发 zip+clone(Task 5);§5 组件 5 项 → 全部有对应 Task;§6 数据流 → Task 2 pollPerfOnce/runPerfdog;§7 错误/并发 → Task 3 独立 try + Task 2 failed/canceled;§8 验收 6 条 → Task 6 覆盖。无遗漏。

**Placeholder scan**:无 TBD/TODO;测试步骤含真实代码;迁移步骤给出确切源行号 + 全局→ctx 映射表 + 关键骨架(非"similar to")。

**Type consistency**:`ctx = { api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID }` 在 Task 2/3/4 一致;`pollPerfOnce(ctx, {runPerfdog})`、`uploadLocalSessions(ctx, target)`、`runPerfdog(ctx, args, beforeDirs, runId, interactive)`、`readSession(dir)`、`listSessionDirs(sessionsDir)`、`decimate(samples, keep)` 全程签名一致;`api(method, path, body) -> data` 约定一致。
