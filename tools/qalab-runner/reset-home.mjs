// 用例前硬复位的重试封装(与 runner 主循环解耦,便于单测)。
// gui.resetHome() 失败重试至多 attempts 次;全失败返回 false(调用方判 fail,不空跑脏态用例)。
export async function resetHomeWithRetry(gui, log = () => {}, attempts = 2) {
  for (let i = 0; i < attempts; i++) {
    try { await gui.resetHome(); return true; }
    catch (e) { log(`  复位失败(第${i + 1}次):${e.message || e}`); }
  }
  return false;
}


// 首页「就绪锚点」候选 key:不同被测产品/注册表命名不一 —— 内置注册表用 homepageTitle,
// 新版 home 用 homeGreetingTitle(线上导入的注册表)。按注册存在性过滤后任一可见即算首页停稳。
// 新增命名时在此登记别名即可(单点)。
const HOME_READY_KEYS = ["homepageTitle", "homeGreetingTitle"];
// 登录弹窗锚点候选 key(掉登录检测)。
const LOGIN_MODAL_KEYS = ["loginModal"];
// 复位自愈入口候选 key:首页 reload 后没停稳时,点侧栏「新建任务/新建对话」强制开一个干净会话回首页。
// 对齐人工纠偏动作(卡在会话/详情里 → 点侧栏新建任务)。按注册存在性过滤,注册表没登记则跳过。
const NEW_CONVERSATION_KEYS = ["newTask", "newChat"];
// 复位自愈入口候选 key:点侧栏主导航「首页」切回首页 Tab(无副作用,不开新会话)。
// 对齐人工纠偏动作(卡在会话/详情/其它 Tab 里 → 点侧栏『首页』导航)。reload 只重载同一 SPA 路由、
// 关不掉时,显式导航回首页最贴合意图;按注册存在性过滤,注册表没登记则跳过。
const NAV_HOME_KEYS = ["navHome"];

// 从候选名里挑出「当前注册表确实登记了」的 key。
// 关键:isKeyVisible 对**未注册的 key 也返回 false**,无法区分「注册表压根没这个锚点」与「注册了但
// 页面上没渲染出来」。若不按注册存在性过滤,换了 key 命名的项目(如首页 key 叫 homeGreetingTitle 而
// 非 homepageTitle)会让门禁探一个恒 false 的锚点 → 每条用例都被判「首页没停稳」连续失败。
// gui.registry 不可用时(防御性)退回全部候选,不比旧行为差。
function registeredKeys(gui, names) {
  const reg = gui && gui.registry;
  if (!reg || typeof reg !== "object") return names.slice();
  return names.filter((k) => reg[k]);
}

// 轮询探首页/登录锚点就绪:返回 { login, ready }。login=任一登录弹窗可见(掉登录);
// ready=已登记首页锚点可见。掉登录优先,一探到立即返回。
async function probeReady(gui, loginKeys, homeKeys, { readyTimeout, pollMs }) {
  const deadline = Date.now() + readyTimeout;
  const probe = [...loginKeys, ...homeKeys];
  for (;;) {
    if (typeof gui.verifyKeys !== "function") throw new Error("执行器缺少就绪检查能力");
    const { verify } = await gui.verifyKeys(probe);
    const v = verify || {};
    if (loginKeys.some((k) => v[k])) return { login: true, ready: false };
    if (homeKeys.some((k) => v[k])) return { login: false, ready: true };
    if (Date.now() >= deadline) return { login: false, ready: false };
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

// 首页没停稳时的自愈:依次点候选 key(注册表里登记了的)里第一个可点成功的,回首页/开干净会话。
// 返回被点击的 key(供日志),都不可用则返回 null。gui.click 不可用(老 gui)也返回 null。
async function clickFirstAvailable(gui, keys, okLog, log) {
  if (typeof gui.click !== "function") return null;
  for (const key of registeredKeys(gui, keys)) {
    try { await gui.click({ key }); log(okLog(key)); return key; }
    catch (e) { log(`  自愈点「${key}」失败:${e.message || e}`); }
  }
  return null;
}

// 点侧栏主导航「首页」切回首页 Tab(无副作用)。都不可用返回 null。
async function clickNavHome(gui, log) {
  return clickFirstAvailable(gui, NAV_HOME_KEYS, (k) => `  首页没停稳:已点主导航「${k}」切回首页,重探就绪`, log);
}

// 点侧栏「新建任务/新建对话」开干净会话回首页(有副作用)。都不可用返回 null。
async function clickNewConversation(gui, log) {
  return clickFirstAvailable(gui, NEW_CONVERSATION_KEYS, (k) => `  首页没停稳:已点「${k}」开干净会话,重探就绪`, log);
}

// 掉登录(会话过期)的阻塞结果:只能重登,自愈无意义。复位流程多处复用(初探/ESC 后/新建会话后),抽此消重。
function loginBlocked() {
  return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "复位后检测到登录弹窗:执行机会话可能已过期,请在执行机重新登录被测客户端", duration_ms: 1 } };
}

// 调用 gui 上某个「尽力而为」的自愈动作,返回是否「试过且生效」:方法不存在(老 gui)→ false 跳过;
// 抛错 → false 不阻断后续自愈;返回 { escaped:false }(如 OS 级 ESC 平台不支持/超时)→ false 跳过其重探。
async function tryGuiHeal(gui, method, desc, log) {
  if (typeof gui[method] !== "function") return false;
  try {
    const r = await gui[method]();
    if (r && r.escaped === false) { log(`  自愈「${desc}」未生效,跳过`); return false; }
    log(`  首页没停稳:已${desc},重探就绪`);
    return true;
  } catch (e) {
    log(`  自愈「${desc}」失败:${e.message || e}`);
    return false;
  }
}

// Reload and recovery actions are followed by a readiness check, including after
// a client restart. Navigation success alone does not establish fixture readiness.
// Persistent business data is preserved; test-specific setup/cleanup belongs in
// explicit fixture steps, not a blanket deletion of the signed-in client profile.
export async function resetOrBlock(gui, log = () => {}, { readyTimeout = 8000, pollMs = 300, maxHealRounds = 3, restartClientFn } = {}) {
  const loginKeys = registeredKeys(gui, LOGIN_MODAL_KEYS);
  const homeKeys = registeredKeys(gui, HOME_READY_KEYS);
  const blocked = (reason) => ({ ok: false, result: { verdict: "fail", fail_kind: "selector", reason, duration_ms: 1 } });
  if (!homeKeys.length) return blocked("未配置首页就绪锚点，请登记 homepageTitle/homeGreetingTitle 后执行");
  // 内部helper:调 restartClientFn 重启客户端,等 CDP 就绪后再尝试一次 resetHome。
  // 成功返回 true;无 restartClientFn / 重启失败 / 复位仍失败均返回 false。
  async function tryRestartAndReset(label) {
    if (typeof restartClientFn !== "function") return false;
    try {
      log(`  ${label}:尝试退出客户端并重新启动…`);
      await restartClientFn();
      log(`  客户端已重启,重新复位中…`);
      const ok = await resetHomeWithRetry(gui, log);
      if (ok) {
        const st = await probeReady(gui, loginKeys, homeKeys, { readyTimeout, pollMs });
        if (!st.login && st.ready) { log("  重启后首页已验证就绪"); return true; }
      }
      log(`  重启后复位仍失败`);
    } catch (e) {
      log(`  重启客户端出错:${e.message || e}`);
    }
    return false;
  }

  if (!(await resetHomeWithRetry(gui, log))) {
    // 兜底:复位重试全失败 → 尝试重启客户端 + 再复位一次
    if (await tryRestartAndReset("复位重试全失败")) return { ok: true };
    return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "用例前复位(reload)失败,跳过执行以免脏态污染", duration_ms: 1 } };
  }
  try {
    let st = await probeReady(gui, loginKeys, homeKeys, { readyTimeout, pollMs });
    // 掉登录优先:任一登录弹窗可见 → 立即阻塞(不等首页、不自愈:会话过期只能重登)。
    if (st.login) { log("  复位后检测到登录弹窗:会话可能过期"); return loginBlocked(); }
    if (st.ready) return { ok: true };
    // 首页锚点已登记但超时仍不可见:reload 可能只重载了停在会话/详情里的同一路由,或页面被弹窗/系统窗挡住。
    log("  复位后首页问候标题未就绪,开始分层自愈");
    // 分层自愈,三招「来回反复」多轮尝试(每招后重探,救回即放行),而非走一遍就放弃 —— 确保用例间衔接顺滑:
    //   ①ESC 关弹窗:页面层 ESC(关网页模态/浮层,快、无害)+ OS 级 ESC(关系统窗,如文件资源管理器/原生文件框)
    //   ②点两次侧栏主导航「首页」(切回首页 Tab,无副作用;第一次可能落在过渡态/只收面板,第二次补一击)
    //   ③点侧栏「新建任务/新建对话」(开干净会话,有副作用兜底)
    // 一轮按①→②→③顺序走(无副作用的排前),一轮走完仍不就绪就再来一轮,最多 maxHealRounds 轮反复;
    // 某轮内所有招式都不可用(gui 能力缺失/未注册 key)则提前止损,不空转多轮。
    const heals = [
      { label: "页面层 ESC", run: () => tryGuiHeal(gui, "pressEscapePage", "按页面层 ESC 关网页弹窗/浮层", log) },
      { label: "OS 级 ESC", run: () => tryGuiHeal(gui, "pressEscapeOs", "按 OS 级 ESC 关系统窗(如文件资源管理器)", log) },
      { label: "点首页导航", run: () => clickNavHome(gui, log).then(Boolean) },
      { label: "再点首页导航", run: () => clickNavHome(gui, log).then(Boolean) },
      { label: "新建会话", run: () => clickNewConversation(gui, log).then(Boolean) },
    ];
    const reprobeTimeout = Math.max(pollMs, Math.floor(readyTimeout / 3));
    const tried = [];
    for (let round = 1; round <= maxHealRounds; round++) {
      let anyTried = false;   // 本轮是否有任一招「试过且生效」——一整轮全不可用则再反复也无意义,提前止损
      for (const { label, run } of heals) {
        if (!(await run())) continue;
        anyTried = true;
        tried.push(round > 1 ? `${label}#${round}` : label);   // 第 2 轮起标注轮次,便于看反复了几遍
        st = await probeReady(gui, loginKeys, homeKeys, { readyTimeout: reprobeTimeout, pollMs });
        if (st.login) return loginBlocked();
        if (st.ready) { log(`  自愈成功(第${round}轮「${label}」后首页已停稳),放行`); return { ok: true }; }
      }
      if (!anyTried) break;   // 本轮无任何可用招式(老 gui/未注册 key),反复也救不回,提前止损不空转
    }
    const tail = tried.length ? `(已试自愈:${tried.join(" → ")},仍未回稳)` : "";
    if (await tryRestartAndReset("多轮自愈仍未回稳")) return { ok: true };
    log("  复位后首页未停稳:多轮反复自愈仍未回稳" + tail);
    return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: `复位后首页未停稳(问候标题未就绪)${tail}:跳过执行以免在未就绪首页上空跑`, duration_ms: 1 } };
  } catch (e) {
    return blocked(`就绪检查失败：${e.message || e}`);
  }
}
