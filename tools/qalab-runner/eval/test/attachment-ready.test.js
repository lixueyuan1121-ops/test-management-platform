// 「多候选选择器判定附件是否已挂」纯逻辑自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/attachment-ready.test.js
//
// 背景:桌面客户端真实的「草稿附件卡片」DOM 未知,单一自定义选择器 attachment-item 匹配不到
// → _waitAttachmentsReady 60s 超时抛错,误杀所有带附件用例(附件其实已 setInputFiles 成功)。
// 改为多候选(配置优先 + 通用附件/上传/图片兜底),任一候选计数达期望即算挂上。

const assert = require('assert');
const { pickReadyAttachment } = require('../src/attachment-ready');

function test_primary_selector_hit() {
  const r = pickReadyAttachment([{ sel: 'attachment-item', n: 1 }, { sel: '[class*="upload"]', n: 0 }], 1);
  assert.strictEqual(r.ready, true, '首选达标应 ready');
  assert.strictEqual(r.via, 'attachment-item', '应记录命中的选择器');
  console.log('✓ 首选选择器命中');
}

function test_fallback_selector_hit() {
  const r = pickReadyAttachment([{ sel: 'attachment-item', n: 0 }, { sel: '[class*="upload"]', n: 2 }], 2);
  assert.strictEqual(r.ready, true, '首选未中、兜底达标也应 ready');
  assert.strictEqual(r.via, '[class*="upload"]');
  console.log('✓ 兜底选择器命中');
}

function test_none_reaches_expected() {
  const r = pickReadyAttachment([{ sel: 'a', n: 0 }, { sel: 'b', n: 1 }], 2);
  assert.strictEqual(r.ready, false, '无候选达期望数应 not ready');
  console.log('✓ 均未达期望:not ready(交上层放行+dump)');
}

function test_priority_first_wins() {
  const r = pickReadyAttachment([{ sel: 'first', n: 3 }, { sel: 'second', n: 3 }], 1);
  assert.strictEqual(r.via, 'first', '多个达标取优先级最高(第一个)');
  console.log('✓ 多候选达标取优先级最高');
}

function main() {
  test_primary_selector_hit();
  test_fallback_selector_hit();
  test_none_reaches_expected();
  test_priority_first_wins();
  console.log('\n✅ 附件就绪判定 全部通过');
}
main();
