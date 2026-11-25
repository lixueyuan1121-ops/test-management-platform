// 环境切换：按执行机本地的 hosts 分组文件切换系统 hosts，跑完还原。
//
// 设计约束（改之前先读）：
//   1. 分组内容只存在于执行机（MD_GROUPS_DIR），平台只存分组名。内网 IP 不上传。
//   2. 不驱动 SwitchHosts 等第三方工具：只写系统 hosts。跑之前人怎么设置的不关心。
//   3. 写 hosts 要提权，但 runner 和 agent 都不该提权 —— 所以只把这一件事交给 helper：
//        Mac: sudo -n <MD_HOSTS_HELPER> {snapshot|apply|restore} <path>
//        Win: 写 job.json → schtasks /run /tn <MD_HOSTS_TASK> → 读 job.result.json
//   4. 切换后必须校验解析，校验不过就中止，绝不让 agent 在错环境里跑。
//   5. 即使中途崩溃，下次启动 heal() 会按 pending-restore.json 还原。

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import fs from "node:fs";
import path from "node:path";

const IS_WIN = process.platform === "win32";

function run(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { ...opts, windowsHide: true });
    let out = "", err = "";
    child.stdout?.on("data", (d) => (out += d));
    child.stderr?.on("data", (d) => (err += d));
    child.on("error", (e) => resolve({ code: -1, out, err: err + e.message }));
    child.on("close", (code) => resolve({ code, out, err }));
  });
}

export function makeHostsEnv({ log, runtimeDir, groupsDir, helperPath, taskName, direct = false }) {
  const hostsRuntime = path.join(runtimeDir, "hosts");
  const pendingFile = path.join(hostsRuntime, "pending-restore.json");
  fs.mkdirSync(hostsRuntime, { recursive: true });

  // 直写模式：不经过提权助手，直接读写系统 hosts。
  // 前提是运行 runner 的账号对 hosts 有写权限（一次性 icacls 授权即可）。
  // 代价：agent 与 runner 同账号，理论上也能改 hosts —— 权限隔离没了，只剩切完校验 + 跑完还原。
  // 换来的是整层计划任务/job 文件/账号匹配的故障面消失。专用测试机上值得。
  function directOp(action, file) {
    const sys = sysHostsPath();
    try {
      if (action === "snapshot") {
        fs.copyFileSync(sys, file);
      } else if (action === "apply" || action === "restore") {
        const text = fs.readFileSync(file, "utf-8");
        fs.writeFileSync(sys, text, "utf-8");
      }
      return { code: 0, out: "", err: "" };
    } catch (e) {
      const hint = e.code === "EPERM" || e.code === "EACCES" ? permHint(sys) : "";
      return { code: -1, out: "", err: `${e.message}${hint}` };
    }
  }

  // 权限提示按平台给，别在 Mac 上教人敲 icacls
  function permHint(sys) {
    return IS_WIN
      ? `无写权限：在管理员 PowerShell 里执行 icacls "${sys}" /grant "$($env:USERNAME):(M)"，然后重开 runner`
      : `无写权限：Mac/Linux 请改用提权助手（不要开 MD_HOSTS_DIRECT），或 sudo chown 让该账号可写 ${sys}`;
  }

  function sysHostsPath() {
    // 与 runner 平台判断一致；MD_HOSTS_PATH 可覆盖（非标准安装，或直写模式的测试）
    return process.env.MD_HOSTS_PATH || (IS_WIN ? "C:\\Windows\\System32\\drivers\\etc\\hosts" : "/etc/hosts");
  }

  async function viaHelper(action, file) {
    if (direct) return directOp(action, file);
    if (!IS_WIN) {
      const bin = helperPath || "/usr/local/bin/qa-hosts-helper";
      return run("sudo", ["-n", bin, action, file]);
    }
    // job 文件必须放在 helper 约定的位置（runtime 根，不是 runtime/hosts）：
    // helper 由计划任务拉起，只认 QA_RUNTIME 或 %USERPROFILE%\qa-workspace\runtime。
    const jobPath = path.join(runtimeDir, "job.json");
    const resPath = path.join(runtimeDir, "job.result.json");
    const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try { fs.unlinkSync(resPath); } catch {}
    fs.writeFileSync(jobPath, JSON.stringify({ token, action, arg: file }));
    const r = await run("schtasks", ["/run", "/tn", taskName || "qa-hosts-helper"]);
    if (r.code !== 0) return { code: -1, out: "", err: `schtasks 启动失败: ${r.err}` };
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      try {
        const res = JSON.parse(fs.readFileSync(resPath, "utf-8"));
        if (res.token === token) return { code: res.ok ? 0 : -1, out: res.out || "", err: res.err || "" };
      } catch { /* helper 还没写完 */ }
    }
    // 超时原因基本只有三种：任务没被拉起、脚本找不到（路径/账号不对）、脚本报错。
    // helper 自己会写 runtime\qa-hosts-helper.log，把最后一行带出来就能区分。
    let hint = "";
    try {
      const logFile = path.join(runtimeDir, "qa-hosts-helper.log");
      if (fs.existsSync(logFile)) {
        const tail = fs.readFileSync(logFile, "utf-8").trim().split(/\r?\n/).slice(-1)[0];
        hint = tail ? `；helper 日志：${tail}` : "；helper 日志为空";
      } else {
        hint = `；helper 从未写入 ${path.join(runtimeDir, "qa-hosts-helper.log")}，多半是计划任务没指到脚本或跑在别的账号下`;
      }
    } catch { /* 读不到就算了，别把超时再包成另一个错 */ }
    return { code: -1, out: "", err: `hosts helper 超时未返回${hint}` };
  }

  async function flushDns() {
    if (IS_WIN) return run("ipconfig", ["/flushdns"]);
    await run("dscacheutil", ["-flushcache"]);
    return run("killall", ["-HUP", "mDNSResponder"]);
  }

  async function resolveIp(domain) {
    if (IS_WIN) {
      const r = await run("powershell.exe", ["-NoProfile", "-Command",
        `try { ([System.Net.Dns]::GetHostAddresses('${domain}') | Where-Object { $_.AddressFamily -eq 'InterNetwork' } | Select-Object -First 1).IPAddressToString } catch { '' }`]);
      return r.out.trim() || null;
    }
    const r = await run("dscacheutil", ["-q", "host", "-a", `name=${domain}`]);
    const m = r.out.match(/ip_address:\s*(\S+)/);
    return m ? m[1] : null;
  }

  // 期望值以分组文件里写的为准，不看任何外部配置，也不依赖平台
  function expectedIp(text, domain) {
    for (const line of text.split(/\r?\n/)) {
      const clean = line.split("#")[0].trim();
      if (!clean) continue;
      const parts = clean.split(/\s+/);
      if (parts.length >= 2 && parts.slice(1).includes(domain)) return parts[0];
    }
    return null;
  }

  function groupFile(group) {
    return path.join(groupsDir || "", `${group}.hosts`);
  }

  function listGroups() {
    try {
      return fs.readdirSync(groupsDir || "").filter((f) => f.endsWith(".hosts")).map((f) => f.replace(/\.hosts$/, ""));
    } catch { return []; }
  }

  /** 切到某个分组并校验。返回 env_check 留痕；校验失败时抛错（调用方负责还原）。 */
  async function apply(group, verifyDomains, verifyTimeoutSec = 15) {
    const file = groupFile(group);
    if (!fs.existsSync(file)) {
      const avail = listGroups();
      throw new Error(`本机没有 hosts 分组「${group}」${avail.length ? `（可用: ${avail.join(", ")}）` : `（${groupsDir} 下没有任何 .hosts）`}`);
    }
    const snapshot = path.join(hostsRuntime, `snapshot-${Date.now()}.hosts`);
    let r = await viaHelper("snapshot", snapshot);
    if (r.code !== 0) throw new Error(`快照 hosts 失败: ${r.err.trim()}`);
    fs.writeFileSync(pendingFile, JSON.stringify({ snapshot, group, at: Date.now() }));

    r = await viaHelper("apply", file);
    if (r.code !== 0) throw new Error(`写入 hosts 失败: ${r.err.trim()}`);
    await flushDns();

    const text = fs.readFileSync(file, "utf-8");
    const verify = [];
    const problems = [];
    for (const domain of verifyDomains || []) {
      const want = expectedIp(text, domain);
      if (!want) continue;
      let got = null;
      for (let i = 0; i < verifyTimeoutSec; i++) {
        got = await resolveIp(domain);
        if (got === want) break;
        await sleep(1000);
      }
      verify.push({ domain, want, got });
      if (got !== want) problems.push(`${domain} 期望 ${want} 实际 ${got ?? "无解析"}`);
    }
    if (problems.length) {
      // 带着已校验到的明细一起抛：调用方要能把"期望/实际"回写给平台，
      // 否则事后只看得到一句"校验失败"，排查时不知道环境到底指到哪去了。
      const err = new Error(`环境校验失败: ${problems.join("; ")}`);
      err.envCheck = { group, verify, restored: false };
      throw err;
    }
    return { group, verify, restored: false };
  }

  /** 还原。任何路径都要调用（包括抛错后的 finally）。 */
  async function restore() {
    if (!fs.existsSync(pendingFile)) return true;
    try {
      const p = JSON.parse(fs.readFileSync(pendingFile, "utf-8"));
      const r = await viaHelper("restore", p.snapshot);
      if (r.code !== 0) { log(`hosts 还原失败（下次启动会重试）: ${r.err.trim()}`); return false; }
      await flushDns();
      try { fs.unlinkSync(pendingFile); } catch {}
      try { fs.unlinkSync(p.snapshot); } catch {}
      return true;
    } catch (e) {
      log(`hosts 还原异常: ${e.message}`);
      return false;
    }
  }

  /** 自检：能否以提权身份读到 hosts（只做快照，不改任何东西）。 */
  async function preflight() {
    if (direct) {
      const sys = sysHostsPath();
      try {
        const text = fs.readFileSync(sys, "utf-8");
        fs.writeFileSync(sys, text, "utf-8");   // 内容不变，只验证权限
        return { ok: true };
      } catch (e) {
        const hint = e.code === "EPERM" || e.code === "EACCES" ? `；${permHint(sys)}` : "";
        return { ok: false, err: `${e.message}${hint}` };
      }
    }
    const f = path.join(runtimeDir, "hosts-preflight.hosts");
    try {
      const r = await viaHelper("snapshot", f);
      if (r.code !== 0) return { ok: false, err: (r.err || "").trim() || "快照失败" };
      fs.existsSync(f) && fs.unlinkSync(f);
      return { ok: true };
    } catch (e) {
      return { ok: false, err: e.message };
    }
  }

  /** 启动时自愈：上次崩在"已切未还原"，系统 hosts 会停在测试环境。 */
  async function heal() {
    if (!fs.existsSync(pendingFile)) return;
    log("发现上次未还原的 hosts，执行自愈");
    const ok = await restore();
    log(ok ? "hosts 已还原" : "hosts 自愈失败，请检查 helper 权限");
  }

  return { apply, restore, heal, preflight, listGroups, groupFile };
}
