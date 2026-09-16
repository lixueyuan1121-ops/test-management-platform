// 真实 Chromium 验证 DOM 折叠、轮次隔离与 trace；保留 ≥60 秒耗时回填回归。
const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { WorkbuddyDomTrace } = require('../src/workbuddy-dom-trace');
let browser, page, trace;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { page = await browser.newPage(); trace = new WorkbuddyDomTrace(); });
afterEach(async () => { await page.close(); });

function collapse(title, thinking, open = false) {
  return `<section class="cr-collapse">
    <button class="cr-collapse__header" onclick="let b=this.nextElementSibling;b.hidden=!b.hidden">
      <span class="cr-collapse__title">${title}</span>
    </button><div class="cr-collapse__content-inner" ${open ? '' : 'hidden'}><div class="cr-markdown">${thinking}</div></div>
  </section>`;
}

test('本轮多个思考区全部读取，长耗时回填 163 秒，已展开区不被反向折叠', async () => {
  await page.setContent(`<div class="cr-agent" data-message-id="current">
    ${collapse('已完成 2分43秒', '先分析')}${collapse('深度思考', '深入推理', true)}
    <div class="cr-markdown">最终回答</div><div class="conversation-finished-footer">共消耗\n5\nDeepseek</div></div>`);
  await trace.captureTurn(page);
  const result = trace.buildTrace();
  assert.equal(result.reported_duration, '163');
  assert.equal(result.reported_duration_raw, '已完成 2分43秒');
  assert.match(result.thinking, /先分析\n深入推理/);
  assert.equal(result.answer, '最终回答');
  assert.equal(await page.locator('.cr-collapse__content-inner:visible').count(), 2);
});

test('本轮耗时在消息正文时兜底，不混入前一轮答案、思考及耗时', async () => {
  await page.setContent(`<div class="cr-agent" data-message-id="old">${collapse('已完成 999s', '旧思考', true)}<div class="cr-markdown">旧答案</div></div>
    <div class="cr-agent" data-message-id="current"><div class="cr-message-list__content">已完成 1分5秒</div>
    <div class="cr-markdown">新答案</div></div>`);
  await trace.captureTurn(page);
  assert.equal(trace.buildTrace().reported_duration, '65');
  assert.equal(trace.buildTrace().thinking, '');
  assert.equal(trace.buildTrace().answer, '新答案');
});

test('读取来源后关闭不响应 Escape 的面板，不把引用伪装成工具成功记录', async () => {
  await page.setContent(`<button class="artifact-slot-panel__sources" onclick="document.querySelector('#sources').hidden=false">来源</button>
    <div id="sources" hidden><button class="sources-panel__close" onclick="this.parentElement.hidden=true">关闭</button>
    <div class="sources-panel__title">引用来源 (1)</div><div class="sources-panel__list">fixture source</div></div>
    <div class="cr-markdown">答案</div>`);
  await trace.captureTurn(page);
  assert.equal(await page.locator('#sources').isVisible(), false);
  assert.match(trace.buildTrace().sources_text, /fixture source/);
  assert.deepEqual(trace.buildTrace().tool_calls, []);
});
