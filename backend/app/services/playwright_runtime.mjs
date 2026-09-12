// 镜像后端 selector_ranking.py；现场唯一性由探测/runtime 验证。
export const FRAGILE_BYS = new Set(['text', 'role'])
const VALID_BYS = new Set(['testid', 'xpath', 'role', 'label', 'text', 'placeholder', 'css'])
export function normalizeCandidate(c) {
  if (!c || !VALID_BYS.has(c.by) || typeof c.value !== 'string' || !c.value.trim()
      || ('name' in c && typeof c.name !== 'string') || ('exact' in c && typeof c.exact !== 'boolean')) return null
  return Object.fromEntries(['by', 'value', 'name', 'exact', 'src', 'status', 'disabled']
    .filter(k => k in c).map(k => [k, c[k]]))
}
export const candidateIdentity = c => {
  const match = c.by === 'css' && c.value?.match(/^\[data-testid=("(?:[^"\\]|\\.)*"|[\w-]+)\]$/)
  if (match) { try { c = { ...c, by: 'testid', value: match[1].startsWith('"') ? JSON.parse(match[1]) : match[1] } } catch {} }
  return JSON.stringify([c.by, c.value, c.name || null, c.exact ?? false])
}
export const isActiveCandidate = c => !!normalizeCandidate(c) && c.src !== 'learned'
  && !['pending', 'rejected', 'retired'].includes(c.status) && c.disabled !== true
export function isFragile(c) {
  return (c?.by === 'role' && !(c.name && c.exact)) || (c?.by === 'text' && !c.exact)
}
export function candidateRank(c) {
  if (c.by === 'testid') return 0
  if (c.by === 'role' && c.name && c.exact) return 1
  if (c.by === 'label') return 2
  if (c.by === 'placeholder') return 3
  if (c.by === 'css' && (c.value.startsWith('#') || c.value.startsWith('[data-test'))) return 4
  if (c.by === 'text' && c.exact) return 5
  if (c.by === 'role' && c.name) return 6
  if (c.by === 'xpath') return 7
  if (c.by === 'css') return 8
  return 9
}
export const orderCandidates = cands => [...(cands || [])].sort((a, b) => candidateRank(a) - candidateRank(b))
export function mergeCandidates(...groups) {
  const unique = new Map()
  for (const c of groups.flat().map(normalizeCandidate).filter(Boolean)) {
    const id = candidateIdentity(c)
    if (!unique.has(id)) unique.set(id, c)
  }
  return orderCandidates([...unique.values()]).slice(0, 6)
}

export function elementTextValue(el) {
  if (!el) return "";
  const tag = String(el.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    // 表单控件:值在 .value(空串是合法值,直接返回,不回落 textContent 以免读到无关文本)
    return el.value == null ? "" : String(el.value);
  }
  return el.textContent == null ? "" : String(el.textContent);
}

// Find the response contract before the last submit-capable action. Non-action
// waits between submission and wait_response do not replace the baseline.
export function responseArgsBeforeAction(script, index) {
  if (!["click", "press"].includes(script[index]?.action)) return null;
  for (let i = index + 1; i < script.length; i++) {
    if (script[i].action === "wait_response") return script[i].args || {};
    if (["click", "press", "goto"].includes(script[i].action)) return null;
  }
  return null;
}

// Shared by the GUI runner and standalone exports. No Node or browser globals:
// the caller supplies a Playwright Page. Keep execution semantics in this file.
export function createAutomationRuntime({ page: getPage, registry: getRegistry, vmIframe: getVmIframe = "", timeout = 10000, pollMs = 100 } = {}) {
  const page = () => typeof getPage === "function" ? getPage() : getPage;
  const registry = () => typeof getRegistry === "function" ? getRegistry() : getRegistry;
  const vmIframe = () => typeof getVmIframe === "function" ? getVmIframe() : getVmIframe;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const error = (code, message) => Object.assign(new Error(message), { code, fail_kind: "selector" });
  const limit = (args = {}) => {
    const n = args.timeout_ms ?? timeout;
    if (!Number.isFinite(n) || n < 0) throw error("INVALID_TIMEOUT", "timeout_ms 必须是非负数");
    return n;
  };
  const validBys = new Set(["testid", "xpath", "role", "text", "label", "placeholder", "css"]);
  const candidates = (entry) => {
    const c = (entry?.candidates || []).filter(isActiveCandidate);
    if (!c.length) throw error("INVALID_SELECTOR", "选择器没有有效候选");
    return orderCandidates(c);
  };
  function locator(scope, cand) {
    const exact = typeof cand.exact === "boolean" ? { exact: cand.exact } : {};
    switch (cand.by) {
      case "testid": return scope.getByTestId(cand.value);
      case "xpath": return scope.locator(cand.value.startsWith("xpath=") ? cand.value : `xpath=${cand.value}`);
      case "role": return scope.getByRole(cand.value, { ...exact, ...(cand.name ? { name: cand.name } : {}) });
      case "text": return scope.getByText(cand.value, exact);
      case "label": return scope.getByLabel(cand.value, exact);
      case "placeholder": return scope.getByPlaceholder(cand.value, exact);
      default: return scope.locator(cand.value);
    }
  }
  async function scopes(frame) {
    const shell = { scope: page(), name: "shell" };
    if (frame === "shell") return [shell];
    if (typeof frame === "string" && frame.startsWith("url:")) {
      const pattern = frame.slice(4);
      if (!pattern) throw error("INVALID_FRAME", "url: 必须指定 frame URL 子串");
      const frames = page().frames().filter((f) => f.url().includes(pattern));
      if (frames.length !== 1) throw error("INVALID_FRAME", `frame ${frame} 匹配 ${frames.length} 个目标，必须唯一`);
      return [{ scope: frames[0], name: frame }];
    }
    let vm = null;
    if (vmIframe()) {
      const count = await page().locator(vmIframe()).count();
      if (count > 1) throw error("AMBIGUOUS_FRAME", `业务 iframe 匹配 ${count} 个目标`);
      if (count === 1) vm = { scope: page().frameLocator(vmIframe()), name: "vm" };
    }
    // A product may flatten the business page into the main renderer.
    if (frame === "vm" || frame === "content") {
      if (vmIframe() && !vm) throw error('INVALID_FRAME', '已配置的业务 iframe 尚未出现');
      return [vm || shell];
    }
    return vm ? [shell, vm] : [shell];
  }
  function validateTarget(target, depth = 0) {
    if (!target || typeof target !== "object" || !(target.key || target.selector)) throw error("INVALID_TARGET", "需要 key 或 selector");
    if (depth > 3) throw error("INVALID_TARGET", "within 最多嵌套三层");
    if (target.nth !== undefined && (!Number.isInteger(target.nth) || target.nth < 0)) throw error("INVALID_TARGET", "nth 必须是非负整数");
    if (target.has_text !== undefined && typeof target.has_text !== "string") throw error("INVALID_TARGET", "has_text 必须是字符串");
    if (target.key && !registry()?.[target.key]) throw error("UNKNOWN_KEY", `未定义语义 key "${target.key}"`);
    if (target.within) validateTarget(target.within, depth + 1);
  }
  async function inspect(target, { multiple = false, parent = null, all = false, requireVisible = false } = {}) {
    validateTarget(target);
    let container = parent;
    if (target.within) {
      const p = await inspect(target.within, { requireVisible });
      if (!p.count) return { count: 0, loc: null, containerMissing: true, hit: p.hit };
      container = p.loc;
    }
    const entry = target.key ? registry()[target.key] : null;
    const cands = entry ? candidates(entry) : [{ by: "css", value: target.selector }];
    const locations = container ? [{ scope: container, name: "within" }] : await scopes(target.frame ?? entry?.frame ?? "content");
    let empty, hidden, ambiguous;
    const matches = [];
    // 候选优先级先于 frame 遍历顺序。auto 的唯一性在全部允许的 frame 上检查。
    for (const cand of cands) {
      const hits = [];
      for (const s of locations) {
        let loc = locator(s.scope, cand);
        if (target.has_text !== undefined) loc = loc.filter({ hasText: target.has_text });
        if (target.visible !== undefined) loc = loc.filter({ visible: !!target.visible });
        if (target.nth !== undefined) loc = loc.nth(target.nth);
        const count = await loc.count(); // 无效语法/失联 frame 不能变成“元素不存在”。
        const hit = { scope: s.name, ...normalizeCandidate(cand), ...(target.nth !== undefined ? { nth: target.nth } : {}) };
        const result = { loc, count, hit };
        empty ||= result;
        if (count) hits.push(result);
      }
      const total = hits.reduce((n, r) => n + r.count, 0);
      if (all) { matches.push(...hits); continue; }
      if (!multiple && total > 1) {
        ambiguous ||= error("AMBIGUOUS_TARGET", `目标 ${target.key || target.selector} 在允许的 frame 中匹配 ${total} 个元素；请限定 frame/within/has_text`);
        continue; // 当前候选不唯一，不妨碍后续更精确的候选唯一定位。
      }
      if (total) {
        const result = hits[0];
        if (requireVisible && !await result.loc.isVisible()) { hidden ||= result; continue; }
        return result;
      }
    }
    if (all) return { ...empty, matches, count: matches.reduce((n, r) => n + r.count, 0) };
    if (hidden) return hidden;
    if (ambiguous) throw ambiguous;
    return empty;
  }

  async function resolve(target, { requireVisible = true } = {}) {
    const end = Date.now() + limit(target);
    for (;;) {
      const r = await inspect(target, { requireVisible });
      if (r.count && (!requireVisible || await r.loc.isVisible())) return r;
      if (Date.now() >= end) throw error("TARGET_TIMEOUT", `目标 ${target.key || target.selector} 在超时内未${requireVisible ? "可见" : "出现"}`);
      await sleep(Math.min(pollMs, Math.max(1, end - Date.now())));
    }
  }
  async function visibleCount(r) {
    if (r.matches) {
      let total = 0;
      for (const match of r.matches) total += await visibleCount(match);
      return total;
    }
    let count = 0;
    for (let i = 0; i < r.count; i++) if (await r.loc.nth(i).isVisible()) count++;
    return count;
  }
  async function check(mode, args) {
    const end = Date.now() + limit(args);
    const stableMs = Math.min(200, limit(args));
    let absentSince = null;
    let actual = null, locatable = false, hit;
    for (;;) {
      const r = await inspect(args, { multiple: mode === "absent", all: mode === "absent", requireVisible: mode !== "absent" });
      hit = r.hit;
      locatable = !!r.count;
      if (!r.count) actual = null;
      let pass = false;
      if (mode === "absent") {
        // A missing container cannot prove that the intended record was checked.
        if (r.containerMissing) throw error("CONTAINER_MISSING", "断言消失时所属记录未找到，无法完成检查");
        const gone = !(await visibleCount(r));
        absentSince = gone ? (absentSince ?? Date.now()) : null;
        pass = gone && Date.now() - absentSince >= stableMs;
      } else if (r.count) {
        if (mode === "visible") pass = await r.loc.isVisible();
        else {
          // Read a fresh non-waiting snapshot. Giving textContent the last 1ms
          // of the assertion budget can turn a normal failed assertion into an
          // infrastructure timeout, even when the node exists.
          const texts = await r.loc.evaluateAll((elements) => elements.map((el) => {
            // Browser snapshots must also preserve upstream form-value assertions.
            const isInput = ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
            return String((isInput ? el.value : el.textContent) ?? "").trim();
          }));
          if (texts.length > 1) throw error("AMBIGUOUS_TARGET", "断言过程中目标变为多个匹配");
          actual = texts[0] ?? null;
          locatable = texts.length === 1;
          if (locatable) {
            const matched = args.contains ? actual.includes(String(args.expected)) : actual === String(args.expected);
            pass = args.negate ? !matched : matched;
          }
        }
      }
      const result = { pass, locatable, actual: actual?.slice(0, 200) ?? null, expected: args.expected, mode: args.contains ? "contains" : "equals", negate: !!args.negate, via: hit, fail_kind: "business" };
      if (pass || Date.now() >= end) return result;
      await sleep(Math.min(pollMs, Math.max(1, end - Date.now())));
    }
  }
  let responseBase = null;
  function responseKeys(args = {}) {
    const choose = (explicit, aliases) => {
      if (explicit) { validateTarget({ key: explicit }); return explicit; }
      return aliases.find((k) => registry()?.[k]);
    };
    const stop = choose(args.stop_key, ["stopBtn", "abortButton"]);
    // Both default completion markers belong to AI messages, not user bubbles.
    const done = choose(args.complete_key, ["answerBubble", "chatMsgActions"]);
    if (!done) throw error("RESPONSE_CONFIG", "缺少回复完成标志：请配置 complete_key（如 chatMsgActions/answerBubble）");
    return { stop, done };
  }
  async function responseSnapshot(keys) {
    const done = await inspect({ key: keys.done }, { multiple: true });
    const stop = keys.stop ? await inspect({ key: keys.stop }, { multiple: true }) : { count: 0 };
    return {
      doneCount: done.count,
      latestVisible: done.count > 0 && await done.loc.nth(done.count - 1).isVisible(),
      generating: !!(await visibleCount(stop)),
    };
  }
  async function captureResponse(args = {}) {
    responseBase = null;
    const keys = responseKeys(args);
    const snapshot = await responseSnapshot(keys);
    if (snapshot.generating) throw error("RESPONSE_BUSY", "上一轮仍在生成，不能建立本轮回复基线");
    responseBase = { keys, ...snapshot };
    return { baseline: snapshot.doneCount, keys };
  }
  async function waitResponse(args = {}) {
    const baseline = responseBase;
    responseBase = null; // Never reuse a previous round after timeout or failure.
    if (!baseline) throw error("RESPONSE_BASELINE", "等待回复前需在提交动作之前调用 captureResponse");
    const keys = responseKeys(args);
    if (keys.done !== baseline.keys.done || keys.stop !== baseline.keys.stop) throw error("RESPONSE_CONFIG", "回复等待配置与提交前基线不一致");
    const start = Date.now(), end = start + limit({ timeout_ms: args.timeout_ms ?? 90000 });
    let sawGenerating = false, completeSince = null;
    const stableMs = args.stable_ms ?? 300;
    if (!Number.isFinite(stableMs) || stableMs < 0) throw error("INVALID_TIMEOUT", "stable_ms 必须是非负数");
    for (;;) {
      const s = await responseSnapshot(keys);
      sawGenerating ||= s.generating;
      const complete = !s.generating && s.latestVisible && s.doneCount > baseline.doneCount;
      completeSince = complete ? (completeSince ?? Date.now()) : null;
      if (complete && Date.now() - completeSince >= stableMs) return { done: true, elapsed_ms: Date.now() - start, saw_generating: sawGenerating };
      if (Date.now() >= end) return { done: false, elapsed_ms: Date.now() - start, reason: "超时：未观察到本轮新增的回复完成标志" };
      await sleep(Math.min(pollMs, Math.max(1, end - Date.now())));
    }
  }
  async function fill(args) {
    const { loc, hit } = await resolve(args);
    const text = String(args.text ?? "");
    try { await loc.fill(text, { timeout: Math.max(1, limit(args)) }); }
    catch (e) {
      if (!/not an\b|contenteditable|is not an <input>|Element is not/i.test(e.message || "")) throw e;
      const inner = loc.locator('[contenteditable="true"], [contenteditable=""], textarea, input');
      const count = await inner.count();
      if (count !== 1) throw error("INVALID_INPUT", `自定义输入组件包含 ${count} 个可编辑目标，需明确定位`);
      await inner.fill(text, { timeout: Math.max(1, limit(args)) });
    }
    return { filled: args.key || args.selector, via: hit };
  }
  async function pressKey(args) {
    if (!args.key_name) throw error("INVALID_KEY", "press 缺少 key_name");
    const target = args.target_key ? { ...args, key: args.target_key } : args;
    if (target.key || target.selector) {
      const { loc } = await resolve(target);
      await loc.press(args.key_name, { timeout: Math.max(1, limit(args)) });
    } else await page().keyboard.press(args.key_name);
    return { pressed: args.key_name };
  }
  const mocks = new Map();
  async function unmockRoute(args) {
    const rec = mocks.get(args.url);
    if (!rec) return { unrouted: args.url, hits: 0, registered: false };
    await rec.context.unroute(rec.matcher, rec.handler);
    mocks.delete(args.url);
    return { unrouted: args.url, hits: rec.stat.hits, registered: true };
  }
  async function unmockAll() {
    const stats = [];
    for (const url of [...mocks.keys()]) stats.push(await unmockRoute({ url }));
    return { unrouted: stats };
  }
  async function mockRoute(args) {
    if (!args.url) throw error("INVALID_MOCK", "mock_route 缺少 url");
    await unmockRoute(args);
    const context = page().context();
    const matcher = toUrlMatcher(args.url);
    const stat = { pattern: args.url, status: Number(args.status ?? 200), hits: 0 };
    const handler = async (route) => {
      await route.fulfill(buildMockResponse(args, route.request().headers()));
      stat.hits++;
    };
    await context.route(matcher, handler);
    mocks.set(args.url, { context, matcher, handler, stat });
    return { mocked: args.url, status: stat.status };
  }
  return {
    mockRoute, unmockRoute, unmockAll,
    mockStats: () => [...mocks.values()].map((r) => ({ ...r.stat })),
    inspect, resolve, captureResponse, waitResponse, fill, pressKey,
    resetResponse() { responseBase = null; },
    resetConnection() { responseBase = null; mocks.clear(); },
    async isKeyVisible(key) { return !!(await visibleCount(await inspect({ key }, { multiple: true }))); },
    assertText: (args) => {
      if (typeof args.expected !== "string") throw error("INVALID_EXPECTED", "expected 必须是字符串");
      return check("text", args);
    },
    assertVisible: (args) => check("visible", args),
    assertAbsent: (args) => check("absent", args),
    async click(args) { const r = await resolve(args); await r.loc.click({ timeout: Math.max(1, limit(args)) }); return { clicked: args.key || args.selector, via: r.hit }; },
    async setChecked(args) {
      if (typeof args.checked !== 'boolean') throw error('INVALID_CHECKED', 'checked 必须是布尔值');
      const r = await resolve(args);
      await r.loc.setChecked(args.checked, { timeout: Math.max(1, limit(args)) });
      return { checked: args.checked, via: r.hit };
    },
    async selectOption(args) {
      if (!(typeof args.values === 'string' || (Array.isArray(args.values) && args.values.every(v => typeof v === 'string')))) throw error('INVALID_OPTION', 'values 必须是字符串或字符串数组');
      const r = await resolve(args);
      return { selected: await r.loc.selectOption(args.values, { timeout: Math.max(1, limit(args)) }), via: r.hit };
    },
    async hover(args) { const r = await resolve(args); await r.loc.hover({ timeout: Math.max(1, limit(args)) }); return { hovered: args.key || args.selector, via: r.hit }; },
    async type(args) { const r = await resolve(args); await r.loc.pressSequentially(String(args.text ?? ""), { timeout: Math.max(1, limit(args)) }); return { typed: args.key || args.selector, via: r.hit }; },
    async getText(args) { const r = await resolve(args, { requireVisible: false }); return { text: await r.loc.evaluate(elementTextValue, undefined, { timeout: Math.max(1, limit(args)) }), via: r.hit }; },
    async waitFor(args) { const r = await resolve(args); return { visible: args.key || args.selector, via: r.hit }; },
  };
}

// mock-route —— mock_route 的两块纯逻辑:URL 模式匹配 + 响应构造。
// 抽成独立模块(不碰 Playwright)是为了能单测——这两处正是「mock 写了却不生效」的两个根因所在。
//
// 根因①「glob 必须匹配整个 URL」:Playwright 的 route(url) 用 glob 匹配**完整 URL**,
//   模型按 prompt 生成的 `**/api/tasks` 匹配不上真实请求 `https://h/api/tasks?project_id=1`
//   (前端列表接口几乎都带 query),拦截器注册成功却永不触发,mock 静默失效。
//   → toUrlMatcher 自己把 glob 编译成正则,并在模式未显式写 query/hash 时追加可选的 `(?:[?#].*)?`,
//     让「路径相同、只多了查询串」的请求照样命中;路径本身不放宽(子路径/别的接口仍不命中)。
//
// 根因②「fulfill 的响应没有 CORS 头」:Playwright 只给 CORS 预检(OPTIONS)自动补允许头,
//   用户 route.fulfill 出去的**正式响应**不补。被测前端与后端常不同源(本平台 :80 页面调 :8000 接口),
//   缺 Access-Control-Allow-Origin 时浏览器直接拦掉这条响应,页面拿到的是网络错误而非 mock 数据。
//   → buildMockResponse 回显请求 Origin(有 Origin 才配 credentials:通配 * 与凭证互斥)。

// glob 元字符 → 正则。`**` 跨 /;`*` 单段且不吃进 query/hash;`{a,b}` 择一;其余字面量转义。
function globToRegexSource(glob) {
  let src = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") { src += ".*"; while (glob[i + 1] === "*") i++; }
      else src += "[^/?#]*";
      continue;
    }
    if (c === "{") { src += "(?:"; continue; }
    if (c === "}") { src += ")"; continue; }
    if (c === ",") { src += "|"; continue; }
    src += c.replace(/[.+?^${}()|[\]\\]/g, "\\$&");
  }
  return src;
}

// 把 mock_route 的 args.url(glob)编译成匹配完整请求 URL 的正则。
// - 裸路径(以 / 开头、无协议)按 ** + 路径处理:模型常写 /api/tasks,不该静默失配。
// - 模式里没写 ? / # 时追加可选的 query/hash 后缀 —— 这是根因①的修复点。
//   模式里显式写了 query(如 **/api/tasks?page=1)则严格匹配,尊重调用方的精确意图。
export function toUrlMatcher(pattern) {
  const glob = String(pattern || "");
  const normalized = glob.startsWith("/") ? `**${glob}` : glob;
  const explicitQuery = normalized.includes("?") || normalized.includes("#");
  const tail = explicitQuery ? "" : "(?:[?#].*)?";
  return new RegExp(`^${globToRegexSource(normalized)}${tail}$`);
}

// 按 mock_route 的 args + 请求头构造 route.fulfill 的入参({status, body, headers})。
// body 为字符串时原样透传(模型有时直接给序列化好的 JSON,再 stringify 一次前端拿到的是字符串不是对象);
// 其余类型 JSON 序列化。headers 一律小写归一,调用方可用 args.headers 覆盖任意头(含 CORS)。
export function buildMockResponse(args = {}, requestHeaders = {}) {
  const status = Number(args.status ?? 200);
  const body = typeof args.body === "string" ? args.body : JSON.stringify(args.body ?? {});
  const origin = requestHeaders.origin || requestHeaders.Origin || "";
  const headers = {
    "content-type": String(args.content_type || args.contentType || "application/json"),
    // 跨域时必须回显具体 Origin:通配 * 在 credentials 请求下会被浏览器判非法。无 Origin 才用 *。
    "access-control-allow-origin": origin || "*",
  };
  if (origin) headers["access-control-allow-credentials"] = "true";
  for (const [k, v] of Object.entries(args.headers || {})) headers[String(k).toLowerCase()] = String(v);
  return { status, body, headers };
}
