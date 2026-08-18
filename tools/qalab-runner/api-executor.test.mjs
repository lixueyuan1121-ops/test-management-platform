import { test } from "node:test";
import assert from "node:assert/strict";
import { getPath, substitute, checkAssert, run } from "./api-executor.mjs";

test("getPath 点路径含数组下标", () => {
  const body = { data: { list: [{ id: 9 }] }, code: 0 };
  assert.equal(getPath(body, "data.list.0.id"), 9);
  assert.equal(getPath(body, "code"), 0);
  assert.equal(getPath(body, "data.missing"), undefined);
  assert.equal(getPath(body, "a.b.c"), undefined);
});

test("substitute 替换 {{var}}", () => {
  const vars = { token: "abc", pid: 3 };
  assert.equal(substitute("Bearer {{token}}", vars), "Bearer abc");
  assert.equal(substitute("/api/p/{{pid}}", vars), "/api/p/3");
  assert.deepEqual(substitute({ h: "{{token}}", n: 1 }, vars), { h: "abc", n: 1 });
  // 整串即单占位 → 保留原类型(数字)
  assert.equal(substitute("{{pid}}", vars), 3);
  // 未定义变量 → 替换为空串(执行期;闭环校验在生成侧 P2)
  assert.equal(substitute("x{{nope}}y", vars), "xy");
});

test("checkAssert status/jsonpath 各 op", () => {
  const body = { code: 0, msg: "ok", data: { id: 5, name: "n" } };
  assert.equal(checkAssert({ type: "status", op: "eq", value: 200 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "status", op: "eq", value: 200 }, 500, body).ok, false);
  assert.equal(checkAssert({ type: "jsonpath", path: "code", op: "eq", value: 0 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "exists" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.x", op: "exists" }, 200, body).ok, false);
  assert.equal(checkAssert({ type: "jsonpath", path: "msg", op: "contains", value: "o" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "gt", value: 3 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "type", value: "number" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "msg", op: "regex", value: "^o" }, 200, body).ok, true);
});

// 桩 fetch:按 "METHOD /path" 返回预设响应。记录调用顺序供断言。
function stubFetch(routes, calls) {
  return async (url, opts) => {
    const u = new URL(url);
    calls.push({ method: opts.method, path: u.pathname, headers: opts.headers, body: opts.body });
    const r = routes[opts.method + " " + u.pathname];
    if (!r) return { status: 404, json: async () => ({ code: 404, msg: "no route" }) };
    return { status: r.status, json: async () => r.body };
  };
}

test("run 链式:登录取token→创建→清理,变量传递", async () => {
  const calls = [];
  const routes = {
    "POST /api/auth/login": { status: 200, body: { code: 0, data: { token: "TK" } } },
    "POST /api/projects": { status: 200, body: { code: 0, data: { id: 42 } } },
    "DELETE /api/projects/42": { status: 200, body: { code: 0 } },
  };
  const script = [
    { name: "登录", request: { method: "POST", path: "/api/auth/login", body: { u: "qa" } },
      asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }], extract: { token: "data.token" } },
    { name: "创建", request: { method: "POST", path: "/api/projects", headers: { Authorization: "Bearer {{token}}" }, body: { name: "n" } },
      asserts: [{ type: "jsonpath", path: "data.id", op: "exists" }], extract: { pid: "data.id" } },
    { name: "清理", cleanup: true, request: { method: "DELETE", path: "/api/projects/{{pid}}", headers: { Authorization: "Bearer {{token}}" } },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
  ];
  const r = await run(script, { base_url: "https://svc", auth_type: "login", auth: {} }, () => {}, stubFetch(routes, calls));
  assert.equal(r.verdict, "pass", r.reason);
  assert.equal(calls[1].headers.Authorization, "Bearer TK");
  assert.equal(calls[2].path, "/api/projects/42");
});

test("run 普通步失败即短路,但仍执行 cleanup", async () => {
  const calls = [];
  const routes = {
    "POST /api/projects": { status: 200, body: { code: 0, data: { id: 7 } } },
    "GET /api/projects/7": { status: 500, body: { code: 500, msg: "err" } },
    "DELETE /api/projects/7": { status: 200, body: { code: 0 } },
  };
  const script = [
    { name: "创建", request: { method: "POST", path: "/api/projects", body: {} },
      asserts: [{ type: "status", op: "eq", value: 200 }], extract: { pid: "data.id" } },
    { name: "查询(将失败)", request: { method: "GET", path: "/api/projects/{{pid}}" },
      asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }] },
    { name: "本不该执行的普通步", request: { method: "GET", path: "/api/never" },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
    { name: "清理", cleanup: true, request: { method: "DELETE", path: "/api/projects/{{pid}}" },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
  ];
  const r = await run(script, { base_url: "https://svc", auth_type: "fixed", auth: {} }, () => {}, stubFetch(routes, calls));
  assert.equal(r.verdict, "fail");
  assert.match(r.reason, /查询/);
  const paths = calls.map((c) => c.method + " " + c.path);
  assert.ok(!paths.includes("GET /api/never"), "失败后普通步不应执行");
  assert.ok(paths.includes("DELETE /api/projects/7"), "cleanup 应执行");
});

test("run 无 base_url 直接 fail", async () => {
  const r = await run([{ name: "x", request: { method: "GET", path: "/a" }, asserts: [{ type: "status", op: "eq", value: 200 }] }],
    { base_url: "", auth_type: "fixed", auth: {} }, () => {}, async () => ({ status: 200, json: async () => ({}) }));
  assert.equal(r.verdict, "fail");
  assert.match(r.reason, /未配置|api 环境|base_url/);
});

test("run 空 script → needClaude", async () => {
  const r = await run([], { base_url: "https://svc" }, () => {});
  assert.equal(r.needClaude, true);
});

test("run fixed 鉴权预置 header", async () => {
  const calls = [];
  const routes = { "GET /api/me": { status: 200, body: { code: 0 } } };
  const script = [{ name: "me", request: { method: "GET", path: "/api/me" },
                   asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }] }];
  await run(script, { base_url: "https://svc", auth_type: "fixed", auth: { headers: { Authorization: "Bearer FIX" } } }, () => {}, stubFetch(routes, calls));
  assert.equal(calls[0].headers.Authorization, "Bearer FIX");
});
