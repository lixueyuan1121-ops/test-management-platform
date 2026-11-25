#!/usr/bin/env node
// md-submit —— 工作机上把一份提测 md 直接下发到平台「提测执行」，不用开页面上传。
// 纯 Node 18+，零依赖。不属于 runner 分发包（放在 tools/md-submit，不会被推到执行机）。
//
//   node md-submit.mjs login                     登录一次，令牌缓存 7 天（密码不落盘）
//   node md-submit.mjs <任务.md>                 按 frontmatter 下发
//   node md-submit.mjs <任务.md> --wait          下发后等执行完，打印结论与结果文档
//   node md-submit.mjs <任务.md> --dry-run       只打印将要下发的内容，不请求平台
//
// 可选参数（优先级：命令行 > md 的 frontmatter > 配置文件）：
//   --runner lili-win,lili-mac   --env test|pre|online   --project 3   --title "…"   --req REQ-1
//
// 配置文件 ~/.qalab/md-submit.json（没有就全靠参数/环境变量）：
//   { "base_url": "https://qalab.claw.qihoo.net", "project_id": 3,
//     "runners": { "win": ["lili-win"], "mac": ["lili-mac"] } }
// 环境变量：QALAB_BASE_URL；无人值守可设 QALAB_USERNAME / QALAB_PASSWORD 代替 login。
//
// 退出码：0 已下发（--wait 时=全部通过）；1 有失败用例；2 有阻塞/取消/超时；3 参数或鉴权错误。

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const HOME_DIR = path.join(os.homedir(), ".qalab");
const CONFIG_FILE = path.join(HOME_DIR, "md-submit.json");
const TOKEN_FILE = path.join(HOME_DIR, "token.json");
const ENV_ALIASES = { test: "test", "work-test": "test", pre: "pre", staging: "pre", "work-staging": "pre", online: "online", prod: "online" };
const TERMINAL = ["passed", "failed", "blocked", "canceled"];

const die = (msg, code = 3) => { console.error(`✗ ${msg}`); process.exit(code); };
const readJson = (file) => { try { return JSON.parse(fs.readFileSync(file, "utf-8")); } catch { return null; } };

// ---------- 参数 ----------
export function parseArgs(argv) {
  const flags = {}, rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { rest.push(a); continue; }
    const key = a.slice(2);
    if (["wait", "dry-run", "help"].includes(key)) flags[key] = true;
    else flags[key] = argv[++i];
  }
  return { flags, rest };
}

// ---------- frontmatter ----------
export function parseFrontmatter(text) {
  const m = text.match(/^﻿?---\r?\n([\s\S]*?)\r?\n---/);
  const meta = {};
  if (!m) return meta;
  for (const raw of m[1].split(/\r?\n/)) {
    const line = raw.trim();
    const i = line.indexOf(":");
    if (!line || line.startsWith("#") || i < 0) continue;
    meta[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/\s+#.*$/, "").replace(/^["']|["']$/g, "");
  }
  return meta;
}

const splitList = (v) => (Array.isArray(v) ? v : String(v || "").split(",")).map((x) => String(x).trim()).filter(Boolean);

/** 把命令行 / frontmatter / 配置合成最终的下发请求体。纯函数，便于测试。 */
export function buildRequest({ file, text, flags, config }) {
  const meta = parseFrontmatter(text);
  const heading = (text.match(/^#\s+(.+)$/m) || [])[1];
  const title = (flags.title || meta.title || heading || path.basename(file).replace(/\.(md|markdown|txt)$/i, "")).trim();

  const envRaw = String(flags.env || meta.env_group || config.env_group || "").trim().toLowerCase();
  if (!envRaw) throw new Error("没有指定环境：在 frontmatter 写 env_group: test|pre|online，或加 --env（线上也必须显式写 online）");
  const env_group = ENV_ALIASES[envRaw];
  if (!env_group) throw new Error(`环境只能是 test / pre / online，当前: ${envRaw}`);

  // 执行机：显式列表优先；否则按 platform 从配置里的映射取
  let runners = splitList(flags.runner || meta.runners || meta.runner);
  if (!runners.length) {
    const platform = String(meta.platform || "").toLowerCase();
    const map = config.runners || {};
    const keys = platform === "both" ? ["mac", "win"] : platform ? [platform] : [];
    runners = keys.flatMap((k) => splitList(map[k]));
    if (!runners.length) {
      throw new Error(platform
        ? `platform=${platform}，但 ${CONFIG_FILE} 的 runners 里没有对应执行机；补上映射，或用 --runner 指定`
        : "没有指定执行机：加 --runner <runner_id>，或在 frontmatter 写 platform 并在配置里配 runners 映射");
    }
  }
  runners = [...new Set(runners)];

  const project = flags.project ?? meta.project_id ?? config.project_id ?? null;
  const project_id = project === null || project === "" ? null : Number(project);
  if (project_id !== null && !Number.isInteger(project_id)) throw new Error(`project 必须是项目 id（数字），当前: ${project}`);

  return {
    project_id,
    req_no: (flags.req || meta.req || meta.req_no || "").trim() || null,
    title: title.slice(0, 255),
    env_group,
    client_version: (flags["client-version"] || meta.client_version || "").trim() || null,
    content: text,
    source: "cli",
    runners,
  };
}

// ---------- 平台调用 ----------
function makeClient(baseUrl) {
  const base = baseUrl.replace(/\/+$/, "");
  async function call(method, apiPath, body, token) {
    let res;
    try {
      res = await fetch(`${base}/api${apiPath}`, {
        method,
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(30000),
      });
    } catch (e) {
      throw new Error(`连不上平台 ${base}（${e.cause?.code || e.message}）`);
    }
    const env = await res.json().catch(() => ({}));
    if (!res.ok || (env.code !== undefined && env.code !== 0)) {
      const err = new Error(env.msg || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return env.data;
  }
  return { call };
}

function saveToken(baseUrl, data) {
  fs.mkdirSync(HOME_DIR, { recursive: true });
  fs.writeFileSync(TOKEN_FILE, JSON.stringify({ base_url: baseUrl, ...data, saved_at: Date.now() }), { mode: 0o600 });
}

/** 取一个可用的 access token：缓存 → 刷新 → 环境变量里的账号密码。都没有就让人先 login。 */
async function getToken(client, baseUrl) {
  const cached = readJson(TOKEN_FILE);
  const mine = cached && cached.base_url === baseUrl ? cached : null;
  if (mine?.access_token) {
    try { await client.call("GET", "/auth/me", undefined, mine.access_token); return mine.access_token; }
    catch (e) { if (e.status !== 401) throw e; }
  }
  if (mine?.refresh_token) {
    try {
      const d = await client.call("POST", "/auth/refresh", { refresh_token: mine.refresh_token });
      saveToken(baseUrl, { access_token: d.access_token, refresh_token: mine.refresh_token });
      return d.access_token;
    } catch (e) { if (e.status !== 401) throw e; }
  }
  const { QALAB_USERNAME: username, QALAB_PASSWORD: password } = process.env;
  if (username && password) {
    const d = await client.call("POST", "/auth/login", { username, password });
    saveToken(baseUrl, { access_token: d.access_token, refresh_token: d.refresh_token });
    return d.access_token;
  }
  die("未登录或登录已过期。先执行：node md-submit.mjs login");
}

function ask(question, { hidden = false } = {}) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    if (hidden) rl._writeToOutput = (s) => { if (s.includes(question)) rl.output.write(question); };  // 不回显密码
    rl.question(question, (answer) => { rl.close(); if (hidden) process.stdout.write("\n"); resolve(answer.trim()); });
  });
}

async function login(client, baseUrl) {
  const username = process.env.QALAB_USERNAME || await ask("平台用户名: ");
  const password = process.env.QALAB_PASSWORD || await ask("密码: ", { hidden: true });
  const d = await client.call("POST", "/auth/login", { username, password });
  saveToken(baseUrl, { access_token: d.access_token, refresh_token: d.refresh_token });
  console.log(`✓ 已登录 ${baseUrl}，令牌保存在 ${TOKEN_FILE}（约 7 天有效，密码未保存）`);
}

async function waitRuns(client, getTok, runs, timeoutMin) {
  const deadline = Date.now() + timeoutMin * 60000;
  const done = new Map();
  const last = new Map();
  while (done.size < runs.length && Date.now() < deadline) {
    for (const run of runs) {
      if (done.has(run.id)) continue;
      let d;
      try { d = await client.call("GET", `/md-runs/${run.id}`, undefined, await getTok()); }
      catch (e) { console.error(`  查询 #${run.id} 失败（稍后重试）: ${e.message}`); continue; }
      if (last.get(run.id) !== d.status) { console.log(`  #${run.id} ${d.runner}: ${d.status}`); last.set(run.id, d.status); }
      if (TERMINAL.includes(d.status)) done.set(run.id, d);
    }
    if (done.size < runs.length) await new Promise((r) => setTimeout(r, 15000));
  }
  let code = 0;
  for (const run of runs) {
    const d = done.get(run.id);
    console.log(`\n===== #${run.id} ${run.runner} =====`);
    if (!d) { console.log(`等待超过 ${timeoutMin} 分钟仍未结束，去页面查看`); code = Math.max(code, 2); continue; }
    console.log(`结论: ${d.status}${d.reason ? ` —— ${d.reason}` : ""}`);
    if (d.findings?.length) console.log(`缺陷候选 ${d.findings.length} 条:\n${d.findings.map((f, i) => `  ${i + 1}. ${f.title || ""}`).join("\n")}`);
    if (d.result_md) console.log(`\n${d.result_md.trim()}`);
    code = Math.max(code, d.status === "passed" ? 0 : d.status === "failed" ? 1 : 2);
  }
  return code;
}

async function main() {
  const { flags, rest } = parseArgs(process.argv.slice(2));
  if (flags.help || !rest.length) {
    console.log(fs.readFileSync(new URL(import.meta.url), "utf-8").split("\n").slice(1, 20).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
    process.exit(rest.length ? 0 : 3);
  }
  const config = readJson(CONFIG_FILE) || {};
  const baseUrl = (flags["base-url"] || process.env.QALAB_BASE_URL || config.base_url || "").replace(/\/+$/, "");
  const client = baseUrl ? makeClient(baseUrl) : null;

  if (rest[0] === "login") {
    if (!client) die(`没有平台地址：设 QALAB_BASE_URL，或在 ${CONFIG_FILE} 写 base_url`);
    try { await login(client, baseUrl); } catch (e) { die(`登录失败：${e.message}`); }
    return;
  }

  const file = rest[0];
  if (!fs.existsSync(file)) die(`文件不存在: ${file}`);
  const text = fs.readFileSync(file, "utf-8");
  if (!text.trim()) die("文件是空的");

  let body;
  try { body = buildRequest({ file, text, flags, config }); } catch (e) { die(e.message); }

  const summary = `标题=${body.title} | 环境=${body.env_group} | 执行机=${body.runners.join(",")} | 项目=${body.project_id ?? "未绑定"}`
    + `${body.req_no ? ` | 提测号=${body.req_no}` : ""}${body.client_version ? ` | 客户端=${body.client_version}` : ""} | ${text.length} 字符`;
  if (flags["dry-run"]) { console.log(`[dry-run] 不会下发。${summary}`); return; }
  if (!client) die(`没有平台地址：设 QALAB_BASE_URL，或在 ${CONFIG_FILE} 写 base_url`);
  if (body.project_id === null) console.error("提示：未绑定项目，执行没问题，但之后无法在页面上把缺陷上报极库云（加 --project <id>）");

  const getTok = () => getToken(client, baseUrl);
  let runs;
  try { runs = await client.call("POST", "/md-runs/batch", body, await getTok()); }
  catch (e) { die(`下发失败：${e.message}`); }

  console.log(`✓ 已下发 ${runs.length} 条。${summary}`);
  for (const r of runs) console.log(`  #${r.id} → ${r.runner}`);
  console.log(`  查看: ${baseUrl}/md-tasks`);

  if (flags.wait) process.exit(await waitRuns(client, getTok, runs, Number(flags.timeout || 90)));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => die(e.message));
}
