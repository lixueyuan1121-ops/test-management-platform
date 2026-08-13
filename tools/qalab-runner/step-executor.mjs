// StepExecutor —— 按结构化 script 确定性执行 gui/e2e 用例(P3 核心)。
// 不经 LLM:assert_* 步直接算 pass/fail,快/稳/省/可复现。用 gui-core 做定位(与 claude 走同一套引擎)。
//
// script 形状(见设计稿 §5.1):[{ action, target?:{key|selector}, args?, desc? }, ...]
//   - 定位/操作:connect / click / fill / wait_for / get_text / screenshot / goto
//   - 断言:assert_text(args:{expected,contains}) / assert_visible
//   - 需降级 claude 的:judge(主观判定)/ wait_response(等 AI 生成)/ 未知 action
//     → executor 不处理,返回 { needClaude:true, reason },由 runner 回退到 claude 兜底执行整条用例。
//
// 判定:任一 assert_* 失败 → 整条 fail(附该步 desc+实际值);全部通过 → pass。
// 证据:每步结果(命中候选 via、实际值、截图)累积进 steps[],回写时可读。

const DETERMINISTIC = new Set([
  "connect", "click", "fill", "wait_for", "get_text", "screenshot", "goto",
  "assert_text", "assert_visible",
]);
// 这些 action 需要主观判断或长等待,确定性执行器不碰,退回 claude:
const NEEDS_CLAUDE = new Set(["judge", "wait_response"]);

// gui: createGuiCore() 实例;script: 步骤数组;log: 进度回调(sec, msg)
export async function runScript(gui, script, log = () => {}) {
  if (!Array.isArray(script) || script.length === 0) {
    return { needClaude: true, reason: "用例无结构化 script,退回 claude 执行" };
  }
  // 预检:出现需降级的 action(或未知 action)→ 整条退回 claude(避免执行一半再切,状态不一致)
  for (const st of script) {
    const a = String(st?.action || "");
    if (NEEDS_CLAUDE.has(a)) return { needClaude: true, reason: `step「${a}」需 claude 判定/等待,整条退回 claude` };
    if (!DETERMINISTIC.has(a)) return { needClaude: true, reason: `未知 step action「${a}」,退回 claude` };
  }

  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);
  const evidence = [];   // 证据链:每步一条
  const steps = [];      // 结构化步骤结果

  for (let i = 0; i < script.length; i++) {
    const st = script[i];
    const { action, target = {}, args = {}, desc = "" } = st;
    const tag = `step${i + 1}/${script.length} ${action}${desc ? "(" + desc + ")" : ""}`;
    log(`  [+${sec()}s] ▶ ${tag}`);
    try {
      switch (action) {
        case "connect": { const r = await gui.connect(); steps.push({ action, ok: true, ...r }); break; }
        case "goto": { const r = await gui.goto(args.url || target.url); steps.push({ action, ok: true, ...r }); break; }
        case "click": { const r = await gui.click(target); steps.push({ action, ok: true, ...r }); break; }
        case "fill": { const r = await gui.fill({ ...target, text: args.text }); steps.push({ action, ok: true, ...r }); break; }
        case "wait_for": { const r = await gui.waitFor({ ...target, timeout_ms: args.timeout_ms }); steps.push({ action, ok: true, ...r }); break; }
        case "get_text": { const r = await gui.getText(target); steps.push({ action, ok: true, ...r }); break; }
        case "screenshot": { const r = await gui.screenshot(args.path || `evidence/step${i + 1}.png`); evidence.push(r.evidence); steps.push({ action, ok: true, ...r }); break; }
        case "assert_visible": {
          const r = await gui.assertVisible(target);
          steps.push({ action, ...r, desc });
          if (!r.pass) return fail(`step${i + 1} 断言可见失败:${desc || target.key || target.selector}(${r.error || ""})`, steps, evidence, started);
          break;
        }
        case "assert_text": {
          const r = await gui.assertText({ ...target, expected: args.expected, contains: args.contains });
          steps.push({ action, ...r, desc });
          if (!r.pass) return fail(`step${i + 1} 断言文本失败:期望${r.mode === "contains" ? "包含" : "等于"}「${args.expected}」,实际「${r.actual}」`, steps, evidence, started);
          break;
        }
      }
    } catch (e) {
      // 定位/操作抛错(元素找不到、超时等)→ 整条 fail,带诊断
      return fail(`step${i + 1}「${action}」执行出错:${e.message}`, steps, evidence, started);
    }
  }
  // 所有步骤(含断言)通过
  const asserts = steps.filter((s) => s.action.startsWith("assert")).length;
  return {
    verdict: "pass",
    reason: `结构化执行通过:${script.length} 步,${asserts} 处断言全部满足`,
    evidence: evidence[evidence.length - 1] || null,
    duration_ms: Date.now() - started,
    steps,
  };
}

function fail(reason, steps, evidence, started) {
  return { verdict: "fail", reason, evidence: evidence[evidence.length - 1] || null, duration_ms: Date.now() - started, steps };
}
