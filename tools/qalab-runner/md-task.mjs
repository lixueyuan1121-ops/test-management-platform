// md 任务：从平台 /api/md-runs/queue 拉一份提测文档，切环境、交给 agent 执行、回写结果、还原环境。
//
// 与 exec-queue 的区别：那边是结构化用例（gui/api/cli 逐条断言），这边是**整份 md** 交给
// agent 自由执行。两者互不影响，平台侧也是两套表和页面。
//
// 开关：MD_TASK_ENABLED=1 才启用。runner 靠自升级分发到所有执行机，新能力必须默认关，
// 由人按机器显式打开，否则每台机器都会去抢这类任务。
//
// 注意：这里不用 codex-engine.mjs —— 那套是 `--ignore-user-config --sandbox read-only`
// 且关掉 plugins/shell，用来做单条断言；md 任务需要 agent 能按文档自由操作、能调它
// 自己配置的报缺陷 skill，所以走独立的调用方式（MD_CODEX_ARGS 可调）。

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import fs from "node:fs";
import path from "node:path";
import { makeHostsEnv } from "./hosts-env.mjs";
// 复用 codex-engine 的可执行文件解析：它已经处理了三种形态 ——
//   .js（@openai/codex/bin/codex.js，用 node 起）、Windows 上的 .cmd 包装、原生 .exe。
// 自己再写一份 where.exe 探测只会和它走岔。
import { codexCommand } from "./codex-engine.mjs";

// 桌面版 Codex 的 CLI 只在 Appx 包内（...\WindowsApps\OpenAI.Codex_<版本>_x64__<hash>\app\resources\codex.exe），
// 不进 PATH，而且路径带版本号 —— 升级后旧路径就没了。所以支持 MD_CODEX_BIN="appx:OpenAI.Codex"：
// 每次启动重新扫一遍，取版本号最大的那个包。
export function resolveAgentBin(bin, windowsAppsDir = process.env.ProgramFiles ? `${process.env.ProgramFiles}\\WindowsApps` : "C:\\Program Files\\WindowsApps") {
  const m = /^appx:(.+)$/i.exec(String(bin || "").trim());
  if (!m) return bin;
  const pkgName = m[1].trim();
  let entries = [];
  try {
    entries = fs.readdirSync(windowsAppsDir);
  } catch (e) {
    throw new Error(`扫不到 ${windowsAppsDir}（${e.code || e.message}），appx: 模式只能在这台机器的 Windows 上用；也可以直接把 MD_CODEX_BIN 写成 codex.exe 的绝对路径`);
  }
  const all = entries.filter((d) => d.startsWith(`${pkgName}_`) && /_(x64|arm64|x86)__/i.test(d));
  if (!all.length) throw new Error(`${windowsAppsDir} 下没有 ${pkgName} 包，确认 Codex 桌面版已安装`);
  // 优先本机架构：同一台机器上一般只装一个架构的包，但升级过渡期可能并存
  const archTag = { x64: "_x64__", arm64: "_arm64__", ia32: "_x86__" }[process.arch];
  const sameArch = archTag ? all.filter((d) => d.includes(archTag)) : [];
  const cands = sameArch.length ? sameArch : all;
  // 版本号形如 26.1120.5083.0，按数字段比较，别用字典序（"26.999" > "26.1120"）
  const ver = (d) => (d.match(/_([\d.]+)_/) || [, "0"])[1].split(".").map(Number);
  cands.sort((a, b) => {
    const va = ver(a), vb = ver(b);
    for (let i = 0; i < Math.max(va.length, vb.length); i++) {
      const x = va[i] || 0, y = vb[i] || 0;
      if (x !== y) return y - x;
    }
    return 0;
  });
  for (const d of cands) {
    const exe = path.join(windowsAppsDir, d, "app", "resources", "codex.exe");
    if (fs.existsSync(exe)) return exe;
  }
  throw new Error(`${pkgName} 包里没有 app\\resources\\codex.exe，可能这个版本不带 CLI`);
}

const IS_WIN = process.platform === "win32";

// 拆分 MD_CODEX_ARGS：要能处理 -c approval_policy="never" 这种带引号的值，
// 简单 split(/\s+/) 会把引号留在 token 里、或把值拆成两半。
export function tokenizeArgs(text) {
  const out = [];
  let cur = "", quote = null;
  for (const ch of String(text || "")) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; continue; }
    if (/\s/.test(ch)) { if (cur) { out.push(cur); cur = ""; } continue; }
    cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

function runCapture(cmd, args, { cwd, env, timeoutMs, control, shell = IS_WIN }) {
  return new Promise((resolve) => {
    const started = Date.now();
    let child;
    try {
      child = spawn(cmd, args, { cwd, env, shell, windowsHide: true });
    } catch (e) {
      return resolve({ code: -1, out: "", err: `无法启动 ${cmd}: ${e.message}`, timedOut: false, duration_ms: 0 });
    }
    let out = "", err = "", done = false;
    const kill = () => { try { child.kill("SIGKILL"); } catch { /* 已退出 */ } };
    const finish = (code, timedOut) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ code, out, err, timedOut, duration_ms: Date.now() - started });
    };
    const timer = timeoutMs > 0 ? setTimeout(() => { kill(); finish(-1, true); }, timeoutMs) : null;
    if (control) control.kill = kill;   // 平台取消时由心跳掉线逻辑调用
    child.stdout?.on("data", (d) => (out += d));
    child.stderr?.on("data", (d) => (err += d));
    child.on("error", (e) => { err += e.message; finish(-1, false); });
    child.on("close", (code) => finish(code, false));
  });
}

// 结果文档里出现这些说法，说明 agent 自己都判定"没真正执行"。
// 只认否定词不算完 —— 必须同时没有任何 pass 行的证据（见下方 rows）。
const BLOCKED_HINTS = /执行受阻|无法执行|未能执行|未能读取|未能完成|无法读取|无法启动|环境不可用|连接超时|窗口.*超时|执行 \*\*0 项\*\*|已执行 0 项|0 项已执行|no applicable|blocked/i;

/** 从 agent 的结果文档里抽"是否有失败"和缺陷候选。
 * 返回值里的 rows 是"带结论的表格行数"：0 行意味着 agent 没产出可判定的结果，
 * 这种情况必须判 blocked，绝不能因为"没看到 fail"就记成 pass —— 空跑记成通过是 QA 最坏的假阳性。 */
function parseResult(text) {
  const findings = [];
  let rows = 0, passes = 0;
  if (!text) return { findings, rows, passes };
  // 1) 表格：| 用例 | 结论 | 说明 |  —— 结论列含 fail/失败 的行算失败
  for (const line of text.split(/\r?\n/)) {
    if (!/^\s*\|/.test(line)) continue;
    const cells = line.split("|").map((c) => c.trim()).filter((c, i, a) => !(i === 0 || i === a.length - 1));
    if (cells.length < 2) continue;
    rows++;
    if (/fail|失败|不通过/i.test(cells[1])) {
      findings.push({ title: cells[0] || "未命名用例失败", detail: cells.slice(2).join(" / ") || "", severity: "major" });
    } else if (/pass|通过|正常/i.test(cells[1])) {
      passes++;
    }
  }
  // 2) 「## 缺陷」小节下的列表项
  const sec = text.split(/\r?\n#{1,4}\s*缺陷/)[1];
  if (sec) {
    for (const line of sec.split(/\r?\n#{1,4}\s/)[0].split(/\r?\n/)) {
      const m = line.match(/^\s*(?:[-*]|\d+\.)\s+(.{4,})$/);
      if (m) findings.push({ title: m[1].slice(0, 200), detail: "", severity: "major" });
    }
  }
  // 去重（表格与列表可能重复描述同一条）
  const seen = new Set();
  const unique = findings.filter((f) => {
    const k = f.title.toLowerCase();
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return { findings: unique, rows, passes };
}

export function makeMdTaskRunner({
  api, log, RUNNER_ID, runtimeDir,
  groupsDir, helperPath, helperTaskName,
  verifyDomains, verifyTimeoutSec,
  codexBin, codexArgs, codexTimeoutMs, workRoot, addDirs,
}) {
  const hosts = makeHostsEnv({ log, runtimeDir, groupsDir, helperPath, taskName: helperTaskName });
  let busy = false;

  async function execute(item) {
    const base = `md-${item.run_id}`;
    const workDir = path.join(workRoot || path.join(runtimeDir, "md-runs"), base);
    fs.rmSync(workDir, { recursive: true, force: true });
    fs.mkdirSync(workDir, { recursive: true });
    const docPath = path.join(workDir, "task.md");
    fs.writeFileSync(docPath, item.content || "");

    const started = Date.now();
    let envCheck = null;
    let result = null;
    const q = `runner=${encodeURIComponent(RUNNER_ID)}`;

    // 心跳：一方面证明还活着，另一方面感知"平台已取消" → 主动杀 agent 并还原环境
    let canceled = false;
    const control = { kill: null };
    const heartbeat = setInterval(async () => {
      try {
        const hb = await api("POST", `/api/md-runs/queue/${item.run_id}/heartbeat?${q}`);
        if (hb?.status === "canceled" && !canceled) {
          canceled = true;
          log(`平台已取消 run_id=${item.run_id}，终止 agent 并还原环境`);
          control.kill?.();
        }
      } catch { /* 心跳失败不致命，网络恢复后继续 */ }
    }, 60000);

    try {
      envCheck = await hosts.apply(item.env_group, verifyDomains, verifyTimeoutSec);
      log(`环境就绪 group=${item.env_group} verify=${envCheck.verify.map((v) => `${v.domain}->${v.got}`).join(",")}`);

      const prompt = [
        `请阅读并执行提测文档：${docPath}`,
        ``,
        `要求：`,
        `1. 按文档中的用例逐条在被测客户端上执行；客户端已安装、环境已配置好，不要改 hosts。`,
        `2. 发现缺陷时按你配置的方式上报${item.req_no ? `，标题带上提测号 ${item.req_no}` : ""}。`,
        `3. 执行结束后把结果写入 ${path.join(workDir, "result.md")}，格式：表格「| 用例 | 结论 | 说明 |」，`,
        `   失败用例的结论写 fail，并在文末用「## 缺陷」小节列出每条缺陷的标题与复现要点。`,
        `4. 这是无人值守执行：**不要向我提问、不要等待确认**。文档缺信息就按最合理的理解继续；`,
        `   确实无法继续时（客户端起不来、缺账号、工具不可用），把原因和已完成的部分写进 result.md，然后结束。`,
        `5. 工作目录 ${workDir} 是唯一可写位置；需要留截图/日志就放在它下面。`,
      ].join("\n");

      const cmd = codexCommand(resolveAgentBin(codexBin));
      // -o：让 CLI 无论 agent 有没有按约定写 result.md，都把最后一条回复落盘。
      // 无人值守下"agent 忘了写文件"是常见失败，有这个兜底才能拿到结论。
      const lastMsgPath = path.join(workDir, "agent-last-message.md");
      const extra = [];
      if (!codexArgs.some((a) => a === "-o" || a === "--output-last-message")) extra.push("-o", lastMsgPath);
      // 客户端/证据目录不在工作目录下时，agent 的 shell 写不进去（沙箱只给 workdir + /tmp）。
      // 由 MD_CODEX_ADD_DIRS 显式放开，别图省事用 danger-full-access。
      if (!codexArgs.includes("--add-dir")) {
        for (const d of addDirs || []) if (d) extra.push("--add-dir", d);
      }
      log(`启动 agent run_id=${item.run_id}${item.req_no ? ` req=${item.req_no}` : ""} bin=${cmd.command}`);
      const r = await runCapture(cmd.command, [...cmd.prefix, ...codexArgs, ...extra, prompt], {
        cwd: workDir, env: process.env, timeoutMs: codexTimeoutMs, control,
        shell: cmd.prefix.length ? false : IS_WIN,
      });
      fs.writeFileSync(path.join(workDir, "agent.log"), `# exit=${r.code} timedOut=${r.timedOut}\n\n## stdout\n${r.out}\n\n## stderr\n${r.err}`);

      const resultPath = path.join(workDir, "result.md");
      const resultMd = fs.existsSync(resultPath) ? fs.readFileSync(resultPath, "utf-8")
        : fs.existsSync(lastMsgPath) ? fs.readFileSync(lastMsgPath, "utf-8")
        : (r.out || "");
      const { findings, rows, passes } = parseResult(resultMd);

      if (canceled) {
        result = { verdict: "blocked", reason: "平台已取消，执行中断", result_md: resultMd || null, findings, env_check: envCheck };
      } else if (r.timedOut) {
        result = { verdict: "blocked", reason: `agent 执行超时（${Math.round(codexTimeoutMs / 1000)}s）`, result_md: resultMd || null, findings, env_check: envCheck };
      } else if (r.code !== 0 || !(fs.existsSync(resultPath) || fs.existsSync(lastMsgPath))) {
        // 把 agent 自己吐的最后一行错误带回去：命令不存在 / 未登录 / 配置错，
        // 在无人值守时全靠这一句，否则页面上只看到 exit=-1。
        const tail = (r.err || "").trim().split(/\r?\n/).filter(Boolean).slice(-1)[0] || "";
        result = {
          verdict: "blocked",
          reason: `agent 未正常完成（exit=${r.code}）${tail ? `：${tail.slice(0, 300)}` : ""}`,
          result_md: resultMd || null, findings, env_check: envCheck,
        };
      } else if (findings.length) {
        result = { verdict: "fail", reason: `${findings.length} 条用例失败`, result_md: resultMd, findings, env_check: envCheck };
      } else if (rows === 0 || (passes === 0 && BLOCKED_HINTS.test(resultMd))) {
        // 没有可判定结论：agent 什么都没跑成、或只写了"执行受阻"。
        // 这种情况如果判 pass，等于把空跑记成通过 —— 假通过比假失败危害大得多。
        const hint = (resultMd.match(BLOCKED_HINTS) || [""])[0];
        result = {
          verdict: "blocked",
          reason: rows === 0
            ? `agent 未产出可判定的结果（0 条带结论的用例行）${hint ? `；文档提到「${hint}」` : ""}`
            : `agent 自述执行受阻${hint ? `「${hint}」` : ""}，无通过证据`,
          result_md: resultMd, findings: [], env_check: envCheck,
        };
      } else {
        result = { verdict: "pass", reason: "", result_md: resultMd, findings, env_check: envCheck };
      }
    } catch (e) {
      // 环境/客户端类失败：归 blocked，不当成产品缺陷（与 exec 的 fail_kind=selector 同思路）
      if (e.envCheck) envCheck = e.envCheck;
      if (/ENOENT|not recognized|不是内部或外部命令|CODEX_BIN/i.test(String(e.message))) {
        e.message = `找不到 agent 命令（MD_CODEX_BIN=${codexBin}）：${e.message}。` +
          `Windows 上桌面版 Codex 不把 CLI 放 PATH，需把 MD_CODEX_BIN 指向 codex.exe 或 @openai/codex/bin/codex.js`;
      }
      result = { verdict: "blocked", reason: e.message, findings: [], env_check: envCheck };
    } finally {
      clearInterval(heartbeat);
      const restored = await hosts.restore();
      // 即使没走到切换这一步（分组不存在/快照失败），也要带上 group 与 restored，
      // 让人一眼看出"这次到底动没动环境、有没有还回去"。
      result.env_check = { ...(envCheck || { group: item.env_group, verify: [] }), restored };
    }

    result.duration_ms = Date.now() - started;
    await api("PATCH", `/api/md-runs/queue/${item.run_id}?${q}`, {
      verdict: result.verdict,
      reason: result.reason || null,
      result_md: result.result_md || null,
      findings: result.findings || [],
      duration_ms: result.duration_ms,
      env_check: result.env_check || null,
    });
    log(`回写 run_id=${item.run_id} -> ${result.verdict}${result.reason ? ` (${String(result.reason).slice(0, 120)})` : ""}`);
  }

  /** 一轮轮询：一次只拿一条（这类任务耗时长，抢多了没意义），整轮串行。 */
  async function pollOnce() {
    if (busy) return 0;
    busy = true;
    try {
      const pending = await api("GET", `/api/md-runs/queue?runner=${encodeURIComponent(RUNNER_ID)}&limit=1`);
      if (!pending?.length) return 0;
      const item = pending[0];
      await api("POST", `/api/md-runs/queue/${item.run_id}/claim?runner=${encodeURIComponent(RUNNER_ID)}`);
      await execute(item);
      return 1;
    } finally {
      busy = false;
    }
  }

  return { pollOnce, heal: hosts.heal };
}
