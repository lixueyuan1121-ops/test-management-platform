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
