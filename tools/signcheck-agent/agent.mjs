#!/usr/bin/env node
// 签名校验机 agent —— 轮询测试管理平台，领到待校验文件后在本机跑 360Base_test.exe，把原始输出回传。
// 纯 Node(v18+ 内置 fetch)，零第三方依赖。只能跑在 Windows 上（校验程序是 32 位 Windows 程序）。
//
// 用法：
//   run.cmd                              常驻轮询（读同目录 .env）
//   node agent.mjs --selftest <文件路径>   不连平台，只在本机校验一个文件并打印输出（验证运行库/区域设置）
//
// 平台侧接口见 backend/app/api/signcheck.py：
//   GET  /api/signcheck/agent/next?agent=<id>   认领一条（无待办 data=null），同时刷新心跳
//   GET  /api/signcheck/agent/{id}/file         下载待校验文件
//   POST /api/signcheck/agent/{id}/result       回传 {exit_code, stdout_b64, stderr_b64, duration_ms, error}

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream, existsSync, readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
// 校验程序运行时要在自己目录里找这几个 DLL，缺一个就会校验出错或直接起不来
export const VERIFIER_FILES = ["360Base_test.exe", "360Base.dll", "pbusc_d.dll", "sesafe.dll", "sesafe64.dll"];
const MAX_OUTPUT_BYTES = 256 * 1024;

// 极简 .env 加载器(零依赖)：同目录 .env 的键值填入 process.env（不覆盖已有环境变量）。
export function loadDotenv(file = join(HERE, ".env")) {
  let raw;
  try { raw = readFileSync(file, "utf-8"); } catch { return; }
  if (raw.charCodeAt(0) === 0xFEFF) raw = raw.slice(1);   // 去 UTF-8 BOM(Windows 记事本常见)
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    const val = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    if (key && process.env[key] === undefined) process.env[key] = val;
  }
}

export function readConfig(env = process.env) {
  return {
    baseUrl: (env.BASE_URL || "").replace(/\/+$/, ""),
    token: env.SIGNCHECK_AGENT_TOKEN || "",
    agentId: (env.AGENT_ID || hostname() || "win-signcheck").slice(0, 64),
    pollMs: Number(env.POLL_MS || 3000),
    timeoutMs: Number(env.VERIFY_TIMEOUT_MS || 120000),
    verifierExe: resolve(HERE, env.VERIFIER_EXE || join("verifier", "360Base_test.exe")),
  };
}

const log = (...a) => console.log(new Date().toISOString(), ...a);

// 落盘文件名：保留原名（校验程序输出里会带路径，便于对照），但去掉 Windows 不接受的部分。
const RESERVED = /^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$/i;
export function safeFileName(name) {
  let n = String(name || "").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[. ]+$/, "");
  if (n.length > 120) {
    const dot = n.lastIndexOf(".");
    const ext = dot > 0 && n.length - dot <= 16 ? n.slice(dot) : "";
    n = n.slice(0, 120 - ext.length) + ext;
  }
  if (!n || RESERVED.test(n)) n = `_${n || "file"}`;
  return n;
}

export function missingVerifierFiles(verifierExe) {
  const dir = dirname(verifierExe);
  const missing = existsSync(verifierExe) ? [] : [verifierExe];
  for (const f of VERIFIER_FILES.slice(1)) if (!existsSync(join(dir, f))) missing.push(join(dir, f));
  return missing;
}

// 跑一次校验程序。结果一律 resolve：{exitCode, stdout, stderr, durationMs, error}（error 非空=没跑成）。
// prefixArgs 仅测试用（用 node 脚本冒充校验程序时放脚本路径）。
export function runVerifier(verifierExe, filePath, timeoutMs, prefixArgs = []) {
  return new Promise((done) => {
    const started = Date.now();
    const out = [], err = [];
    let outBytes = 0, errBytes = 0, timedOut = false, settled = false;
    const finish = (extra) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      done({ exitCode: null, stdout: Buffer.concat(out), stderr: Buffer.concat(err), durationMs: Date.now() - started, error: null, ...extra });
    };
    let child;
    try {
      // cwd 设为校验程序所在目录：它按相对路径加载同目录的 360Base.dll 等
      child = spawn(verifierExe, [...prefixArgs, filePath], { cwd: dirname(verifierExe), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    } catch (e) {
      return done({ exitCode: null, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0), durationMs: 0, error: `无法启动原校验工具（${e.message}）。` });
    }
    const timer = setTimeout(() => { timedOut = true; child.kill(); }, timeoutMs);
    child.stdout.on("data", (c) => { if (outBytes < MAX_OUTPUT_BYTES) { out.push(c); outBytes += c.length; } });
    child.stderr.on("data", (c) => { if (errBytes < MAX_OUTPUT_BYTES) { err.push(c); errBytes += c.length; } });
    child.on("error", (e) => finish({ error: `无法启动原校验工具（${e.message}）。请检查 verifier 目录是否完整、Visual C++ 2008 x86 运行库是否已安装。` }));
    child.on("close", (code, signal) => {
      if (timedOut) return finish({ error: `原校验工具在 ${Math.round(timeoutMs / 1000)} 秒内未完成，请重试。` });
      finish({ exitCode: code ?? null, error: code === null ? `原校验工具被异常终止（${signal || "unknown"}）。` : null });
    });
  });
}

export function createClient(cfg, fetchImpl = fetch) {
  const auth = { Authorization: `Bearer ${cfg.token}` };
  async function api(path, init = {}) {
    const res = await fetchImpl(cfg.baseUrl + path, { ...init, headers: { ...auth, ...(init.headers || {}) } });
    let body = null;
    try { body = await res.json(); } catch { /* 非 JSON（网关错误页等） */ }
    if (!res.ok || !body || body.code !== 0) {
      const e = new Error(`${init.method || "GET"} ${path} -> HTTP ${res.status} ${body?.msg || ""}`.trim());
      e.status = res.status;
      throw e;
    }
    return body.data;
  }
  return {
    next: () => api(`/api/signcheck/agent/next?agent=${encodeURIComponent(cfg.agentId)}`),
    result: (id, payload) => api(`/api/signcheck/agent/${id}/result`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }),
    // 流式下载到 dest，边写边算 sha256；返回 {size, sha256}
    async download(id, dest) {
      const res = await fetchImpl(`${cfg.baseUrl}/api/signcheck/agent/${id}/file`, { headers: auth });
      if (!res.ok || !res.body) throw new Error(`下载待校验文件失败（HTTP ${res.status}）`);
      const hash = createHash("sha256");
      let size = 0;
      const tap = new Transform({ transform(chunk, _enc, cb) { hash.update(chunk); size += chunk.length; cb(null, chunk); } });
      await pipeline(Readable.fromWeb(res.body), tap, createWriteStream(dest));
      return { size, sha256: hash.digest("hex") };
    },
  };
}

// 处理一条认领到的校验：下载 → 校验 → 回传 → 清理临时目录。
export async function handleJob(job, cfg, client) {
  const dir = await mkdtemp(join(tmpdir(), "signcheck-"));
  let payload;
  try {
    const file = join(dir, safeFileName(job.filename));
    const got = await client.download(job.id, file);
    if (job.sha256 && got.sha256 !== job.sha256) {
      payload = { error: "校验机下载到的文件与上传的不一致（sha256 不匹配），请重新上传。" };
    } else {
      const r = await runVerifier(cfg.verifierExe, file, cfg.timeoutMs, cfg.verifierArgs);
      payload = {
        exit_code: r.exitCode,
        stdout_b64: r.stdout.toString("base64"),
        stderr_b64: r.stderr.toString("base64"),
        duration_ms: r.durationMs,
        error: r.error,
      };
    }
  } catch (e) {
    payload = { error: `校验机处理失败：${e.message}` };
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
  payload.agent = cfg.agentId;
  // 回传失败重试几次：结果丢了平台侧要等 10 分钟才会把这条收口成失败
  for (let i = 1; ; i++) {
    try {
      return await client.result(job.id, payload);
    } catch (e) {
      if (e.status === 404 || e.status === 409 || i >= 5) throw e;   // 记录已删/已收口：重试无意义
      await sleep(2000 * i);
    }
  }
}

export async function pollOnce(cfg, client) {
  const job = await client.next();
  if (!job) return false;
  log(`#${job.id} 开始校验 ${job.filename}（${job.file_size} 字节）`);
  const r = await handleJob(job, cfg, client);
  log(`#${job.id} 完成 -> ${r.status}/${r.verdict}`);
  return true;
}

function decodeForConsole(buf) {
  try { return new TextDecoder("gbk").decode(buf); } catch { return buf.toString("latin1"); }
}

async function selftest(cfg, file) {
  if (!file || !existsSync(file)) { console.error("用法: node agent.mjs --selftest <文件路径>"); return 2; }
  const r = await runVerifier(cfg.verifierExe, resolve(file), cfg.timeoutMs);
  if (r.error) { console.error(r.error); return 1; }
  const code = r.exitCode < 0 || r.exitCode > 0x7fffffff ? `0x${(r.exitCode >>> 0).toString(16).padStart(8, "0")}` : r.exitCode;
  console.log(`进程退出码 ${code}，耗时 ${r.durationMs} ms\n`);
  console.log(decodeForConsole(r.stdout) || "（无输出）");
  if (r.stderr.length) console.log(`[stderr]\n${decodeForConsole(r.stderr)}`);
  return 0;
}

async function main() {
  loadDotenv();
  const cfg = readConfig();
  const missing = missingVerifierFiles(cfg.verifierExe);
  if (missing.length) {
    console.error("缺少校验程序文件，请把 Windows-SignCheck 工具包里 verifier 目录的全部文件放到位：");
    for (const m of missing) console.error("  " + m);
    process.exit(2);
  }
  const st = process.argv.indexOf("--selftest");
  if (st >= 0) process.exit(await selftest(cfg, process.argv[st + 1]));

  if (!cfg.baseUrl || !cfg.token) {
    console.error("请在同目录 .env 里配置 BASE_URL（平台地址）和 SIGNCHECK_AGENT_TOKEN（与平台后端 .env 一致）。");
    process.exit(2);
  }
  if (process.platform !== "win32") log("警告：当前不是 Windows，360Base_test.exe 无法在此运行。");
  const client = createClient(cfg);
  log(`签名校验机启动 base=${cfg.baseUrl} agent=${cfg.agentId} verifier=${cfg.verifierExe}`);
  let failures = 0;
  for (;;) {
    let busy = false;
    try {
      busy = await pollOnce(cfg, client);
      if (failures) log("与平台的连接已恢复");
      failures = 0;
    } catch (e) {
      failures += 1;
      if (e.status === 401) log("平台拒绝了令牌（401）：请核对 .env 的 SIGNCHECK_AGENT_TOKEN 与平台后端是否一致");
      else log(`轮询失败（第 ${failures} 次）：${e.message}`);
    }
    // 刚处理完一条就立刻再拉（队列里可能还有）；出错则退避，最长 30s
    if (!busy) await sleep(failures ? Math.min(30000, cfg.pollMs * 2 ** Math.min(failures, 4)) : cfg.pollMs);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
