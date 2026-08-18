import { test } from "node:test";
import assert from "node:assert/strict";
import { getPath, substitute, checkAssert } from "./api-executor.mjs";

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
