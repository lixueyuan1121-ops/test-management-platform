import { test } from "node:test";
import assert from "node:assert/strict";
import { rawEventToStep, dedupeSteps } from "./record-capture.mjs";

test("rawEventToStep: 普通点击 → click", () => {
  const s = rawEventToStep({ tag: "button", text: "保存", candidates: [{ by: "testid", value: "save-btn" }] }, "vm");
  assert.equal(s.action, "click");
  assert.equal(s.frame, "vm");
  assert.deepEqual(s.candidates, [{ by: "testid", value: "save-btn" }]);
});

test("rawEventToStep: 无 testid 的元素带 xpath 候选 → 原样保留(供回放精确定位)", () => {
  // 技能 tab(无 testid):候选含 css 类(多命中) + xpath(精确) + text(脆弱),rawEventToStep 全保留,
  // 后端 order_candidates 会把 xpath 排到 css/text 之前。
  const s = rawEventToStep({ tag: "span", text: "技能", candidates: [
    { by: "css", value: ".sidebar-nav__text" },
    { by: "xpath", value: "//span[contains(@class,'sidebar-nav__text')][normalize-space(.)='技能']" },
    { by: "text", value: "技能" },
  ] }, "shell");
  assert.equal(s.action, "click");
  assert.ok(s.candidates.some((c) => c.by === "xpath"), "应保留 xpath 候选");
  assert.equal(s.candidates.length, 3);
});

test("rawEventToStep: change → fill(带 value)", () => {
  const s = rawEventToStep({ type: "change", tag: "input", value: "hello", candidates: [{ by: "css", value: "#q" }] });
  assert.equal(s.action, "fill");
  assert.equal(s.value, "hello");
});

test("rawEventToStep: Alt+点击有短文本 → assert_text;无文本 → assert visible", () => {
  const a = rawEventToStep({ tag: "div", text: "成功", altKey: true, candidates: [{ by: "testid", value: "toast" }] });
  assert.equal(a.action, "assert");
  assert.deepEqual(a.assert, { kind: "text", expected: "成功" });
  const b = rawEventToStep({ tag: "div", text: "", altKey: true, candidates: [{ by: "testid", value: "panel" }] });
  assert.deepEqual(b.assert, { kind: "visible" });
});

test("rawEventToStep: 无有效候选 / 非对象 → null", () => {
  assert.equal(rawEventToStep({ tag: "div", candidates: [{ by: "css" }] }), null);
  assert.equal(rawEventToStep(null), null);
});

test("dedupeSteps: 保留两次真实点击和输入，只按事件 ID 去重", () => {
  const steps = [
    { action: "click", candidates: [{ by: "testid", value: "a" }] },
    { action: "click", candidates: [{ by: "testid", value: "a" }] },   // 重复 → 去
    { action: "fill", value: "h", candidates: [{ by: "css", value: "#q" }] },
    { action: "fill", value: "he", candidates: [{ by: "css", value: "#q" }] },   // 覆盖上一 fill
    { action: "click", candidates: [{ by: "testid", value: "b" }] },
  ];
  const out = dedupeSteps(steps);
  assert.equal(out.length, 5, JSON.stringify(out));
  assert.equal(out[0].candidates[0].value, "a");
  assert.equal(out[2].value, "h");
  assert.equal(out[3].value, "he");
  assert.equal(out[4].candidates[0].value, "b");
  assert.equal(dedupeSteps([{...steps[0], event_id:"1"}, {...steps[0], event_id:"1"}]).length, 1);
});

// 同一页面连续录制时复用监听器，但不能复用上一轮缓冲。
import vm from "node:vm";
import { CAPTURE_INIT, DRAIN_SCRIPT, STOP_SCRIPT } from "./record-capture.mjs";

function capturePage() {
  const listeners = new Map();
  const context = vm.createContext({
    window: { addEventListener() {} },
    performance: { timeOrigin: 0, now: () => Date.now() },
    document: { addEventListener(type, callback) {
      const list = listeners.get(type) || [];
      list.push(callback); listeners.set(type, list);
    } },
  });
  const run = (fn, arg) => vm.runInContext(`(${fn.toString()})(${JSON.stringify(arg) || ""})`, context);
  const click = (text) => {
    const target = { tagName: "BUTTON", innerText: text, classList: [],
      matches: () => true, getAttribute: (name) => name === "data-testid" ? text : null };
    for (const callback of listeners.get("click") || []) callback({ target });
  };
  return { context, listeners, run, click };
}

test("新录制清空未上报事件，复用监听器且只捕获本轮操作", () => {
  const page = capturePage();
  page.run(CAPTURE_INIT);
  page.click("old");
  page.run(CAPTURE_INIT, { sessionId: "new" });
  assert.equal(page.run(DRAIN_SCRIPT).length, 0);
  page.click("new");
  const events = page.run(DRAIN_SCRIPT);
  assert.equal(events.length, 1);
  assert.equal(events[0].text, "new");
  assert.equal(page.listeners.get("click").length, 1);
});

test("停止保留未确认缓冲，停止期间不再捕获，新会话清空旧缓冲", () => {
  const page = capturePage();
  page.run(CAPTURE_INIT);
  page.click("old");
  page.run(STOP_SCRIPT);
  page.click("between");
  assert.equal(page.run(DRAIN_SCRIPT).length, 1);
  page.run(CAPTURE_INIT, { sessionId: "next" });
  page.click("fresh");
  const events = page.run(DRAIN_SCRIPT);
  assert.equal(events.length, 1);
  assert.equal(events[0].text, "fresh");
});
