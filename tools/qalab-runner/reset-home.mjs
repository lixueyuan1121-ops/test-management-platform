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

// 复位 + 掉登录检测 + 首页就绪门禁,产出「放行或阻塞」决策(接后端 L2 的 blocked 归类)。
// - 复位(reload+就绪)重试仍失败 → { ok:false, result }:环境问题,fail_kind=selector,不计功能失败率。
// - 复位成功但检测到登录弹窗可见(会话过期)→ { ok:false, result }:提示执行机需重新登录。
// - 复位成功、未掉登录,但注册表登记的首页锚点在超时内始终不可见 → { ok:false, result }:首页没停稳,
//   不空跑(避免在未就绪首页上跑 wait_for 产生瞬态 fail);记 blocked(fail_kind=selector)。
// - 注册表未登记任何首页/登录锚点(无从判断就绪)或探测本身抛错(probe 基建问题)→ { ok:true }:
//   尽力而为放行(与 gui-core resetHome 缺 readyKey 时跳过就绪等的宽容语义一致)。
// - 否则 → { ok:true }:放行执行本条用例。
// 门禁只探「当前注册表里登记了的」锚点(见 registeredKeys),并对首页锚点做带超时轮询 —— reload 后
// 首页(改版 home)需要一点时间渲染,轮询给足就绪时间,避免探太早误判没停稳。
export async function resetOrBlock(gui, log = () => {}, { readyTimeout = 8000, pollMs = 300 } = {}) {
  if (!(await resetHomeWithRetry(gui, log))) {
    return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "用例前复位(reload)失败,跳过执行以免脏态污染", duration_ms: 1 } };
  }
  const loginKeys = registeredKeys(gui, LOGIN_MODAL_KEYS);
  const homeKeys = registeredKeys(gui, HOME_READY_KEYS);
  // 注册表未登记任何登录/首页锚点 → 无从判断就绪,尽力而为放行(不阻塞)。
  if (!loginKeys.length && !homeKeys.length) return { ok: true };
  try {
    const deadline = Date.now() + readyTimeout;
    const probe = [...loginKeys, ...homeKeys];
    for (;;) {
      const { verify } = (await gui.verifyKeys?.(probe)) || { verify: {} };
      const v = verify || {};
      // 掉登录优先:任一登录弹窗可见 → 立即阻塞(不等首页)。
      if (loginKeys.some((k) => v[k])) {
        log("  复位后检测到登录弹窗:会话可能过期");
        return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "复位后检测到登录弹窗:执行机会话可能已过期,请在执行机重新登录被测客户端", duration_ms: 1 } };
      }
      // 首页锚点:无锚点可探 或 任一可见 → 首页已停稳,放行。
      if (!homeKeys.length || homeKeys.some((k) => v[k])) return { ok: true };
      if (Date.now() >= deadline) break;
      await new Promise((r) => setTimeout(r, pollMs));
    }
    // 首页锚点已登记但超时仍不可见 = 首页没停稳 → 阻塞,不空跑。
    log("  复位后首页问候标题未就绪:首页可能没停稳");
    return { ok: false, result: { verdict: "fail", fail_kind: "selector", reason: "复位后首页未停稳(问候标题未就绪):跳过执行以免在未就绪首页上空跑", duration_ms: 1 } };
  } catch {
    // 掉登录/就绪检测尽力而为:probe 不可用不阻断已成功的复位。
    return { ok: true };
  }
}
