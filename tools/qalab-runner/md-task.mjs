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
// 自己配置的客户端操作工具，所以走独立的调用方式（MD_CODEX_ARGS 可调）。

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import fs from "node:fs";
import path from "node:path";
import { startClientLog, collectMdEvidence, queueMdReport, sendMdReport } from "./md-evidence.mjs";
import { makeHostsEnv } from "./hosts-env.mjs";
// 复用 codex-engine 的可执行文件解析：它已经处理了三种形态 ——
//   .js（@openai/codex/bin/codex.js，用 node 起）、Windows 上的 .cmd 包装、原生 .exe。
// 自己再写一份 where.exe 探测只会和它走岔。
import { codexCommand } from "./codex-engine.mjs";

// 桌面版 Codex 的 CLI 只在 Appx 包内（...\WindowsApps\OpenAI.Codex_<版本>_x64__<hash>\app\resources\codex.exe），
// 不进 PATH，而且路径带版本号 —— 升级后旧路径就没了。所以支持 MD_CODEX_BIN="appx:OpenAI.Codex"：
// 每次启动重新扫一遍，取版本号最大的那个包。
export function resolveAgentBin(bin, windowsAppsDir = process.env.ProgramFiles ? `${process.env.ProgramFiles}\\WindowsApps` : "C:\\Program Files\\WindowsApps") {
  // npm:codex → npm 全局安装目录下的 codex.cmd（APPADATA\npm）。
  // 实测 npm 版 0.172 默认 unified exec 可用，而 Appx 版 0.171-alpha 在 Windows 上起不来
  // （exec_command 报 "Failed to create unified exec process: runner handshake task failed"），
  // 所以推荐这条路：与桌面版升级解耦，也不依赖 WindowsApps 的 ACL。
  const npmMatch = /^npm:(.+)$/i.exec(String(bin || "").trim());
  if (npmMatch) {
    const name = npmMatch[1].trim();
    const base = process.env.APPDATA
      || (process.env.USERPROFILE ? `${process.env.USERPROFILE}\\AppData\\Roaming` : "");
    if (!base) throw new Error("npm: 模式需要 APPDATA/USERPROFILE，可以直接把 MD_CODEX_BIN 写成 codex.cmd 的绝对路径");
    const cmdPath = path.join(base, "npm", `${name}.cmd`);
    if (!fs.existsSync(cmdPath)) {
      throw new Error(`没找到 ${cmdPath}。先在该机执行 npm i -g @openai/codex，或把 MD_CODEX_BIN 写成绝对路径`);
    }
    return cmdPath;
  }
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

function runCapture(cmd, args, { cwd, env, timeoutMs, control, logFile }) {
  return new Promise((resolve) => {
    const started = Date.now();
    let child;
    try {
      // stdin 必须 ignore：`codex exec` 的规则是「stdin 有内容就追加到 prompt 之后」，
      // stdin 是打开的管道时它会一直等 EOF —— 表现为永远不返回、也没有任何输出。
      // 手动在终端跑没事，因为终端里的 stdin 不是管道。
      child = spawn(cmd, args, { cwd, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    } catch (e) {
      return resolve({ code: -1, out: "", err: `无法启动 ${cmd}: ${e.message}`, timedOut: false, duration_ms: 0 });
    }
    let out = "", err = "", done = false;
    const kill = () => { try { child.kill("SIGKILL"); } catch { /* 已退出 */ } };
    const finish = (code, timedOut) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { sink?.end(); } catch {}
      resolve({ code, out, err, timedOut, duration_ms: Date.now() - started });
    };
    const timer = timeoutMs > 0 ? setTimeout(() => { kill(); finish(-1, true); }, timeoutMs) : null;
    if (control) control.kill = kill;   // 平台取消时由心跳掉线逻辑调用
    // 边跑边写文件：无人值守时卡住/失败，靠这个能看到它最后在做什么
    let sink = null;
    if (logFile) { try { sink = fs.createWriteStream(logFile, { flags: "a" }); } catch { sink = null; } }
    const tap = (chunk) => { try { sink?.write(chunk); } catch { /* 写盘失败不影响执行 */ } };
    child.stdout?.on("data", (d) => { out += d; tap(d); });
    child.stderr?.on("data", (d) => { err += d; tap(d); });
    child.on("error", (e) => { err += e.message; finish(-1, false); });
    child.on("close", (code) => finish(code, false));
  });
}

export function makeMdTaskRunner({
  api, log, RUNNER_ID, runtimeDir,
  groupsDir, helperPath, helperTaskName,
  verifyDomains, verifyTimeoutSec,
  codexBin, codexArgs, codexTimeoutMs, workRoot, addDirs, direct, restartClient, captureScreenshot, clientLogFile,
  hosts = makeHostsEnv({ log, runtimeDir, groupsDir, helperPath, taskName: helperTaskName, direct }),
}) {
  let busy = false;
  const pendingDir = path.join(runtimeDir, "md-pending-reports");

  async function execute(item) {
    const base = `md-${item.run_id}`;
    const workDir = path.join(workRoot || path.join(runtimeDir, "md-runs"), base);
    fs.rmSync(workDir, { recursive: true, force: true });
    fs.mkdirSync(workDir, { recursive: true });
    const docPath = path.join(workDir, "task.md");
    fs.writeFileSync(docPath, item.content || "");

    fs.mkdirSync(path.join(workDir, "evidence"));
    const started = Date.now();
    let logStart, evidence = { files: [], notes: [] };
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

      envCheck.client_restarted = false;
      if (canceled) throw new Error("平台已取消");
      try {
        logStart = startClientLog(clientLogFile);
        await restartClient();
      } catch (error) {
        throw new Error(`目标客户端重启失败：${error.message}`);
      }
      envCheck.client_restarted = true;
      if (canceled) throw new Error("平台已取消");

      const prompt = [
        `请阅读并执行提测文档：${docPath}`,
        ``,
        `要求：`,
        `1. 按文档中的用例逐条在被测客户端上执行；客户端已安装、环境已配置好，不要改 hosts。`,
        `2. 按用例预期判定结果，包括负向场景。实际符合预期填 pass，不符合填 fail；环境问题导致无法验证填 blocked。每条 fail 都要关联缺陷候选，由平台在人工确认后上报极库云。`,
        `3. 执行结束后把结果写入 ${path.join(workDir, "result.md")}，格式：表格「| 用例 | 结论 | 说明 |」，`,
        `   失败行的说明写清预期和实际结果。文末写「## 产品缺陷」，每个问题用「### 问题标题」开头，下一行写「关联用例：用例编号或全名」。同一问题影响多条用例时用顿号列出，再写复现步骤和证据。没有候选时本节填「无」。执行期间不要创建外部缺陷。`,
        `4. 这是无人值守执行：**不要向我提问、不要等待确认**。文档缺信息就按最合理的理解继续；`,
        `   确实无法继续时（客户端起不来、缺账号、工具不可用），把原因和已完成的部分写进 result.md，然后结束。`,
        `5. 工作目录 ${workDir} 是唯一可写位置；每条失败用例在异常现场用客户端截图工具（如 gui_screenshot）将真实图片保存到 ${path.join(workDir, "evidence")} 下，传入完整的 PNG/JPEG 绝对路径，继续下一条前完成。可同时保存该用例对应的客户端日志片段为 UTF-8 .log。不要复制 agent.log、配置文件或整个历史日志。`,
        `   将每份附件登记到 evidence/manifest.json，内容为 JSON 数组，例如 [{"case":"C-001","file":"C-001.png"},{"case":"C-001","file":"C-001.log"}]。case 使用结果表中对应的编号或全名；文件名不能含目录。采集失败要在报告写明原因。`,
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
      const liveLog = path.join(workDir, "agent.log");
      fs.writeFileSync(liveLog, `# run_id=${item.run_id} bin=${cmd.command} args=${[...cmd.prefix, ...codexArgs].join(" ")}\n`);
      const r = await runCapture(cmd.command, [...cmd.prefix, ...codexArgs, ...extra, prompt], {
        cwd: workDir, env: process.env, timeoutMs: codexTimeoutMs, control,
        logFile: liveLog,
      });
      fs.appendFileSync(liveLog, `\n# exit=${r.code} timedOut=${r.timedOut} duration=${Date.now() - started}ms\n`);

      const resultPath = path.join(workDir, "result.md");
      const resultMd = fs.existsSync(resultPath) ? fs.readFileSync(resultPath, "utf-8")
        : fs.existsSync(lastMsgPath) ? fs.readFileSync(lastMsgPath, "utf-8")
        : (r.out || "");

      if (canceled) {
        result = { verdict: "blocked", reason: "平台已取消，执行中断", result_md: resultMd || null, env_check: envCheck };
      } else if (r.timedOut) {
        result = { verdict: "blocked", reason: `agent 执行超时（${Math.round(codexTimeoutMs / 1000)}s）`, result_md: resultMd || null, env_check: envCheck };
      } else if (r.code !== 0 || !(fs.existsSync(resultPath) || fs.existsSync(lastMsgPath))) {
        // 把 agent 自己吐的最后一行错误带回去：命令不存在 / 未登录 / 配置错，
        // 在无人值守时全靠这一句，否则页面上只看到 exit=-1。
        const tail = (r.err || "").trim().split(/\r?\n/).filter(Boolean).slice(-1)[0] || "";
        result = {
          verdict: "blocked",
          reason: `agent 未正常完成（exit=${r.code}）${tail ? `：${tail.slice(0, 300)}` : ""}`,
          result_md: resultMd || null, env_check: envCheck,
        };
      } else {
        result = { verdict: "completed", result_md: resultMd, env_check: envCheck };
      }
    } catch (e) {
      // 环境/客户端类失败：归 blocked，不当成产品缺陷（与 exec 的 fail_kind=selector 同思路）
      if (e.envCheck) envCheck = e.envCheck;
      if (envCheck?.client_restarted && /ENOENT|not recognized|不是内部或外部命令|CODEX_BIN/i.test(String(e.message))) {
        e.message = `找不到 agent 命令（MD_CODEX_BIN=${codexBin}）：${e.message}。` +
          `Windows 上桌面版 Codex 不把 CLI 放 PATH：建议 MD_CODEX_BIN=npm:codex（npm 全局安装），或指向 codex.exe / codex.cmd`;
      }
      result = { verdict: "blocked", reason: e.message, env_check: envCheck };
    } finally {
      try {
        if (!canceled) evidence = await collectMdEvidence({ workDir, logStart, captureScreenshot, clientStarted: envCheck?.client_restarted });
      } catch (error) { evidence.notes.push("附件采集失败：" + error.message.slice(0, 160)); }
      clearInterval(heartbeat);
      const restored = await hosts.restore();
      // 即使没走到切换这一步（分组不存在/快照失败），也要带上 group 与 restored，
      // 让人一眼看出"这次到底动没动环境、有没有还回去"。
      result.env_check = { ...(envCheck || { group: item.env_group, verify: [] }), restored };
    }

    result.duration_ms = Date.now() - started;
    result.evidence_summary = { expected: evidence.files.length, notes: evidence.notes };
    const packet = queueMdReport(pendingDir, item.run_id, result, evidence.files);
    const saved = await sendMdReport(packet, api, RUNNER_ID, log);
    const reason = saved?.reason || result.reason;
    log(`回写 run_id=${item.run_id} -> ${saved?.verdict || result.verdict}${reason ? ` (${String(reason).slice(0, 120)})` : ""}`);
  }

  /** 一轮轮询：一次只拿一条（这类任务耗时长，抢多了没意义），整轮串行。 */
  async function pollOnce() {
    if (busy) return 0;
    busy = true;
    try {
      if (fs.existsSync(pendingDir)) {
        for (const file of fs.readdirSync(pendingDir).filter(name => /^md-\d+\.json$/.test(name))) {
          try { await sendMdReport(path.join(pendingDir, file), api, RUNNER_ID, log); }
          catch (error) { log("提测结果等待重传：" + error.message); }
        }
      }
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

  return { pollOnce, heal: hosts.heal, preflight: hosts.preflight };
}
