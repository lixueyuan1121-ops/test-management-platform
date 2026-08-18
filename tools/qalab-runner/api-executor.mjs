// api-executor —— 按结构化 api script 确定性执行(请求-断言-提取原子)。
// 纯 Node fetch,不经 LLM。镜像 step-executor.mjs 的返回契约。
// script 形状(设计稿 §5.1):[{ name, request:{method,path,headers?,query?,body?},
//   asserts:[{type,path?,op,value?}], extract?:{var:"点路径"}, cleanup?:bool }, ...]

// 点路径取值:"data.list.0.id" → 逐段下钻;任一段不存在返回 undefined。
export function getPath(obj, path) {
  if (obj == null || !path) return undefined;
  let cur = obj;
  for (const seg of String(path).split(".")) {
    if (cur == null) return undefined;
    cur = cur[seg];
  }
  return cur;
}

// 深度替换 {{var}}。整串恰为单个 {{var}} 时保留 vars 原类型(数字/布尔);
// 否则做字符串插值。未定义变量替换为空串(执行期宽松;闭环校验在生成侧)。
export function substitute(value, vars) {
  if (typeof value === "string") {
    const whole = value.match(/^\{\{(\w+)\}\}$/);
    if (whole) return vars[whole[1]] !== undefined ? vars[whole[1]] : "";
    return value.replace(/\{\{(\w+)\}\}/g, (_, k) => (vars[k] !== undefined ? String(vars[k]) : ""));
  }
  if (Array.isArray(value)) return value.map((v) => substitute(v, vars));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = substitute(v, vars);
    return out;
  }
  return value;
}

// 判一条断言。返回 {ok, actual}。type: status | jsonpath;op 见下。
export function checkAssert(a, statusCode, body) {
  const actual = a.type === "status" ? statusCode : getPath(body, a.path);
  const v = a.value;
  let ok = false;
  switch (a.op) {
    case "eq": ok = actual === v; break;
    case "neq": ok = actual !== v; break;
    case "exists": ok = actual !== undefined && actual !== null; break;
    case "contains":
      ok = typeof actual === "string" ? actual.includes(String(v))
         : Array.isArray(actual) ? actual.includes(v) : false;
      break;
    case "gt": ok = typeof actual === "number" && actual > v; break;
    case "lt": ok = typeof actual === "number" && actual < v; break;
    case "regex": { try { ok = new RegExp(v).test(String(actual)); } catch { ok = false; } break; }
    case "type": {
      const t = actual === null ? "null" : Array.isArray(actual) ? "array" : typeof actual;
      ok = t === v; break;
    }
    default: ok = false;
  }
  return { ok, actual };
}

// 主执行:顺序跑普通步,失败即短路;最后逆序执行 cleanup 步(尽力而为,不计入 verdict)。
// 返回契约与 step-executor 对齐:{verdict, reason, evidence, duration_ms, steps} 或 {needClaude, reason}。
export async function run(script, apiEnv, log = () => {}, fetchImpl = fetch) {
  if (!Array.isArray(script) || script.length === 0) {
    return { needClaude: true, reason: "用例无结构化 api script,退回 claude 执行" };
  }
  const baseUrl = String((apiEnv && apiEnv.base_url) || "").replace(/\/$/, "");
  if (!baseUrl) {
    return { verdict: "fail", reason: "项目未配置 api 环境(base_url 为空),无法执行 api 用例", evidence: null, duration_ms: 0, steps: [] };
  }
  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);
  const vars = {};
  // fixed 鉴权:预置固定 header,注入每个请求(login 模式靠用例内登录步骤 extract token)。
  const fixedHeaders = (apiEnv.auth_type === "fixed" && apiEnv.auth && apiEnv.auth.headers) ? apiEnv.auth.headers : {};

  const steps = [];
  const normals = [];
  const cleanups = [];
  script.forEach((st, i) => (st && st.cleanup ? cleanups : normals).push({ st, i }));

  let failed = null;  // 首个失败的诊断
  // 执行单步:替换变量→发请求→判断言→提取。返回 {ok, reason?}。
  async function exec({ st, i }, isCleanup) {
    const name = st.name || `step${i + 1}`;
    const req = substitute(st.request || {}, vars);
    const headers = { "Content-Type": "application/json", ...fixedHeaders, ...(req.headers || {}) };
    let url = baseUrl + (req.path || "");
    if (req.query && typeof req.query === "object") {
      const qs = new URLSearchParams(Object.entries(req.query).map(([k, v]) => [k, String(v)])).toString();
      if (qs) url += (url.includes("?") ? "&" : "?") + qs;
    }
    const method = String(req.method || "GET").toUpperCase();
    const hasBody = req.body !== undefined && method !== "GET";
    log(`  [+${sec()}s] ▶ ${isCleanup ? "[cleanup] " : ""}${name} ${method} ${req.path}`);
    let statusCode, body;
    try {
      const res = await fetchImpl(url, { method, headers, body: hasBody ? JSON.stringify(req.body) : undefined });
      statusCode = res.status;
      try { body = await res.json(); } catch { body = null; }
    } catch (e) {
      return { ok: false, reason: `${name} 请求异常: ${e.message}` };
    }
    // 断言
    for (const a of st.asserts || []) {
      const { ok, actual } = checkAssert(a, statusCode, body);
      if (!ok) {
        const tgt = a.type === "status" ? "status" : `jsonpath ${a.path}`;
        return { ok: false, reason: `${name} 断言失败: ${tgt} 期望 ${a.op} ${a.value ?? ""},实际 ${JSON.stringify(actual)}` };
      }
    }
    // 提取
    if (st.extract && typeof st.extract === "object") {
      for (const [k, path] of Object.entries(st.extract)) {
        const val = getPath(body, path);
        if (val === undefined) return { ok: false, reason: `${name} 提取变量 ${k} 失败: 响应无路径 ${path}` };
        vars[k] = val;
      }
    }
    steps.push({ name, method, path: req.path, status: statusCode, cleanup: !!isCleanup });
    return { ok: true };
  }

  // 普通步:顺序,失败即短路
  for (const item of normals) {
    const r = await exec(item, false);
    if (!r.ok) { failed = r.reason; break; }
  }
  // cleanup:逆序,尽力而为(失败只告警,不计入 verdict)
  for (const item of cleanups.reverse()) {
    try { const r = await exec(item, true); if (!r.ok) log(`  [+${sec()}s] ⚠ cleanup 失败(忽略): ${r.reason}`); }
    catch (e) { log(`  [+${sec()}s] ⚠ cleanup 异常(忽略): ${e.message}`); }
  }

  const duration_ms = Date.now() - started;
  if (failed) return { verdict: "fail", reason: failed, evidence: null, duration_ms, steps };
  // 只计普通步断言(cleanup 尽力而为、不计入判定,避免"全部满足"虚高)。
  const checks = normals.reduce((n, { st }) => n + ((st.asserts || []).length), 0);
  return { verdict: "pass", reason: `api 确定性执行通过: ${normals.length} 步, ${checks} 处断言全部满足`, evidence: null, duration_ms, steps };
}
