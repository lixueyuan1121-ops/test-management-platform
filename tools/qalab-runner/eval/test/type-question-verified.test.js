// 「输入 query 后校验其真的进了输入框」控制流自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/type-question-verified.test.js
//
// 背景:桌面客户端里附件 setInputFiles 之后,ProseMirror 输入框焦点/selection 常未落稳,
// pressSequentially 静默不进字符 → query 没输入、点发送也发空 → 静默超时(带附件才暴露,
// 平台模式此前跳过带附件会话故一直没踩到)。故输入后必须读回校验:空则重试一次,仍空则显式抛错。
// 纯控制流(注入 type/readText),不依赖 Playwright,可单测。

const assert = require('assert');
const { typeQuestionVerified } = require('../src/type-question-verified');

async function test_first_try_succeeds_no_retry() {
  let typed = 0;
  await typeQuestionVerified({
    type: async () => { typed++; }, readText: async () => '你好世界',
    question: '你好世界', sleep: async () => {}, warn: () => {},
  });
  assert.strictEqual(typed, 1, '一次输入成功不应重试');
  console.log('✓ 首次输入成功:不重试、不抛');
}

async function test_empty_then_retry_succeeds() {
  let typed = 0;
  const reads = ['', '重试后进去了'];
  await typeQuestionVerified({
    type: async () => { typed++; }, readText: async () => reads[typed - 1],
    question: '重试后进去了', sleep: async () => {}, warn: () => {},
  });
  assert.strictEqual(typed, 2, '首次为空应重试一次');
  console.log('✓ 首次为空、重试后进去:不抛');
}

async function test_always_empty_throws() {
  let typed = 0;
  await assert.rejects(
    typeQuestionVerified({
      type: async () => { typed++; }, readText: async () => '',
      question: '始终进不去', sleep: async () => {}, warn: () => {},
    }),
    /未能输入到对话框/, '始终为空应显式抛错(把静默失败暴露出来)');
  assert.strictEqual(typed, 2, '默认重试 1 次后放弃(共 2 次尝试)');
  console.log('✓ 始终为空:重试后显式抛错,不静默发空');
}

async function test_blank_question_no_throw() {
  // 空 query(理论不该发,但这里只保证不因「读回空」误抛)
  await typeQuestionVerified({
    type: async () => {}, readText: async () => '', question: '   ',
    sleep: async () => {}, warn: () => {},
  });
  console.log('✓ 空 query:读回空不误判抛错');
}

async function main() {
  await test_first_try_succeeds_no_retry();
  await test_empty_then_retry_succeeds();
  await test_always_empty_throws();
  await test_blank_question_no_throw();
  console.log('\n✅ 输入校验控制流 全部通过');
}
main().catch((e) => { console.error('✗ 测试失败:', e.message); process.exit(1); });
