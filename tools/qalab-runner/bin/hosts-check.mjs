#!/usr/bin/env node
// hosts-check —— 验证本机 hosts 分组文件，不连平台、不跑 agent。
//
//   node bin/hosts-check.mjs                 列出所有分组及它们会写的映射
//   node bin/hosts-check.mjs test       真的切一次：快照 → 写入 → 校验解析 → 还原，并核对还原是否一致
//
// 用来在发真任务之前把「文件名对不对 / IP 写没写错 / DNS 通不通 / 还原得回来吗」这几件事先验掉。
// 读 .env 的方式与 runner 一致（同目录 .env），所以用的就是 runner 的真实配置。

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { makeHostsEnv } from "../hosts-env.mjs";

const SELF_DIR = path.dirname(fileURLToPath(import.meta.url));
const RUNNER_DIR = path.resolve(SELF_DIR, "..");
const IS_WIN = process.platform === "win32";

// 与 runner.mjs 同款的极简 .env 加载（不覆盖已存在的环境变量）
(function loadDotenv() {
  try {
    for (const line of fs.readFileSync(path.join(RUNNER_DIR, ".env"), "utf-8").split(/\r?\n/)) {
      const eq = line.indexOf("=");
      if (eq < 0 || line.trim().startsWith("#")) continue;
      const key = line.slice(0, eq).trim();
      const val = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
      if (key && process.env[key] === undefined) process.env[key] = val;
    }
  } catch { /* 没有 .env 就用当前环境 */ }
})();

const expand = (p) => (p?.startsWith("~") ? path.join(os.homedir(), p.slice(1)) : p);
const home = process.env.HOME || process.env.USERPROFILE || os.homedir();
const GROUPS_DIR = expand(process.env.MD_GROUPS_DIR || path.join(home, "qa-workspace", "hosts-groups"));
const RUNTIME_DIR = expand(process.env.MD_RUNTIME_DIR || path.join(home, "qa-workspace", "runtime"));
const DIRECT = (process.env.MD_HOSTS_DIRECT ?? "0") === "1";
const VERIFY = (process.env.MD_VERIFY_DOMAINS || "").split(",").map((x) => x.trim()).filter(Boolean);
const SYS_HOSTS = process.env.MD_HOSTS_PATH || (IS_WIN ? "C:\\Windows\\System32\\drivers\\etc\\hosts" : "/etc/hosts");

const sha = (file) => {
  try { return createHash("sha256").update(fs.readFileSync(file)).digest("hex").slice(0, 12); }
  catch (e) { return `读取失败(${e.code || e.message})`; }
};

function mappingsOf(file) {
  const out = [];
  for (const line of fs.readFileSync(file, "utf-8").split(/\r?\n/)) {
    const clean = line.split("#")[0].trim();
    if (!clean) continue;
    const parts = clean.split(/\s+/);
    if (parts.length >= 2) out.push({ ip: parts[0], hosts: parts.slice(1) });
  }
  return out;
}

function listGroups() {
  console.log(`分组目录: ${GROUPS_DIR}`);
  if (!fs.existsSync(GROUPS_DIR)) {
    console.log("  目录不存在。先从 tools/qalab-runner/hosts-groups.example/ 拷一份过去。");
    return;
  }
  const files = fs.readdirSync(GROUPS_DIR).filter((f) => f.endsWith(".hosts")).sort();
  if (!files.length) { console.log("  目录里没有任何 .hosts 文件。"); return; }
  for (const f of files) {
    const name = f.replace(/\.hosts$/, "");
    const maps = mappingsOf(path.join(GROUPS_DIR, f));
    const base = name === "_base" ? "（基础行，不作为环境下发）" : "";
    if (!maps.length) { console.log(`  ${name.padEnd(16)} 无域名映射 ${base}`); continue; }
    console.log(`  ${name.padEnd(16)} ${maps.length} 条 ${base}`);
    for (const m of maps) console.log(`      ${m.ip.padEnd(15)} ${m.hosts.join(" ")}`);
  }
  console.log(`\n校验域名(MD_VERIFY_DOMAINS): ${VERIFY.length ? VERIFY.join(", ") : "（空，不做解析校验）"}`);
  console.log(`系统 hosts: ${SYS_HOSTS}    模式: ${DIRECT ? "直写" : "提权助手"}`);
}

async function check(group) {
  const hosts = makeHostsEnv({
    log: (...a) => console.log("  ", ...a),
    runtimeDir: RUNTIME_DIR, groupsDir: GROUPS_DIR, direct: DIRECT,
    helperPath: process.env.MD_HOSTS_HELPER, taskName: process.env.MD_HOSTS_TASK,
  });
  const file = hosts.groupFile(group);
  if (!fs.existsSync(file)) {
    console.error(`✗ 没有这个分组: ${file}`);
    const avail = fs.existsSync(GROUPS_DIR)
      ? fs.readdirSync(GROUPS_DIR).filter((f) => f.endsWith(".hosts")).map((f) => f.replace(/\.hosts$/, ""))
      : [];
    console.error(`  可用: ${avail.join(", ") || "（无）"}`);
    process.exit(1);
  }

  const before = sha(SYS_HOSTS);
  console.log(`组: ${group}`);
  console.log(`还原前 hosts 指纹: ${before}`);

  let info = null;
  try {
    info = await hosts.apply(group, VERIFY, Number(process.env.MD_VERIFY_TIMEOUT_SEC || 15));
    console.log(`切换成功，校验结果：`);
    if (!info.verify.length) console.log("  （该分组没有涉及校验域名，跳过解析检查）");
    for (const v of info.verify) console.log(`  ${v.domain.padEnd(20)} 期望 ${v.want}  实际 ${v.got}  ${v.got === v.want ? "✓" : "✗"}`);
  } catch (e) {
    console.error(`✗ 切换失败：${e.message}`);
  } finally {
    const ok = await hosts.restore();
    const after = sha(SYS_HOSTS);
    console.log(`还原后 hosts 指纹: ${after}`);
    console.log(ok && before === after ? "✓ 已完整还原" : `✗ 还原异常（${ok ? "内容与原来不一致" : "还原失败"}），请检查 ${SYS_HOSTS}`);
  }
  process.exit(info ? 0 : 1);
}

const arg = process.argv[2];
if (!arg) listGroups();
else await check(arg);
