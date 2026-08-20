import { test } from "node:test";
import assert from "node:assert/strict";
import { resetHomeWithRetry } from "./reset-home.mjs";

test("首次成功 → true,只调 1 次", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 1);
});

test("首次失败、二次成功 → true", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; if (n === 1) throw new Error("reload 超时"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 2);
});

test("两次都失败 → false", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; throw new Error("客户端未响应"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), false);
  assert.equal(n, 2);
});
