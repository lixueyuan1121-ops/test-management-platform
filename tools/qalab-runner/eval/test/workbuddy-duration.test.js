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
const { parseDuration, pickDurationSeconds } = require('../src/workbuddy-dom-trace');

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

// pickDurationSeconds:从多个候选文本里挑出耗时。折叠头是主来源;若真机上耗时不在 cr-collapse
// (探针 probe-workbuddy-duration.js 待确认的场景),整条消息文本作兜底候选,任一命中即取,
// 并返回 { seconds, raw } 供 runner 落日志作证据。全不命中 → seconds=''(不臆造)。
function test_pick_from_collapse_titles() {
  const r = pickDurationSeconds({ titles: ['深度思考', '已完成 2分43秒'], messageText: '' });
  assert.strictEqual(r.seconds, '163', '多折叠中挑出「已完成 …」那条');
  assert.strictEqual(r.raw, '已完成 2分43秒', 'raw 保留原文供排障');
  console.log('✓ 多折叠标题中挑出耗时');
}

function test_pick_fallback_to_message_text() {
  // 折叠头都没耗时 → 退回整条消息文本里找「已完成 …」(耗时不在 cr-collapse 的兜底)
  const r = pickDurationSeconds({
    titles: ['深度思考'],
    messageText: '回答正文……\n已完成 1分5秒\n共消耗 12',
  });
  assert.strictEqual(r.seconds, '65', '折叠头无耗时时从消息文本兜底');
  console.log('✓ 折叠头无耗时时退回消息文本兜底');
}

function test_pick_none() {
  const r = pickDurationSeconds({ titles: ['思考中'], messageText: '还在想' });
  assert.strictEqual(r.seconds, '', '全不命中 seconds 为空');
  console.log('✓ 全不命中返回空(不臆造)');
}

function main() {
  test_short_seconds_preserved();
  test_compound_minutes_seconds();
  test_hours_and_colon();
  test_no_done_marker();
  test_pick_from_collapse_titles();
  test_pick_fallback_to_message_text();
  test_pick_none();
  console.log('\n✅ WorkBuddy 耗时解析 全部通过');
}
main();
