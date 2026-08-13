// StepExecutor —— 按结构化 script 确定性执行 gui/e2e 用例(P3 核心)。
// 不经 LLM:assert_* 步直接算 pass/fail,快/稳/省/可复现。用 gui-core 做定位(与 claude 走同一套引擎)。
//
// script 形状(见设计稿 §5.1):[{ action, target?:{key|selector}, args?, desc? }, ...]
//   - 定位/操作:connect / click / fill / wait_for / wait_response / get_text / screenshot / goto
//   - 断言:assert_text(args:{expected,contains}) / assert_visible
//   - judge(主观判定):args:{question};喂前面步骤捕获的 context 给 claude,只回 {pass,reason}。
//     需 runner 注入 judgeFn(question, context)=>{pass,reason};未注入时该步降级(整条退回 claude)。
//   - 未知 action → needClaude,整条退回 claude 兜底。
//
// 判定:任一 assert_*/judge 失败 → 整条 fail;全部通过 → pass。

const DETERMINISTIC = new Set([
  "connect", "click", "fill", "wait_for", "wait_response", "get_text", "screenshot", "goto",
  "assert_text", "assert_visible", "judge",
]);

// gui: createGuiCore() 实例;script: 步骤数组;log: 进度回调;judgeFn: 可选,judge 步调它降级 claude
export async function runScript(gui, script, log = () => {}, judgeFn = null) {
  if (!Array.isArray(script) || script.length === 0) {
    return { needClaude: true, reason: "用例无结构化 script,退回 claude 执行" };
  }
  // 预检:未知 action → 整条退回 claude;judge 但没注入 judgeFn → 也退回(无法单步判定)
  for (const st of script) {
    const a = String(st?.action || "");
    if (!DETERMINISTIC.has(a)) return { needClaude: true, reason: `未知 step action「${a}」,退回 claude` };
    if (a === "judge" && typeof judgeFn !== "function") return { needClaude: true, reason: "含 judge 步但未注入 judgeFn,整条退回 claude" };
  }

  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);
  const evidence = [];   // 证据链:每步一条
  const steps = [];      // 结构化步骤结果
  const captured = [];   // judge 步的上下文素材(前面 get_text 文本 / screenshot 路径)

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
        case "wait_response": {
          const r = await gui.waitResponse({ timeout_ms: args.timeout_ms });
          steps.push({ action, ...r, desc });
          if (!r.done) return fail(`step${i + 1} 等待 AI 回复未完成:${r.reason || ""}`, steps, evidence, started);
          break;
        }
        case "get_text": { const r = await gui.getText(target); captured.push(`[文本] ${desc || target.key || target.selector}: ${r.text}`); steps.push({ action, ok: true, ...r }); break; }
        case "screenshot": { const r = await gui.screenshot(args.path || `evidence/step${i + 1}.png`); evidence.push(r.evidence); captured.push(`[截图] ${r.evidence}`); steps.push({ action, ok: true, ...r }); break; }
        case "judge": {
          // 主观判定:把前面步骤捕获的 context 喂 claude,只判这一步(不整条降级)。
          const context = captured.join("\n") || "(前面步骤未捕获文本/截图;请仅依据问题判断)";
          const v = await judgeFn(args.question || desc, context);
          steps.push({ action, ...v, question: args.question || desc, desc });
          if (!v.pass) return fail(`step${i + 1} judge 判定不通过:${v.reason || ""}`, steps, evidence, started);
          break;
        }
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
  // 所有步骤(含断言/judge)通过
  const checks = steps.filter((s) => s.action.startsWith("assert") || s.action === "judge").length;
  return {
    verdict: "pass",
    reason: `结构化执行通过:${script.length} 步,${checks} 处判定全部满足`,
    evidence: evidence[evidence.length - 1] || null,
    duration_ms: Date.now() - started,
    steps,
  };
}

function fail(reason, steps, evidence, started) {
  return { verdict: "fail", reason, evidence: evidence[evidence.length - 1] || null, duration_ms: Date.now() - started, steps };
}
