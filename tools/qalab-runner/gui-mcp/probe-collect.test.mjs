import { test } from "node:test";
import assert from "node:assert/strict";
import { rectIntersect, rectInsideRatio, collectDeep } from "./probe-collect.mjs";

test("rectIntersect: 相交/包含/相离/边界相接", () => {
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 5, y: 5, w: 10, h: 10 }), true, "部分重叠");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 100, h: 100 }, { x: 10, y: 10, w: 5, h: 5 }), true, "包含");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 20, y: 20, w: 5, h: 5 }), false, "相离");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 10, y: 0, w: 5, h: 10 }), true, "右边界相接");
  assert.equal(rectIntersect(null, { x: 0, y: 0, w: 1, h: 1 }), false, "空矩形");
});

test("rectInsideRatio: 占比过滤（框选去噪核心）", () => {
  // 小控件完全在框内 → 1.0（保留）
  assert.equal(rectInsideRatio({ x: 10, y: 10, w: 20, h: 20 }, { x: 0, y: 0, w: 100, h: 100 }), 1, "全在框内");
  // 页面级大容器盖住小框 → 占比极小（丢弃）：容器 1000×1000，框 100×100 → 交集 10000 / 容器 1e6 = 0.01
  assert.equal(rectInsideRatio({ x: 0, y: 0, w: 1000, h: 1000 }, { x: 0, y: 0, w: 100, h: 100 }), 0.01, "大容器占比极小");
  // 半进半出 → 0.5（边界，≥0.5 保留）：元素 20×20 在 (90,0)，框右边界 x=100 → 交集宽 10 → 200/400=0.5
  assert.equal(rectInsideRatio({ x: 90, y: 0, w: 20, h: 20 }, { x: 0, y: 0, w: 100, h: 100 }), 0.5, "半在框内");
  assert.equal(rectInsideRatio({ x: 0, y: 0, w: 10, h: 10 }, { x: 50, y: 50, w: 5, h: 5 }), 0, "相离为0");
  assert.equal(rectInsideRatio(null, { x: 0, y: 0, w: 1, h: 1 }), 0, "空元素为0");
});

test("collectDeep: 穿透 shadowRoot 递归采集", () => {
  const inner = { tagName: "BUTTON", shadowRoot: null };
  const shadowHost = { tagName: "DIV", shadowRoot: { querySelectorAll: () => [inner] } };
  const top = { tagName: "SECTION", shadowRoot: null };
  const root = { querySelectorAll: () => [top, shadowHost] };
  const got = collectDeep(root);
  assert.ok(got.includes(top) && got.includes(shadowHost) && got.includes(inner),
    "顶层 + shadow host + shadow 内元素都采到");
  assert.equal(got.length, 3);
});

test("collectDeep: includeShadow=false 不进 shadow", () => {
  const inner = { tagName: "BUTTON", shadowRoot: null };
  const shadowHost = { tagName: "DIV", shadowRoot: { querySelectorAll: () => [inner] } };
  const root = { querySelectorAll: () => [shadowHost] };
  const got = collectDeep(root, { includeShadow: false });
  assert.deepEqual(got, [shadowHost], "只采 host，不进 shadow");
});

test("collectDeep: 多层嵌套 shadow", () => {
  const deep = { tagName: "SPAN", shadowRoot: null };
  const mid = { tagName: "DIV", shadowRoot: { querySelectorAll: () => [deep] } };
  const host = { tagName: "DIV", shadowRoot: { querySelectorAll: () => [mid] } };
  const root = { querySelectorAll: () => [host] };
  const got = collectDeep(root);
  assert.ok(got.includes(deep), "两层 shadow 深处元素也采到");
  assert.equal(got.length, 3);
});
