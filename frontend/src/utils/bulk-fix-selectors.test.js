// 批量补选择器纯逻辑的单测(node:test)。
// 跑: cd frontend && node --test src/utils/bulk-fix-selectors.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { collectMissingKeys, matchElementsToKeys } from "./bulk-fix-selectors.js";

// ---- collectMissingKeys:汇总选中待补用例缺的 key,去重、剔除已注册 ----
test("collectMissingKeys: 汇总去重、剔除已注册、忽略非待补用例", () => {
  const cases = [
    { selector_fix: true, selector_fix_keys: ["k1", "k2"], title: "创建任务", steps: "点新建" },
    { selector_fix: true, selector_fix_keys: ["k2", "k3"], title: "校验弹窗", steps: "看弹窗" }, // k2 重复
    { selector_fix: false, selector_fix_keys: ["k4"], title: "非待补", steps: "x" },              // 忽略
  ];
  const r = collectMissingKeys(cases, new Set(["k3"])); // k3 已注册 → 剔除
  assert.deepEqual(r.keys, ["k1", "k2"], "去重、剔除已注册k3、忽略C的k4");
  assert.deepEqual(r.skipped, ["k3"], "已注册的记入 skipped(已有覆盖)");
  assert.equal(r.caseCount, 2, "只计待补用例");
  assert.ok(r.ctx.includes("创建任务") && r.ctx.includes("校验弹窗"), "ctx 含待补用例上下文");
  assert.ok(!r.ctx.includes("非待补"), "ctx 不含非待补用例");
});

test("collectMissingKeys: registered 传数组也可、保序、无缺key返回空", () => {
  const r = collectMissingKeys(
    [{ selector_fix: true, selector_fix_keys: ["b", "a", "b"], title: "T", steps: "" }],
    ["x"],
  );
  assert.deepEqual(r.keys, ["b", "a"], "按首次出现保序、去重");
  const empty = collectMissingKeys([], new Set());
  assert.deepEqual(empty.keys, []);
  assert.equal(empty.caseCount, 0);
});

// ---- matchElementsToKeys:探测元素 → 待补 key 批量配对(贪心,一元素配一 key) ----
test("matchElementsToKeys: 按语义把元素配到最匹配的待补 key", () => {
  const keys = ["automationCreateBtn", "acmModal"];
  const elements = [
    { text: "新建", candidates: [{ by: "css", value: ".automation-create-btn" }] },
    { text: "弹窗", candidates: [{ by: "css", value: ".acm-modal" }] },
    { text: "无关", candidates: [{ by: "css", value: ".footer" }] },
  ];
  const pairs = matchElementsToKeys(keys, "创建任务 点新建", elements);
  const byKey = Object.fromEntries(pairs.map((p) => [p.key, p]));
  assert.equal(byKey.automationCreateBtn.el.text, "新建", "createBtn 配到 .automation-create-btn 元素");
  assert.equal(byKey.acmModal.el.text, "弹窗", "acmModal 配到 .acm-modal 元素");
  assert.ok(byKey.automationCreateBtn.score > 0 && byKey.acmModal.score > 0);
});

test("matchElementsToKeys: 一个元素不被两个 key 抢占(贪心去冲突)", () => {
  const keys = ["acmModal", "acmModalTitle"];
  // 只有一个元素能匹配 modal 类词 → 分数高者独占,另一个 key 落空(score=0,留待换状态探测)
  const elements = [{ text: "弹窗标题", candidates: [{ by: "css", value: ".acm-modal-title" }] }];
  const pairs = matchElementsToKeys(keys, "", elements);
  const used = pairs.filter((p) => p.el);
  assert.equal(used.length, 1, "同一元素只配给一个 key");
  const unmatched = pairs.filter((p) => !p.el);
  assert.equal(unmatched.length, 1, "另一个 key 落空,el=null");
});

test("matchElementsToKeys: 无匹配元素时 key 落空 el=null score=0", () => {
  const pairs = matchElementsToKeys(["someKey"], "", []);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0].el, null);
  assert.equal(pairs[0].score, 0);
});
