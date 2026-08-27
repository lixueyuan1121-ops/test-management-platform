import { test } from "node:test";
import assert from "node:assert/strict";
import { rectIntersect, collectDeep } from "./probe-collect.mjs";

test("rectIntersect: 相交/包含/相离/边界相接", () => {
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 5, y: 5, w: 10, h: 10 }), true, "部分重叠");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 100, h: 100 }, { x: 10, y: 10, w: 5, h: 5 }), true, "包含");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 20, y: 20, w: 5, h: 5 }), false, "相离");
  assert.equal(rectIntersect({ x: 0, y: 0, w: 10, h: 10 }, { x: 10, y: 0, w: 5, h: 10 }), true, "右边界相接");
  assert.equal(rectIntersect(null, { x: 0, y: 0, w: 1, h: 1 }), false, "空矩形");
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
