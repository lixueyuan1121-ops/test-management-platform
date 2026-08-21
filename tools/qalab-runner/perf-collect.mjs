import { spawn } from "node:child_process";
import { readFileSync, readdirSync, statSync, existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const enc = encodeURIComponent;

export const ndjson = (p) => (existsSync(p)
  ? readFileSync(p, "utf8").split(/\r?\n/).filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
  : []);

// 按 metric 分组抽稀，每组最多 keep 点（等距），回传前压体积。
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

// 跑一次 perfdog run，返回新增（或最新）session 目录。beforeDirs 由调用方在 spawn 前算好。
// interactive=true（冷启动/对话等）：stdin/stdout 接管道，把 perfdog 的回车提示搬到平台——
//   捕获 stdout 的提示行上报，轮询平台 signal_seq 到点就往 stdin 写 \n；平台 canceled 则 kill。
export function runPerfdog(ctx, args, beforeDirs, runId, interactive) {
  return new Promise((resolve, reject) => {
    const child = spawn("node", ["nami-perfdog.mjs", "run", ...args], {
      cwd: ctx.PERFDOG_DIR,
      stdio: interactive ? ["pipe", "pipe", "inherit"] : "inherit",
    });
    child.on("error", reject);

    let pump = null;
    if (interactive) {
      let lastSeq = 0;          // 已消费到的平台信号序号
      let curPrompt = null;     // 当前已上报的提示（去重）
      let buf = "";
      // 只把 perfdog 的“等回车”提示行搬到平台：▶ 开始 / ⏹ 结束 / 含“回车”的行。
      const isPrompt = (line) => /[▶⏹]/.test(line) || /回车|按\s*Enter/i.test(line);
      // perfdog 的提示是用 readline question 打印的，可能不带换行；stdout 到达即扫描。
      child.stdout.on("data", (d) => {
        const text = d.toString();
        process.stdout.write(text);   // 仍回显到窗口
        buf += text;
        const lines = buf.split(/\r?\n/);
        buf = lines.pop();            // 末段可能是无换行的 question 提示，连同下轮再判
        for (const ln of [...lines, buf]) {
          const s = ln.trim();
          if (s && isPrompt(s) && s !== curPrompt) {
            curPrompt = s;
            ctx.api("PATCH", `/api/perf/queue/${runId}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: s }).catch(() => {});
          }
        }
      });
      // 每 1.5s 问平台：有没有点“继续”(seq 变大→写回车)、有没有取消(→kill)
      pump = setInterval(async () => {
        try {
          const d = await ctx.api("PATCH", `/api/perf/queue/${runId}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: curPrompt });
          if (d.status === "canceled") { try { child.stdin.end(); } catch {} child.kill(); return; }
          if ((d.signal_seq || 0) > lastSeq) {
            lastSeq = d.signal_seq;
            curPrompt = null;                 // 推进后清本地提示，等 perfdog 打下一条
            try { child.stdin.write("\n"); } catch {}
          }
        } catch { /* 轮询失败下轮再试 */ }
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

// ---- 轮询执行 dispatch 任务(一轮) ----
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
      let dur = String(job.duration);
      if (/^\d+$/.test(dur)) dur += "s";   // 纯数字按秒（perfdog 对无单位数字解析为 0s）
      runArgs.push("--duration", dur);
    }
    if (job.proc) runArgs.push("--proc", job.proc);
    const interactive = job.scenario !== "长监控";
    // 交互场景（冷启动/对话/热启动/杀进程/首次安装）：提示与回车都搬到平台采集控制页。
    if (interactive) {
      ctx.log(`  ⏳ 交互场景「${job.scenario}」：请在平台「采集控制」页按提示操作应用并点【继续】`);
    }
    const before = new Set(listSessionDirs(ctx.SESSIONS_DIR));
    try {
      const { dir } = await runFn(ctx, runArgs, before, job.run_id, interactive);
      // 采集期间被平台取消：run 已是 canceled，不覆盖，跳过回传
      const check = await ctx.api("PATCH", `/api/perf/queue/${job.run_id}/prompt?runner=${enc(ctx.RUNNER_ID)}`, { prompt: null }).catch(() => ({ status: null }));
      if (check.status === "canceled") { ctx.log(`采集 #${job.run_id} 已被取消，跳过回传`); continue; }
      const sess = dir ? readFn(dir) : null;
      if (!sess) throw new Error("未找到采集产物 session");
      await ctx.api("PATCH", `/api/perf/queue/${job.run_id}?runner=${enc(ctx.RUNNER_ID)}`, {
        outcome: sess.meta.outcome, meta: sess.meta, samples: sess.samples, events: sess.events,
      });
      ctx.log(`回传 #${job.run_id} ✓（${sess.samples.length} samples）`);
    } catch (e) {
      ctx.log(`执行 #${job.run_id} 失败：${e.message}`);
      await ctx.api("PATCH", `/api/perf/queue/${job.run_id}?runner=${enc(ctx.RUNNER_ID)}`, { outcome: "failed", error: e.message });
    }
  }
}

// ---- upload：直传本地 session ----
export async function uploadLocalSessions(ctx, target) {
  let dirs;
  if (target && target !== "--all") {
    dirs = [target.includes("/") || target.includes("\\") ? target : join(ctx.SESSIONS_DIR, target)];
  } else {
    dirs = listSessionDirs(ctx.SESSIONS_DIR);
    if (target !== "--all") dirs = dirs.filter((d) => !existsSync(join(d, ".uploaded")));
  }
  if (!dirs.length) { ctx.log("无待上传 session（已全部打过 .uploaded 标记，或目录为空）"); return; }
  let ok = 0;
  for (const dir of dirs) {
    const sess = readSession(dir);
    if (!sess) { ctx.log("跳过（无 meta.json）：", dir); continue; }
    try {
      const data = await ctx.api("POST", `/api/perf/queue/upload?runner=${enc(ctx.RUNNER_ID)}`, {
        runner: ctx.RUNNER_ID,
        report_set_id: ctx.REPORT_SET_ID,
        scenario: sess.meta.scenario,
        variant: sess.meta.variant,
        proc: sess.meta.proc || null,
        duration: sess.meta.duration || null,
        outcome: sess.meta.outcome,
        meta: sess.meta,
        samples: sess.samples,
        events: sess.events,
      });
      writeFileSync(join(dir, ".uploaded"), String(data.id));
      ok++;
      ctx.log(`upload ✓ ${sess.meta.scenario}/${sess.meta.variant} → run#${data.id}（${sess.samples.length} samples）`);
    } catch (e) {
      ctx.log(`upload ✗ ${dir}：${e.message}`);
    }
  }
  ctx.log(`完成 ${ok}/${dirs.length}`);
}
