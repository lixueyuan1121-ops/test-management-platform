// WorkBuddy 思考折叠头「已完成 …」耗时解析纯逻辑自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/workbuddy-duration.test.js
//
// 背景(根因,2026-09-08):reportedDuration 短会话能回填、长会话恒 null。
// WorkBuddy 折叠头文本 <60s 呈「已完成 42s」、≥60s 呈「已完成 2分43秒」(中文复合,与纳米
// .chat-thinking-toggle__label「已完成 2分43秒」同形)。原 parseDuration 只认 /已完成\s*(\d+)\s*s/
// (整数+拉丁 s),复合时长/冒号/时分秒全不匹配 → 返回空串 → 提交 null → 后端拿不到耗时。
// 修复:识别「已完成」后任意时长表达并统一换算成纯秒(口径对齐 dialog-runner._durationToSeconds
// 与后端 _parse_seconds),解析不出才返回空。

const assert = require('assert');
const { parseDuration } = require('../src/workbuddy-dom-trace');

function test_short_seconds_preserved() {
  // 回归:短会话「已完成 12s」原本就能回填,修复后仍是纯秒 "12"
  assert.strictEqual(parseDuration('已完成 12s'), '12', '整数秒保持不变');
  assert.strictEqual(parseDuration('已完成 42s'), '42', '整数秒保持不变');
  console.log('✓ 短会话整数秒仍正常(无回归)');
}

function test_compound_minutes_seconds() {
  // THE BUG:长会话「已完成 2分43秒」原返回空 → 现换算成 163 秒
  assert.strictEqual(parseDuration('已完成 2分43秒'), '163', '2分43秒→163');
  assert.strictEqual(parseDuration('已完成 1分5秒'), '65', '1分5秒→65');
  assert.strictEqual(parseDuration('已完成 10秒'), '10', '纯中文秒→10');
  console.log('✓ 长会话中文复合时长可解析(根因修复)');
}

function test_hours_and_colon() {
  assert.strictEqual(parseDuration('已完成 1小时2分3秒'), '3723', '含小时→3723');
  assert.strictEqual(parseDuration('已完成 01:05'), '65', 'mm:ss→65');
  console.log('✓ 时分秒/冒号格式可解析');
}

function test_no_done_marker() {
  // 未完成(仍思考中)/其他折叠头 → 空串,不臆造耗时
  assert.strictEqual(parseDuration('深度思考'), '', '无「已完成」标记返回空');
  assert.strictEqual(parseDuration('思考中'), '', '未完成返回空');
  assert.strictEqual(parseDuration(''), '', '空文本返回空');
  assert.strictEqual(parseDuration(null), '', 'null 返回空');
  console.log('✓ 无完成标记/不可解析返回空(不臆造)');
}

function main() {
  test_short_seconds_preserved();
  test_compound_minutes_seconds();
  test_hours_and_colon();
  test_no_done_marker();
  console.log('\n✅ WorkBuddy 耗时解析 全部通过');
}
main();
