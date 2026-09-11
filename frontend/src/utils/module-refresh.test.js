import { test } from "node:test";
import assert from "node:assert/strict";
import { keysOfModule, staleKeysFromVerify } from "./module-refresh.js";

test("keysOfModule: 按 page 精确筛该模块的 key", () => {
  const rows = [
    { key: "a", page: "自动化" }, { key: "b", page: "自动化" },
    { key: "c", page: "会话" }, { key: "d", page: "" },
  ];
  assert.deepEqual(keysOfModule(rows, "自动化").map((r) => r.key), ["a", "b"]);
  assert.deepEqual(keysOfModule(rows, "会话").map((r) => r.key), ["c"]);
});

test("keysOfModule: 空 page 不匹配任何具体模块", () => {
  assert.deepEqual(keysOfModule([{ key: "d", page: "" }], ""), []);
});

test("staleKeysFromVerify: 取该模块里 verify 失效(false)的 key", () => {
  const verify = { a: true, b: false, c: false, x: false }; // x 不在本模块
  const moduleKeys = [{ key: "a" }, { key: "b" }, { key: "c" }];
  assert.deepEqual(staleKeysFromVerify(verify, moduleKeys).sort(), ["b", "c"]);
});

test("staleKeysFromVerify: 无 verify 结果 → 空", () => {
  assert.deepEqual(staleKeysFromVerify(null, [{ key: "a" }]), []);
});
