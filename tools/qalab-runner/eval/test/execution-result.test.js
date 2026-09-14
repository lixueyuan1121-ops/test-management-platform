const { test } = require('node:test');
const assert = require('node:assert/strict');
const { formatFailureReason } = require('../src/execution-result');

test('configuration failure is reported with exactly one error-code prefix', () => {
  for (const prefix of ['', '[CONFIG_ERROR] ', '[CONFIG_ERROR] [CONFIG_ERROR] ']) {
    assert.equal(formatFailureReason({ success: false, errorCode: 'CONFIG_ERROR', errorMessage: `${prefix}找不到模型「GLM-5.3」` }),
      '[CONFIG_ERROR] 找不到模型「GLM-5.3」');
  }
});

test('failure reason retains uncoded errors, completion reasons and other codes', () => {
  assert.equal(formatFailureReason({ errorMessage: '连接中断' }), '连接中断');
  assert.equal(formatFailureReason({ completeReason: '任务未完成' }), '任务未完成');
  assert.equal(formatFailureReason({ errorCode: 'TIMEOUT', errorMessage: '等待超时' }), '[TIMEOUT] 等待超时');
  assert.equal(formatFailureReason({ errorCode: 'TIMEOUT' }), '[TIMEOUT]');
  assert.equal(formatFailureReason({}), null);
});

test('successful results do not carry a stale failure reason', () => {
  assert.equal(formatFailureReason({ success: true, errorCode: 'CONFIG_ERROR', errorMessage: '旧错误' }), null);
});
