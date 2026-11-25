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

const IS_WIN = process.platform === "win32";

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

/** 从 agent 的结果文档里抽"是否有失败"和缺陷候选。解析宁缺毋滥：认不出就当没缺陷，不影响 verdict。 */
function parseResult(text) {
  const findings = [];
  if (!text) return { findings };
  // 1) 表格：| 用例 | 结论 | 说明 |  —— 结论列含 fail/失败 的行算失败
  for (const line of text.split(/\r?\n/)) {
    if (!/^\s*\|/.test(line)) continue;
    const cells = line.split("|").map((c) => c.trim()).filter((c, i, a) => !(i === 0 || i === a.length - 1));
    if (cells.length < 2) continue;
    if (/fail|失败|不通过/i.test(cells[1])) {
      findings.push({ title: cells[0] || "未命名用例失败", detail: cells.slice(2).join(" / ") || "", severity: "major" });
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
  return { findings: unique };
}

export function makeMdTaskRunner({
  api, log, RUNNER_ID, runtimeDir,
  groupsDir, helperPath, helperTaskName,
  verifyDomains, verifyTimeoutSec,
  codexBin, codexArgs, codexTimeoutMs, workRoot,
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
      ].join("\n");

      const cmd = codexCommand(codexBin);
      log(`启动 agent 执行 run_id=${item.run_id}${item.req_no ? ` req=${item.req_no}` : ""} bin=${cmd.command}`);
      const r = await runCapture(cmd.command, [...cmd.prefix, ...codexArgs, prompt], {
        cwd: workDir, env: process.env, timeoutMs: codexTimeoutMs, control,
        shell: cmd.prefix.length ? false : IS_WIN,
      });
      fs.writeFileSync(path.join(workDir, "agent.log"), `# exit=${r.code} timedOut=${r.timedOut}\n\n## stdout\n${r.out}\n\n## stderr\n${r.err}`);

      const resultPath = path.join(workDir, "result.md");
      const resultMd = fs.existsSync(resultPath) ? fs.readFileSync(resultPath, "utf-8") : (r.out || "");
      const { findings } = parseResult(resultMd);

      if (canceled) {
        result = { verdict: "blocked", reason: "平台已取消，执行中断", result_md: resultMd || null, findings, env_check: envCheck };
      } else if (r.timedOut) {
        result = { verdict: "blocked", reason: `agent 执行超时（${Math.round(codexTimeoutMs / 1000)}s）`, result_md: resultMd || null, findings, env_check: envCheck };
      } else if (r.code !== 0 || !fs.existsSync(resultPath)) {
        // 把 agent 自己吐的最后一行错误带回去：命令不存在 / 未登录 / 配置错，
        // 在无人值守时全靠这一句，否则页面上只看到 exit=-1。
        const tail = (r.err || "").trim().split(/\r?\n/).filter(Boolean).slice(-1)[0] || "";
        result = {
          verdict: "blocked",
          reason: `agent 未正常完成（exit=${r.code}）${tail ? `：${tail.slice(0, 300)}` : ""}`,
          result_md: resultMd || null, findings, env_check: envCheck,
        };
      } else {
        result = { verdict: findings.length ? "fail" : "pass", reason: findings.length ? `${findings.length} 条用例失败` : "", result_md: resultMd, findings, env_check: envCheck };
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
