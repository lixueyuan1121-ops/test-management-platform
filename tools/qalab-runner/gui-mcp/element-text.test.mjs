import { test } from "node:test";
import assert from "node:assert/strict";
import { elementTextValue } from "./element-text.mjs";

test("input/textarea/select 取 .value(用户输入值不在 textContent)", () => {
  // 数字步进输入框:用户填了 23,textContent 恒空 —— 这正是 tc15/tc14 假失败的根因
  assert.equal(elementTextValue({ tagName: "INPUT", value: "23", textContent: "" }), "23");
  assert.equal(elementTextValue({ tagName: "TEXTAREA", value: "多行\n文本", textContent: "" }), "多行\n文本");
  assert.equal(elementTextValue({ tagName: "SELECT", value: "opt2", textContent: "选项一选项二" }), "opt2");
});

test("非表单元素回落 textContent", () => {
  assert.equal(elementTextValue({ tagName: "DIV", value: undefined, textContent: "主对话" }), "主对话");
  assert.equal(elementTextValue({ tagName: "SPAN", textContent: "每日AI新闻推送" }), "每日AI新闻推送");
});

test("input 的 value 为空串 → 返回空串(不回落 textContent,避免读到 placeholder 类噪声)", () => {
  assert.equal(elementTextValue({ tagName: "INPUT", value: "", textContent: "占位" }), "");
});

test("大小写标签名不敏感", () => {
  assert.equal(elementTextValue({ tagName: "input", value: "6", textContent: "" }), "6");
});

test("null/缺字段兜底空串,不抛错", () => {
  assert.equal(elementTextValue(null), "");
  assert.equal(elementTextValue({ tagName: "DIV" }), "");
  assert.equal(elementTextValue({}), "");
});
