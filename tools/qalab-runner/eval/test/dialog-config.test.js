const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
const DesktopRunner = require('../src/desktop-runner');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { readSelection } = require('../src/dialog-config');
let browser, page;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { page = await browser.newPage(); });
afterEach(async () => { await page.close(); });

function nami(options) {
  return new DialogRunner(null, { modelDropdownSelector: '#trigger', modelOptionSelector: '.option' },
    { dialogOptions: options }).attachToPage(page);
}
async function fixture(change = true) {
  await page.setContent(`<button id="trigger" onclick="document.querySelector('#menu').hidden=false"><span id="selected">模型A</span></button>
    <div id="menu" hidden><button class="option" onclick="${change ? "document.querySelector('#selected').textContent='模型B';" : ''}document.querySelector('#menu').hidden=true">模型B</button></div>`);
}
test('explicit model switches and readback proves the selected value', async () => {
  await fixture();
  const r = nami({ model: '模型B' });
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
  assert.deepEqual(r.executionConfig.observed.model, ['模型B']);
});
test('click without state change fails instead of reporting success', async () => {
  await fixture(false);
  const r = nami({ model: '模型B' });
  await assert.rejects(r._applyDialogOptions(), /CONFIG_ERROR/);
  assert.equal(r.executionConfig.status, 'config_error');
});
test('missing requested option fails; unspecified model only reads the current value', async () => {
  await fixture();
  await assert.rejects(nami({ model: '不存在' })._applyDialogOptions(), /CONFIG_ERROR/);
  await page.locator('#menu').evaluate(e => { e.hidden = true; });
  const r = nami({});
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'observed');
  assert.deepEqual(r.executionConfig.observed.model, ['模型A']);
});
test('readback supports nested shadow DOM and ignores hidden menu labels', async () => {
  await page.setContent('<model-dropdown></model-dropdown>');
  await page.locator('model-dropdown').evaluate(el => {
    el.attachShadow({ mode: 'open' }).innerHTML = '<button>GLM-5.3</button><div hidden>GLM-5.2</div>';
  });
  assert.deepEqual(await readSelection(page.locator('model-dropdown')), ['GLM-5.3']);
});
test('WorkBuddy validates selection and rejects unsupported modes', async () => {
  await fixture();
  const r = new WorkbuddyRunner(page, {modelTriggerSelector:'#trigger', modelOptionSelector:'.option'}, {dialogOptions:{model:'模型B'}});
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
  r.execution.dialogOptions = { thinkingDepth: '高' };
  await assert.rejects(r._applyDialogOptions(), /CONFIG_ERROR/);
});
test('desktop result carries the configuration of its own case', () => {
  const build = DesktopRunner.prototype._buildResult;
  const r = { execution: {}, dr: { executionConfig: { requested: { model: '另一任务' } } } };
  const result = build.call(r, { executionConfig: { requested: { model: '模型B' }, status: 'config_error' } }, {},
    { errorMsg: '[CONFIG_ERROR] readback failed', completed: false, startTime: 1, endTime: 2 });
  assert.equal(result.success, false);
  assert.equal(result.errorCode, 'CONFIG_ERROR');
  assert.equal(result.executionConfig.requested.model, '模型B');
});
