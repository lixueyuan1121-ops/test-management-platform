// 「判定一个会话(conv)是否含带附件轮次」纯函数自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/conv-has-attachments.test.js
//
// 背景:WorkBuddy 附件走 Electron 原生文件对话框(纳米 setInputFiles 方案不适用,contextBridge 冻结),
// 真机坐实方案 = 剪贴板 file-url + Meta+V 粘贴(见 workbuddy-runner._pasteAttachments)。
// runWorkbuddyBatch 用本判定识别带附件会话 → 执行前把附件下到本地挂到 testCase;下载失败则整组
// fail-closed(显式 failed),绝不「缺附件裸跑」污染判定。多轮里任一轮带附件即需整组走下载分支。

const assert = require('assert');
const { convHasAttachments } = require('../src/conversation-group');

function item(run_id, atts) {
  return { run_id, payload: { prompt: `q${run_id}`, attachments: atts } };
}

function test_single_turn_with_attachments() {
  const conv = [item(1, [{ name: 'a.txt', url: 'http://x/a.txt' }])];
  assert.strictEqual(convHasAttachments(conv), true, '单轮带附件应判 true');
  console.log('✓ 单轮带附件 → true');
}

function test_no_attachments() {
  const conv = [item(1, []), item(2, [])];
  assert.strictEqual(convHasAttachments(conv), false, '各轮空附件应判 false');
  console.log('✓ 各轮空附件 → false');
}

function test_missing_attachments_field() {
  const conv = [{ run_id: 1, payload: { prompt: 'q1' } }];  // 无 attachments 字段
  assert.strictEqual(convHasAttachments(conv), false, '无 attachments 字段应判 false');
  console.log('✓ 缺 attachments 字段 → false');
}

function test_multiturn_only_one_turn_has_attachment() {
  // 多轮里只有中间一轮带附件 → 整组也应判 true(该轮裸跑同样污染,且会连累整段上下文)
  const conv = [item(1, []), item(2, [{ name: 'p.png', url: 'http://x/p.png' }]), item(3, [])];
  assert.strictEqual(convHasAttachments(conv), true, '多轮中任一轮带附件应判 true');
  console.log('✓ 多轮中任一轮带附件 → true');
}

function test_null_and_empty_conv() {
  assert.strictEqual(convHasAttachments([]), false, '空会话应判 false');
  assert.strictEqual(convHasAttachments(null), false, 'null 应判 false(不抛错)');
  assert.strictEqual(convHasAttachments(undefined), false, 'undefined 应判 false(不抛错)');
  console.log('✓ 空/null/undefined → false(不抛错)');
}

function test_payload_missing() {
  const conv = [{ run_id: 1 }];  // 连 payload 都没有
  assert.strictEqual(convHasAttachments(conv), false, '缺 payload 应判 false(不抛错)');
  console.log('✓ 缺 payload → false(不抛错)');
}

function test_attachments_not_array() {
  // 脏数据:attachments 不是数组(如 null/对象/字符串)→ 容错判 false,不抛错
  const conv = [{ run_id: 1, payload: { attachments: null } },
                { run_id: 2, payload: { attachments: 'x' } }];
  assert.strictEqual(convHasAttachments(conv), false, 'attachments 非数组应容错判 false');
  console.log('✓ attachments 非数组 → false(容错)');
}

function main() {
  test_single_turn_with_attachments();
  test_no_attachments();
  test_missing_attachments_field();
  test_multiturn_only_one_turn_has_attachment();
  test_null_and_empty_conv();
  test_payload_missing();
  test_attachments_not_array();
  console.log('\n✅ 会话带附件判定 全部通过');
}
main();
