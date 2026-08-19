#!/usr/bin/env node
/**
 * perf-agent —— 平台性能任务执行机代理（类比 tools/qalab-runner/runner.mjs）。
 * 纯 Node（v18+ 内置 fetch），零依赖。
 *
 * 用法：
 *   node perf-agent.mjs              轮询平台队列，自动执行可无人值守的场景（长监控带 --duration）
 *   node perf-agent.mjs poll-once    只轮询一轮就退出（便于 Windows 计划任务定时调度/联调）
 *   node perf-agent.mjs upload [目录|--all]
 *                                    把本地已采集的 session 直传平台（冷启动/对话等交互场景用）
 *                                    省略目录=传 sessions 下所有"未打过 .uploaded 标记"的；
 *                                    --all=不管标记全传；给具体目录=只传那一个。
 *
 * 配置（同目录 .env 或环境变量）：BASE_URL / RUNNER_TOKEN / RUNNER_ID / PERFDOG_DIR / POLL_MS
 *
 * 设计要点：
 * - dispatch 轨执行所有下发场景：长监控带 --duration 无人值守；交互场景（冷启动/对话/热启动/
 *   杀进程/首次安装）由 perfdog 自带的回车引导，需在本窗口按提示操作应用并回车，采完自动回传。
 * - 回传前按 metric 抽稀 samples（每指标≤2000 点），控制体积。
 * - runner 鉴权用 RUNNER_TOKEN（共享）或"我的设备"专属 token，与用户 JWT 分离。
 * - 交互场景要求 agent 跑在真实终端窗口（run.cmd / start-all.bat），因为 perfdog 需真实 stdin。
 */
import { spawn } from 'node:child_process';
import { readFileSync, readdirSync, statSync, existsSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));

// 极简 .env 载入（不覆盖已存在的环境变量）
try {
  const envPath = join(__dir, '.env');
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, 'utf8').split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
} catch { /* 无 .env 用默认/环境变量 */ }

// ---- 配置 ----
const BASE_URL = (process.env.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const RUNNER_TOKEN = process.env.RUNNER_TOKEN || 'perf-local-dev-token';
const RUNNER_ID = process.env.RUNNER_ID || 'win-01';
// 打包分发后 perfdog 与 agent 同目录 → 优先用自身目录；开发环境(agent 单独在 tools/)回落到源码路径。
const PERFDOG_DIR = process.env.PERFDOG_DIR || (existsSync(join(__dir, 'nami-perfdog.mjs')) ? __dir : 'D:/git/test/nami-perfdog');
const SESSIONS_DIR = join(PERFDOG_DIR, 'sessions');
const POLL_MS = Number(process.env.POLL_MS || 5000);
const REPORT_SET_ID = process.env.REPORT_SET_ID ? Number(process.env.REPORT_SET_ID) : null;  // upload 时归入的报告集(可选)

const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${RUNNER_TOKEN}` };
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const enc = encodeURIComponent;

async function api(method, path, body) {
  const res = await fetch(`${BASE_URL}${path}`, { method, headers: H, body: body ? JSON.stringify(body) : undefined });
  const txt = await res.text();
  let json;
  try { json = JSON.parse(txt); } catch { json = { code: -1, msg: txt }; }
  if (!res.ok || json.code !== 0) throw new Error(`${method} ${path} → ${res.status} ${json.msg || txt}`);
  return json.data;
}

const ndjson = (p) => (existsSync(p)
  ? readFileSync(p, 'utf8').split(/\r?\n/).filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
  : []);

// 按 metric 分组抽稀，每组最多 keep 点（等距），回传前压体积。
function decimate(samples, keep = 2000) {
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

function listSessionDirs() {
  if (!existsSync(SESSIONS_DIR)) return [];
  return readdirSync(SESSIONS_DIR)
    .map((d) => join(SESSIONS_DIR, d))
    .filter((p) => { try { return statSync(p).isDirectory(); } catch { return false; } });
}

function readSession(dir) {
  const metaPath = join(dir, 'meta.json');
  if (!existsSync(metaPath)) return null;
  const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
  return { dir, meta, samples: decimate(ndjson(join(dir, 'samples.ndjson'))), events: ndjson(join(dir, 'events.ndjson')) };
}

// 跑一次 perfdog run，返回新增（或最新）session 目录。beforeDirs 由调用方在 spawn 前算好。
// interactive=true（冷启动/对话等）：stdin/stdout 接管道，把 perfdog 的回车提示搬到平台——
//   捕获 stdout 的提示行上报，轮询平台 signal_seq 到点就往 stdin 写 \n；平台 canceled 则 kill。
function runPerfdog(args, beforeDirs, runId, interactive) {
  return new Promise((resolve, reject) => {
    const child = spawn('node', ['nami-perfdog.mjs', 'run', ...args], {
      cwd: PERFDOG_DIR,
      stdio: interactive ? ['pipe', 'pipe', 'inherit'] : 'inherit',
    });
    child.on('error', reject);

    let pump = null;
    if (interactive) {
      let lastSeq = 0;          // 已消费到的平台信号序号
      let curPrompt = null;     // 当前已上报的提示（去重）
      let buf = '';
      // 只把 perfdog 的“等回车”提示行搬到平台：▶ 开始 / ⏹ 结束 / 含“回车”的行。
      const isPrompt = (line) => /[▶⏹]/.test(line) || /回车|按\s*Enter/i.test(line);
      // perfdog 的提示是用 readline question 打印的，可能不带换行；stdout 到达即扫描。
      child.stdout.on('data', (d) => {
        const text = d.toString();
        process.stdout.write(text);   // 仍回显到 agent 窗口
        buf += text;
        const lines = buf.split(/\r?\n/);
        buf = lines.pop();            // 末段可能是无换行的 question 提示，连同下轮再判
        for (const ln of [...lines, buf]) {
          const s = ln.trim();
          if (s && isPrompt(s) && s !== curPrompt) {
            curPrompt = s;
            api('PATCH', `/api/perf/queue/${runId}/prompt?runner=${enc(RUNNER_ID)}`, { prompt: s }).catch(() => {});
          }
        }
      });
      // 每 1.5s 问平台：有没有点“继续”(seq 变大→写回车)、有没有取消(→kill)
      pump = setInterval(async () => {
        try {
          const d = await api('PATCH', `/api/perf/queue/${runId}/prompt?runner=${enc(RUNNER_ID)}`, { prompt: curPrompt });
          if (d.status === 'canceled') { try { child.stdin.end(); } catch {} child.kill(); return; }
          if ((d.signal_seq || 0) > lastSeq) {
            lastSeq = d.signal_seq;
            curPrompt = null;                 // 推进后清本地提示，等 perfdog 打下一条
            try { child.stdin.write('\n'); } catch {}
          }
        } catch { /* 轮询失败下轮再试 */ }
      }, 1500);
    }

    child.on('exit', (code) => {
      if (pump) clearInterval(pump);
      const after = listSessionDirs();
      const fresh = after.filter((d) => !beforeDirs.has(d));
      const pool = fresh.length ? fresh : after;
      const pick = pool.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
      resolve({ code, dir: pick });
    });
  });
}

// ---- 轮询执行 dispatch 任务 ----
async function pollOnce() {
  const jobs = await api('GET', `/api/perf/queue?runner=${enc(RUNNER_ID)}&limit=5`);
  if (!jobs.length) return;
  for (const job of jobs) {
    log(`认领 #${job.run_id} ${job.scenario}/${job.variant}${job.duration ? ' duration=' + job.duration : ''}`);
    await api('POST', `/api/perf/queue/${job.run_id}/claim?runner=${enc(RUNNER_ID)}`);
    const runArgs = ['--scenario', job.scenario, '--variant', job.variant];
    if (job.scenario === '长监控' && job.duration) {
      let dur = String(job.duration);
      if (/^\d+$/.test(dur)) dur += 's';   // 纯数字按秒（perfdog 对无单位数字解析为 0s）
      runArgs.push('--duration', dur);
    }
    if (job.proc) runArgs.push('--proc', job.proc);
    const interactive = job.scenario !== '长监控';
    // 交互场景（冷启动/对话/热启动/杀进程/首次安装）：提示与回车都搬到平台采集控制页。
    if (interactive) {
      log(`  ⏳ 交互场景「${job.scenario}」：请在平台「采集控制」页按提示操作应用并点【继续】`);
    }
    const before = new Set(listSessionDirs());
    try {
      const { dir } = await runPerfdog(runArgs, before, job.run_id, interactive);
      // 采集期间被平台取消：run 已是 canceled，不覆盖，跳过回传
      const check = await api('PATCH', `/api/perf/queue/${job.run_id}/prompt?runner=${enc(RUNNER_ID)}`, { prompt: null }).catch(() => ({ status: null }));
      if (check.status === 'canceled') { log(`采集 #${job.run_id} 已被取消，跳过回传`); continue; }
      const sess = dir ? readSession(dir) : null;
      if (!sess) throw new Error('未找到采集产物 session');
      await api('PATCH', `/api/perf/queue/${job.run_id}?runner=${enc(RUNNER_ID)}`, {
        outcome: sess.meta.outcome, meta: sess.meta, samples: sess.samples, events: sess.events,
      });
      log(`回传 #${job.run_id} ✓（${sess.samples.length} samples）`);
    } catch (e) {
      log(`执行 #${job.run_id} 失败：${e.message}`);
      await api('PATCH', `/api/perf/queue/${job.run_id}?runner=${enc(RUNNER_ID)}`, { outcome: 'failed', error: e.message });
    }
  }
}

// ---- upload 子命令：直传本地 session ----
async function cmdUpload(target) {
  let dirs;
  if (target && target !== '--all') {
    dirs = [target.includes('/') || target.includes('\\') ? target : join(SESSIONS_DIR, target)];
  } else {
    dirs = listSessionDirs();
    if (target !== '--all') dirs = dirs.filter((d) => !existsSync(join(d, '.uploaded')));
  }
  if (!dirs.length) { log('无待上传 session（已全部打过 .uploaded 标记，或目录为空）'); return; }
  let ok = 0;
  for (const dir of dirs) {
    const sess = readSession(dir);
    if (!sess) { log('跳过（无 meta.json）：', dir); continue; }
    try {
      const data = await api('POST', `/api/perf/queue/upload?runner=${enc(RUNNER_ID)}`, {
        runner: RUNNER_ID,
        report_set_id: REPORT_SET_ID,
        scenario: sess.meta.scenario,
        variant: sess.meta.variant,
        proc: sess.meta.proc || null,
        duration: sess.meta.duration || null,
        outcome: sess.meta.outcome,
        meta: sess.meta,
        samples: sess.samples,
        events: sess.events,
      });
      writeFileSync(join(dir, '.uploaded'), String(data.id));
      ok++;
      log(`upload ✓ ${sess.meta.scenario}/${sess.meta.variant} → run#${data.id}（${sess.samples.length} samples）`);
    } catch (e) {
      log(`upload ✗ ${dir}：${e.message}`);
    }
  }
  log(`完成 ${ok}/${dirs.length}`);
}

// ---- 入口 ----
const [cmd, arg] = process.argv.slice(2);
if (cmd === 'upload') {
  await cmdUpload(arg);
} else if (cmd === 'poll-once') {
  log(`poll-once · runner=${RUNNER_ID} · ${BASE_URL}`);
  await pollOnce();
  log('done');
} else {
  log(`perf-agent 轮询启动 · runner=${RUNNER_ID} · ${BASE_URL} · 每 ${POLL_MS}ms`);
  log(`perfdog: ${PERFDOG_DIR}`);
  for (;;) {
    try { await pollOnce(); } catch (e) { log('轮询错误：', e.message); }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}
