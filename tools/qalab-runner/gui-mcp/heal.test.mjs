import { test } from "node:test";
import assert from "node:assert/strict";
import { splitKeyTokens, tokensForKey, pickConfident, mintedToCandidates } from "./heal.mjs";

test("splitKeyTokens: camelCase/snake 拆词 + 去重 + 过滤过短", () => {
  assert.deepEqual(splitKeyTokens("homeGreetingTitle"), ["home", "greeting", "title"]);
  assert.deepEqual(splitKeyTokens("nav_home-btn"), ["nav", "home", "btn"]);
  assert.deepEqual(splitKeyTokens("aB"), ["ab"]);   // 整体≥2 保留
  assert.deepEqual(splitKeyTokens(""), []);
  assert.deepEqual(splitKeyTokens(null), []);
});

test("tokensForKey: desc 引号内文案 + 旧候选 text/label/placeholder/role.name 进 textTokens", () => {
  const { idTokens, textTokens } = tokensForKey("sendBtn", {
    desc: "输入框右侧「发送」按钮",
    candidates: [
      { by: "text", value: "发送" },
      { by: "role", value: "button", name: "发送消息" },
      { by: "label", value: "send message" },
      { by: "placeholder", value: "输入内容" },
      { by: "testid", value: "chat-send" },
      { by: "css", value: ".send" },   // css 不进 tokens
    ],
  });
  assert.deepEqual(idTokens, ["send", "btn", "chat-send"]);
  assert.deepEqual(textTokens, ["发送", "发送消息", "send message", "输入内容"]);
});

test("tokensForKey: 空 entry 也可用(仅 key 拆词)", () => {
  const { idTokens, textTokens } = tokensForKey("loginSubmit");
  assert.deepEqual(idTokens, ["login", "submit"]);
  assert.deepEqual(textTokens, []);
});

test("pickConfident: 高分唯一 → 取;分差不足/低分 → null(保守不自愈)", () => {
  assert.equal(pickConfident([]), null);
  assert.equal(pickConfident(null), null);
  // 低于阈值
  assert.equal(pickConfident([{ score: 50 }]), null);
  // 唯一高分 → 取
  const only = { score: 90, text: "发送" };
  assert.equal(pickConfident([only]), only);
  // 与次名差距足够(≥25) → 取
  const top = { score: 120 };
  assert.equal(pickConfident([top, { score: 60 }]), top);
  // 差距不足 → null(两个都像,不敢选)
  assert.equal(pickConfident([{ score: 90 }, { score: 80 }]), null);
  // 自定义阈值
  assert.equal(pickConfident([{ score: 50 }], { minScore: 40 }).score, 50);
});

test("mintedToCandidates: 清洗+去重+截断+打 learned 标", () => {
  const out = mintedToCandidates([
    { by: "testid", value: "chat-send", score: 100 },
    { by: "css", value: "#send", score: 90 },
    { by: "css", value: "#send", score: 90 },       // 重复剔除
    { by: "bogus", value: "x" },                     // 非法 by 剔除
    { by: "text", value: "" },                       // 空 value 剔除
    { by: "role", value: "button", name: "发送", score: 68 },
    { by: "text", value: "发送", score: 40 },        // cap=3 截掉
  ]);
  assert.deepEqual(out, [
    { by: "testid", value: "chat-send", src: "learned" },
    { by: "css", value: "#send", src: "learned" },
    { by: "role", value: "button", src: "learned", name: "发送" },
  ]);
  assert.deepEqual(mintedToCandidates(null), []);
});
