import { test } from "node:test";
import assert from "node:assert/strict";
import { runScript } from "./step-executor.mjs";

// 假 gui:记录 shotBuffer 调用次数;可控 assertVisible 成败。返回 Buffer 模拟截图。
function fakeGui({ visibleOk = true } = {}) {
  const calls = { shot: 0 };
  return {
    calls,
    async connect() { return { connected: true }; },
    async click() { return { clicked: true }; },
    async assertVisible() { return visibleOk ? { pass: true } : { pass: false, error: "元素不可见" }; },
    async assertText() { return { pass: true, mode: "contains", actual: "x" }; },
    async shotBuffer() { calls.shot += 1; return Buffer.from([0x89, 0x50, 0x4e, 0x47]); },
  };
}

test("pass:关键步(assert 通过)截图,report 每步 ok + shotBuf", async () => {
  const gui = fakeGui({ visibleOk: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "click", target: { key: "navTasks" }, desc: "点导航" },
    { action: "assert_visible", target: { key: "navTasks" }, desc: "看导航" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "pass", r.reason);
  assert.equal(r.report.length, 3);
  // connect/click 无截图,assert_visible 通过后截 1 张
  assert.equal(r.report[0].shotBuf, undefined);
  assert.equal(r.report[1].shotBuf, undefined);
  assert.ok(r.report[2].shotBuf && r.report[2].shotBuf.length, "关键步应带 shotBuf");
  assert.equal(r.report[2].ok, true);
  assert.equal(gui.calls.shot, 1, "只在关键步截 1 张");
});

test("fail:失败步必截 + 标 ok=false + error", async () => {
  const gui = fakeGui({ visibleOk: false });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_visible", target: { key: "x" }, desc: "看X" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  const last = r.report[r.report.length - 1];
  assert.equal(last.ok, false);
  assert.ok(last.error && /断言可见失败/.test(last.error), last.error);
  assert.ok(last.shotBuf && last.shotBuf.length, "失败步应带 shotBuf");
  assert.equal(gui.calls.shot, 1, "失败现场截 1 张");
});

test("空 script → needClaude,不产 report", async () => {
  const r = await runScript(fakeGui(), [], () => {}, null);
  assert.equal(r.needClaude, true);
  assert.equal(r.report, undefined);
});
