import { test } from "node:test";
import assert from "node:assert/strict";
import { runScript } from "./step-executor.mjs";

// 假 gui:记录 shotBuffer 调用次数;可控 assertVisible 成败。返回 Buffer 模拟截图。
// visibleOk=false 时按 visibleLocatable 区分:true=元素定位到但不可见(business);false=定位不到(selector)。
function fakeGui({ visibleOk = true, visibleLocatable = true, clickThrow = false, hoverThrow = false, absentGone = true, textPass = true } = {}) {
  const calls = { shot: 0, hover: 0 };
  return {
    calls,
    async connect() { return { connected: true }; },
    async click() { if (clickThrow) throw new Error("未命中 key「navTasks」"); return { clicked: true }; },
    async hover(t) { if (hoverThrow) throw new Error("未命中 key「taskMenuButton」"); calls.hover += 1; return { hovered: t.key || t.selector }; },
    async assertVisible() {
      if (visibleOk) return { pass: true, locatable: true };
      return { pass: false, error: visibleLocatable ? "元素已定位但不可见" : '未命中 key「x」', locatable: visibleLocatable };
    },
    async assertAbsent() { return absentGone ? { pass: true, locatable: false } : { pass: false, locatable: true }; },
    async assertText(a) { return { pass: textPass, mode: a.contains ? "contains" : "equals", negate: !!a.negate, actual: "x", expected: a.expected }; },
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

test("fail_kind:断言可见但元素已定位(隐藏)→ business(真功能问题)", async () => {
  const gui = fakeGui({ visibleOk: false, visibleLocatable: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_visible", target: { key: "x" }, desc: "看X" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  assert.equal(r.fail_kind, "business", "定位到但不可见应归 business(该可见却没可见)");
});

test("fail_kind:断言可见但元素定位不到 → selector(选择器阻塞,非功能失败)", async () => {
  const gui = fakeGui({ visibleOk: false, visibleLocatable: false });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_visible", target: { key: "composeAddMenuExpertViewAll" }, desc: "看查看更多专家" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  assert.equal(r.fail_kind, "selector", "定位不到(key 候选没覆盖)应归 selector,不计功能失败率");
});

test("assert_absent:元素已消失 → pass", async () => {
  const gui = fakeGui({ absentGone: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_absent", target: { key: "composeAddMenuExpertChip" }, desc: "断言专家 Chip 已移除" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "pass", r.reason);
});

test("assert_absent:元素仍在 → business(本应消失却还在)", async () => {
  const gui = fakeGui({ absentGone: false });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_absent", target: { key: "composeAddMenuExpertChip" }, desc: "断言专家 Chip 已移除" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  assert.equal(r.fail_kind, "business");
});

test("assert_text negate:否定断言透传 negate 到 gui.assertText", async () => {
  const gui = fakeGui({ textPass: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "assert_text", target: { key: "expertRow" }, args: { expected: "纳米Work", negate: true }, desc: "断言右侧不显示纳米Work" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "pass", r.reason);
  assert.equal(r.steps.find((s) => s.action === "assert_text").negate, true);
});

test("fail_kind:定位/操作抛错 → selector(阻塞,非功能失败)", async () => {
  const gui = fakeGui({ clickThrow: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "click", target: { key: "navTasks" }, desc: "点导航" },
    { action: "assert_visible", target: { key: "x" }, desc: "看X" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  assert.equal(r.fail_kind, "selector", "定位/操作失败应归 selector(选择器/环境阻塞)");
});

test("空 script → needClaude,不产 report", async () => {
  const r = await runScript(fakeGui(), [], () => {}, null);
  assert.equal(r.needClaude, true);
  assert.equal(r.report, undefined);
});

test("hover:调 gui.hover 后正常继续,report 记该步 ok", async () => {
  const gui = fakeGui({ visibleOk: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "hover", target: { key: "taskListItem" }, desc: "悬停任务项" },
    { action: "assert_visible", target: { key: "taskMenuButton" }, desc: "看悬停后出现的菜单按钮" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "pass", r.reason);
  assert.equal(gui.calls.hover, 1, "应调用一次 gui.hover");
  assert.equal(r.report[1].action, "hover");
  assert.equal(r.report[1].ok, true);
});

test("hover:定位/操作抛错 → selector(阻塞,非功能失败)", async () => {
  const gui = fakeGui({ hoverThrow: true });
  const script = [
    { action: "connect", desc: "连接" },
    { action: "hover", target: { key: "taskMenuButton" }, desc: "悬停" },
    { action: "assert_visible", target: { key: "x" }, desc: "看X" },
  ];
  const r = await runScript(gui, script, () => {}, null);
  assert.equal(r.verdict, "fail");
  assert.equal(r.fail_kind, "selector", "hover 抛错应归 selector(定位/环境阻塞)");
});
