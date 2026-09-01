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
