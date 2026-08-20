// 用例前硬复位的重试封装(与 runner 主循环解耦,便于单测)。
// gui.resetHome() 失败重试至多 attempts 次;全失败返回 false(调用方判 fail,不空跑脏态用例)。
export async function resetHomeWithRetry(gui, log = () => {}, attempts = 2) {
  for (let i = 0; i < attempts; i++) {
    try { await gui.resetHome(); return true; }
    catch (e) { log(`  复位失败(第${i + 1}次):${e.message || e}`); }
  }
  return false;
}

// 复位 + 掉登录检测 + 首页就绪门禁,产出「放行或阻塞」决策(接后端 L2 的 blocked 归类)。
// - 复位(reload+就绪)重试仍失败 → { ok:false, result }:环境问题,fail_kind=selector,不计功能失败率。
// - 复位成功但 reload 后检测到 loginModal 可见(会话过期)→ { ok:false, result }:提示执行机需重新登录。
// - 复位成功、未掉登录,但首页锚点(homepageTitle)未可见 → { ok:false, result }:首页(改版 home)
//   没停稳就放行会产生「运行时首页没停稳」的瞬态 fail(connect()/resetHome 只等到了某个 iframe,
//   问候标题还没渲染);此处硬门禁,记 blocked(fail_kind=selector),不空跑未停稳的用例。
// - 否则 → { ok:true }:放行执行本条用例。
// 一次 verifyKeys(["loginModal","homepageTitle"]) 探两项(复用 gui-core 同一定位引擎);探测本身
// 抛错(probe 不可用属基建问题)→ 不阻断已成功的复位,照旧放行(与掉登录检测同为尽力而为)。
export async function resetOrBlock(gui, log = () => {}) {
  if (!(await resetHomeWithRetry(gui, log))) {
    return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "用例前复位(reload)失败,跳过执行以免脏态污染", duration_ms: 1 } };
  }
  try {
    const { verify } = (await gui.verifyKeys?.(["loginModal", "homepageTitle"])) || { verify: {} };
    if (verify && verify.loginModal) {
      log("  复位后检测到登录弹窗:会话可能过期");
      return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "复位后检测到登录弹窗:执行机会话可能已过期,请在执行机重新登录被测客户端", duration_ms: 1 } };
    }
    // 掉登录已排除,若首页锚点仍不可见 = 首页没停稳(改版 home 未渲染出问候标题)→ 阻塞,不空跑。
    if (verify && verify.homepageTitle === false) {
      log("  复位后首页问候标题未就绪:首页可能没停稳");
      return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "复位后首页未停稳(问候标题未就绪):跳过执行以免在未就绪首页上空跑", duration_ms: 1 } };
    }
  } catch { /* 掉登录/就绪检测尽力而为,probe 不可用不阻断已成功的复位 */ }
  return { ok: true };
}
