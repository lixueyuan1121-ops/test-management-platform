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
//
// 执行报告(report):返回 result.report = [{ no, action, desc, ok, error?, shotBuf? }, ...]。
//   截图策略「关键步 + 失败必截」:显式 screenshot 步、assert_* 通过后、以及任一步失败时,截当前视口为
//   PNG Buffer 挂在该步 shotBuf。runner 负责把 shotBuf 逐张上传换成 URL(step-executor 不碰网络)。

const DETERMINISTIC = new Set([
  "connect", "click", "hover", "fill", "type", "press",
  "wait_for", "wait_response", "get_text", "screenshot", "goto",
  "assert_text", "assert_visible", "assert_absent", "judge",
  "mock_route", "unmock_route",
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
  const evidence = [];   // 证据链:每步一条(兼容旧 evidence_url:取最后一条)
  const steps = [];      // 结构化步骤结果
  const report = [];     // 执行报告:每步 { no, action, desc, ok, error?, shotBuf? }
  const captured = [];   // judge 步的上下文素材(前面 get_text 文本 / screenshot 路径)
  // 本条用例注册过的网络拦截:pattern -> { pattern, status, hits }。按**整条用例**累计,
  // 而非收尾时问一次 gui —— 脚本尾部跑过 unmock_route 的拦截器那时已不在 gui 里,统计会凭空丢掉。
  const mocksSeen = new Map();

  // 截当前视口为 Buffer 挂到某 report 步(关键步/失败存证);失败静默,不阻断执行。
  const capShot = async (rep) => {
    try { const b = await gui.shotBuffer?.(); if (b && b.length) rep.shotBuf = b; } catch { /* 忽略 */ }
  };
  // 记一步到 report(desc 缺省用 action);返回该 report 条目供后续挂截图。
  const rec = (i, action, desc, ok, error) => {
    const rep = { no: i + 1, action, desc: desc || action, ok, ...(error ? { error } : {}) };
    report.push(rep);
    return rep;
  };
  // 每条返回路径的统一出口(失败早退/断言失败/全部通过都经此),负责两件收尾:
  //  ① 兜底清掉本条用例注册的全部网络拦截 —— 用例中途失败会直接早退、跳过脚本尾部的 unmock_route,
  //     而拦截器挂在 BrowserContext 上跨用例存活,不清就会让后续每一条用例都吃到这条假数据;
  //  ② 把 mock 命中统计挂进结果,并在「注册了 mock 却全程 0 拦截」+ 用例失败时点名 ——
  //     这正是 mock 不生效最常见的形态(URL 模式没匹配上真实请求),不点名就只剩一句看不出所以然的断言失败。
  // 老 gui 没有 mockStats/unmockAll → 各自跳过(向后兼容)。
  const finish = async (result) => {
    // 收尾时仍存活的拦截器命中数以 gui 为准(它一直在计数);已被 unmock_route 撤掉的以 mocksSeen 记的为准。
    // 老 gui 不会计数,此时 hits 恒 0 并不代表"没拦到",不能据此报警 —— 整块统计一并跳过。
    let counted = typeof gui.mockStats === "function";
    if (counted) {
      try { for (const s of gui.mockStats() || []) mocksSeen.set(s.pattern, { ...s }); }
      catch { counted = false; }
    }
    if (typeof gui.unmockAll === "function") {
      try { await gui.unmockAll(); } catch (e) { log(`  mock 收尾清理失败(不影响本条判定):${e.message || e}`); }
    }
    if (!counted || !mocksSeen.size) return result;
    const stats = [...mocksSeen.values()];
    result.mock_stats = stats;
    const dead = stats.filter((s) => !s.hits).map((s) => s.pattern);
    if (dead.length) {
      log(`  ⚠ mock 未生效:「${dead.join("、")}」全程 0 拦截(URL 模式可能与真实请求不匹配)`);
      // 结论里必须说清:失败时它常常就是失败主因;通过时更要说 —— 那是在真实数据上通过的「假通过」,
      // mock 场景其实一次都没被验证过,不点破就会被当成"这个异常分支已覆盖"。
      const why = `mock「${dead.join("、")}」全程未拦截到任何请求,URL 模式可能与真实请求不匹配`;
      result.reason = result.verdict === "fail"
        ? `${result.reason}（注意:${why},mock 数据未生效）`
        : `${result.reason}（注意:${why},本条实际是在真实数据上通过的,mock 场景未被验证）`;
    }
    return result;
  };
  // 失败:记该步 + 截图,返回 fail 结果(带 report/steps)。
  // failKind 归类失败性质(接后端 L2):"selector"=定位/操作/环境阻塞(不计功能失败率),
  // "business"=断言不通过(真功能 bug)。缺省 selector(定位/操作抛错走 catch 兜底,均属阻塞类)。
  const failAt = async (i, action, desc, reason, failKind = "selector", extraSteps) => {
    const rep = rec(i, action, desc, false, reason);
    await capShot(rep);
    return finish({ verdict: "fail", fail_kind: failKind, reason, evidence: evidence[evidence.length - 1] || null, duration_ms: Date.now() - started, steps: extraSteps || steps, report });
  };

  for (let i = 0; i < script.length; i++) {
    const st = script[i];
    const { action, target = {}, args = {}, desc = "" } = st;
    const tag = `step${i + 1}/${script.length} ${action}${desc ? "(" + desc + ")" : ""}`;
    log(`  [+${sec()}s] ▶ ${tag}`);
    try {
      switch (action) {
        case "connect": { const r = await gui.connect(); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "goto": { const r = await gui.goto(args.url || target.url); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "click": { const r = await gui.click(target); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "hover": { const r = await gui.hover(target); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "fill": { const r = await gui.fill({ ...target, text: args.text }); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        // type: 逐字符追加输入，不清空原有内容。先 click 聚焦元素，再模拟键盘打字。
        // 用于：在已有内容末尾追加、或对 fill 不兼容的富文本组件输入。
        case "type": { const r = await gui.type({ ...target, text: args.text }); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        // press: 向目标元素（或全局页面）发送单个按键（如 End / Home / Enter / Escape / Tab）。
        // target 有值时先 click 聚焦再按键；用 args.key_name 指定按键名。
        case "press": { const r = await gui.pressKey({ ...target, key_name: args.key_name }); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        // mock_route: 拦截匹配 args.url 的 fetch/XHR 请求，直接返回 args.status + args.body。
        // 用于模拟后端返回数据，验证前端在各种响应下的行为。
        // unmock_route: 取消拦截，恢复真实请求。
        case "mock_route": { const r = await gui.mockRoute(args); mocksSeen.set(String(args.url || ""), { pattern: String(args.url || ""), status: Number(args.status ?? 200), hits: 0 }); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "unmock_route": { const r = await gui.unmockRoute(args); const seen = mocksSeen.get(String(args.url || "")); if (seen) seen.hits = Number(r?.hits ?? seen.hits); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "wait_for": { const r = await gui.waitFor({ ...target, timeout_ms: args.timeout_ms }); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "wait_response": {
          const r = await gui.waitResponse({ timeout_ms: args.timeout_ms });
          steps.push({ action, ...r, desc });
          if (!r.done) return await failAt(i, action, desc, `step${i + 1} 等待 AI 回复未完成:${r.reason || ""}`);
          rec(i, action, desc, true);
          break;
        }
        case "get_text": { const r = await gui.getText(target); captured.push(`[文本] ${desc || target.key || target.selector}: ${r.text}`); steps.push({ action, ok: true, ...r }); rec(i, action, desc, true); break; }
        case "screenshot": {
          // 显式截图步:既落本地文件(兼容旧证据),又挂 Buffer 进报告。
          const r = await gui.screenshot(args.path || `evidence/step${i + 1}.png`);
          evidence.push(r.evidence); captured.push(`[截图] ${r.evidence}`);
          steps.push({ action, ok: true, ...r });
          const rep = rec(i, action, desc, true); await capShot(rep);
          break;
        }
        case "judge": {
          // 主观判定:把前面步骤捕获的 context 喂 claude,只判这一步(不整条降级)。
          const context = captured.join("\n") || "(前面步骤未捕获文本/截图;请仅依据问题判断)";
          const v = await judgeFn(args.question || desc, context);
          steps.push({ action, ...v, question: args.question || desc, desc });
          if (!v.pass) return await failAt(i, action, desc, `step${i + 1} judge 判定不通过:${v.reason || ""}`, "business");
          rec(i, action, desc, true);
          break;
        }
        case "assert_visible": {
          const r = await gui.assertVisible(target);
          steps.push({ action, ...r, desc });
          if (!r.pass) {
            // 定位不到(locatable=false)= 选择器/候选没覆盖 → selector(阻塞,不计功能失败率);
            // 定位到但不可见(locatable=true 或未提供)= 该可见却没可见 → business(真功能问题)。
            const kind = r.locatable === false ? "selector" : "business";
            const why = r.locatable === false ? "元素定位不到(选择器/key 未覆盖)" : (r.error || "");
            return await failAt(i, action, desc, `step${i + 1} 断言可见失败:${desc || target.key || target.selector}(${why})`, kind);
          }
          const rep = rec(i, action, desc, true); await capShot(rep);   // 关键步通过后存证
          break;
        }
        case "assert_absent": {
          // 否定式可见断言:元素消失即通过。仍可见 → business(本应消失却还在)。
          const r = await gui.assertAbsent(target);
          steps.push({ action, ...r, desc });
          if (!r.pass) return await failAt(i, action, desc, `step${i + 1} 断言消失失败:${desc || target.key || target.selector}(元素仍可见,未按预期消失)`, "business");
          const rep = rec(i, action, desc, true); await capShot(rep);
          break;
        }
        case "assert_text": {
          const r = await gui.assertText({ ...target, expected: args.expected, contains: args.contains, negate: args.negate });
          steps.push({ action, ...r, desc });
          if (!r.pass) {
            const rel = `${r.negate ? "不" : ""}${r.mode === "contains" ? "包含" : "等于"}`;
            const rep = rec(i, action, desc, false, `step${i + 1} 断言文本失败:期望${rel}「${args.expected}」,实际「${r.actual}」`);
            rep.check = { actual: r.actual, expected: args.expected, mode: r.mode, negate: !!r.negate };  // 结构化证据,供纠偏一眼分辨真假 fail
            await capShot(rep);
            return finish({ verdict: "fail", fail_kind: "business", reason: rep.error, evidence: evidence[evidence.length - 1] || null, duration_ms: Date.now() - started, steps, report });
          }
          const rep = rec(i, action, desc, true); await capShot(rep);   // 关键步通过后存证
          break;
        }
      }
    } catch (e) {
      // 定位/操作抛错(元素找不到、超时等)→ 整条 fail,带诊断 + 失败现场截图
      return await failAt(i, action, desc, `step${i + 1}「${action}」执行出错:${e.message}`);
    }
  }
  // 所有步骤(含断言/judge)通过
  const checks = steps.filter((s) => s.action.startsWith("assert") || s.action === "judge").length;
  return finish({
    verdict: "pass",
    reason: `结构化执行通过:${script.length} 步,${checks} 处判定全部满足`,
    evidence: evidence[evidence.length - 1] || null,
    duration_ms: Date.now() - started,
    steps,
    report,
  });
}

