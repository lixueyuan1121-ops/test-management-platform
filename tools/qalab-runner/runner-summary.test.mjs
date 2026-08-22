import { test } from "node:test";
import assert from "node:assert/strict";
import { summarizeBatch } from "./runner-summary.mjs";

// summarizeBatch:把本次 tick 执行的一批结果汇总成一行结束语。
// verdict:pass=通过、fail=功能失败、blocked=选择器/环境阻塞(runner 回写时 selector 已映射成 blocked)。

test("全通过", () => {
  const s = summarizeBatch([{ verdict: "pass" }, { verdict: "pass" }]);
  assert.equal(s.total, 2);
  assert.equal(s.passed, 2);
  assert.equal(s.failed, 0);
  assert.equal(s.blocked, 0);
  assert.match(s.text, /本批完成/);
  assert.match(s.text, /2 条/);
  assert.match(s.text, /2 过/);
});

test("混合:过/失/阻塞各计数", () => {
  const s = summarizeBatch([
    { verdict: "pass" }, { verdict: "fail" }, { verdict: "blocked" }, { verdict: "fail" },
  ]);
  assert.equal(s.total, 4);
  assert.equal(s.passed, 1);
  assert.equal(s.failed, 2);
  assert.equal(s.blocked, 1);
  assert.match(s.text, /1 过/);
  assert.match(s.text, /2 失/);
  assert.match(s.text, /1 阻塞/);
});

test("fail_kind=selector 也算 blocked(兼容未映射的结果)", () => {
  const s = summarizeBatch([{ verdict: "fail", fail_kind: "selector" }]);
  assert.equal(s.blocked, 1);
  assert.equal(s.failed, 0);
});

test("耗时求和(ms→秒,1 位小数)", () => {
  const s = summarizeBatch([{ verdict: "pass", duration_ms: 1500 }, { verdict: "pass", duration_ms: 500 }]);
  assert.match(s.text, /2\.0s/);
});

test("空批 → total 0,无阻塞段", () => {
  const s = summarizeBatch([]);
  assert.equal(s.total, 0);
  assert.doesNotMatch(s.text, /阻塞/);
});

test("无阻塞时结束语不含阻塞段", () => {
  const s = summarizeBatch([{ verdict: "pass" }, { verdict: "fail" }]);
  assert.doesNotMatch(s.text, /阻塞/);
});
