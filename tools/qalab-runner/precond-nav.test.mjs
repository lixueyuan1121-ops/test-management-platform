import { test } from "node:test";
import assert from "node:assert/strict";
import { parseNavOk } from "./precond-nav.mjs";

test("parseNavOk: 裸 JSON ok:true", () => {
  assert.deepEqual(parseNavOk('{"ok":true,"reason":"已到达:进入首个会话"}'), { ok: true, reason: "已到达:进入首个会话" });
});

test("parseNavOk: claude --output-format json 信封", () => {
  const env = JSON.stringify({ result: '好的\n{"ok":false,"reason":"未找到含文件产物的会话"}' });
  assert.deepEqual(parseNavOk(env), { ok: false, reason: "未找到含文件产物的会话" });
});

test("parseNavOk: 前置解释文字 + markdown 代码块,取最后一个 ok 对象", () => {
  const t = '我先探测。```json\n{"ok":true,"reason":"到位"}\n```';
  assert.deepEqual(parseNavOk(t), { ok: true, reason: "到位" });
});

test("parseNavOk: 多个对象取最后一个含布尔 ok 的", () => {
  const t = '{"foo":1} 中间 {"ok":true,"reason":"a"} 再 {"ok":false,"reason":"b"}';
  assert.deepEqual(parseNavOk(t), { ok: false, reason: "b" });
});

test("parseNavOk: reason 内含花括号不破坏配平", () => {
  assert.deepEqual(parseNavOk('{"ok":true,"reason":"点了 {更多} 菜单"}'), { ok: true, reason: "点了 {更多} 菜单" });
});

test("parseNavOk: 无可解析结论 → null", () => {
  assert.equal(parseNavOk("我不确定该怎么做"), null);
  assert.equal(parseNavOk('{"verdict":"pass"}'), null);  // 无 ok 字段
});
