import { test } from "node:test";
import assert from "node:assert/strict";
import { pickCoreKeys, failedCoreKeys } from "./core-keys.mjs";

// pickCoreKeys:从注册表 json 取核心 key 清单;缺失/非数组 → []。
test("pickCoreKeys:读 json.coreKeys", () => {
  assert.deepEqual(pickCoreKeys({ coreKeys: ["a", "b"] }), ["a", "b"]);
});

test("pickCoreKeys:无 coreKeys / 非数组 → []", () => {
  assert.deepEqual(pickCoreKeys({}), []);
  assert.deepEqual(pickCoreKeys({ coreKeys: "x" }), []);
  assert.deepEqual(pickCoreKeys(null), []);
});

// failedCoreKeys:给定核心 key 清单 + verify 结果(key->bool),返回不可见的核心 key(告警用)。
test("failedCoreKeys:挑出 verify=false 的核心 key", () => {
  const failed = failedCoreKeys(["home", "nav", "login"], { home: true, nav: false, login: false });
  assert.deepEqual(failed, ["nav", "login"]);
});

test("failedCoreKeys:verify 缺某 key(未探到)也算失效", () => {
  const failed = failedCoreKeys(["home", "nav"], { home: true });
  assert.deepEqual(failed, ["nav"], "verify 里没有的核心 key 视作失效");
});

test("failedCoreKeys:全可见 → []", () => {
  assert.deepEqual(failedCoreKeys(["home"], { home: true }), []);
});

test("failedCoreKeys:空清单 → []", () => {
  assert.deepEqual(failedCoreKeys([], { home: false }), []);
});
