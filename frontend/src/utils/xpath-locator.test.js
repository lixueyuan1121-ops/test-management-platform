import test from 'node:test';
import assert from 'node:assert/strict';
import { autoXPath, xpathTextEq } from './xpath-locator.js';

// 探测端算好的 uniqueXPath(真实 DOM 里带同标签序号,保证唯一)优先于前端类+文本拼装。
test('autoXPath prefers browser-computed uniqueXPath', () => {
  const el = { tag: 'button', text: 'DeepSeek-V4-Flash', uniqueXPath: '//*[@id="app"]/div[2]/button[3]',
    candidates: [{ by: 'css', value: '.trigger--compose' }] };
  assert.equal(autoXPath(el), '//*[@id="app"]/div[2]/button[3]');
});

// 没有 uniqueXPath(旧 runner)时退回类+文本拼装。
test('autoXPath falls back to class+text when no uniqueXPath', () => {
  const el = { tag: 'button', text: '打开文件夹', candidates: [{ by: 'css', value: '.preview-btn' }] };
  assert.equal(autoXPath(el), "//button[contains(@class,'preview-btn')][normalize-space(.)='打开文件夹']");
});

// 空 uniqueXPath(shadow 内/脱离文档)不采纳,继续退回拼装。
test('autoXPath ignores empty uniqueXPath', () => {
  const el = { tag: 'button', text: '发送', uniqueXPath: '', candidates: [{ by: 'css', value: '.send' }] };
  assert.equal(autoXPath(el), "//button[contains(@class,'send')][normalize-space(.)='发送']");
});

test('xpathTextEq quoting', () => {
  assert.equal(xpathTextEq('报告'), "normalize-space(.)='报告'");
  assert.equal(xpathTextEq("it's"), 'normalize-space(.)="it\'s"');
  assert.equal(xpathTextEq(''), '');
});
