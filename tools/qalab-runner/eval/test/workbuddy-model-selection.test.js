const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { workbuddy } = require('../config/default.config');

let browser, page;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { page = await browser.newPage(); });
afterEach(async () => { await page.close(); });

// 独立合成页面，复现 5.5.6 的名称/徽标/倍率分离结构及列表滚动。
async function fixture({ models = ['GLM-5.3-Flash', 'GLM-5.3'], delay = 0,
  disabled = false, change = true, hiddenDuplicate = false, open = true } = {}) {
  await page.setContent(`<button class="cr-model-selector__trigger"><span id="selected">快速</span></button>
    <div id="menu" role="listbox" hidden><div id="models" style="height:100px;overflow:auto"></div></div>
    <div contenteditable="true" role="textbox"></div>`);
  await page.evaluate(({ models, delay, disabled, change, hiddenDuplicate, open }) => {
    window.clicks = [];
    window.sends = 0;
    const menu = document.querySelector('#menu');
    const list = document.querySelector('#models');
    const makeOption = name => {
      const item = document.createElement('div');
      item.className = 'cr-model-selector__item';
      item.style.cssText = 'height:36px;display:flex;gap:10px';
      item.setAttribute('role', 'option');
      item.setAttribute('aria-disabled', String(disabled && name === 'GLM-5.3'));
      item.innerHTML = '<div class="cr-model-selector__item-info"><span class="cr-model-selector__item-name"></span>'
        + '<span class="cr-model-selector__item-badge">夜间折扣</span></div>'
        + '<span class="cr-model-selector__item-credits">0.79x</span>';
      item.querySelector('.cr-model-selector__item-name').textContent = name;
      item.onclick = () => {
        window.clicks.push(name);
        window.scrollAtClick = list.scrollTop;
        if (change) document.querySelector('#selected').textContent = name;
        menu.hidden = true;
      };
      return item;
    };
    if (hiddenDuplicate) {
      const hidden = document.createElement('div');
      hidden.hidden = true;
      hidden.append(makeOption('GLM-5.3'));
      document.body.prepend(hidden);
    }
    document.querySelector('button').onclick = () => {
      if (!open) return;
      menu.hidden = !menu.hidden;
      if (!list.childElementCount) {
        if (delay) list.append(makeOption('快速'));
        setTimeout(() => list.replaceChildren(...models.map(makeOption)), delay);
      }
    };
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') menu.hidden = true;
      if (event.key === 'Enter' && event.target.isContentEditable) window.sends++;
    });
  }, { models, delay, disabled, change, hiddenDuplicate, open });
}

function runner(model, selectors = {}) {
  return new WorkbuddyRunner(page, { ...workbuddy, ...selectors }, { dialogOptions: { model } });
}

for (const requested of ['GLM-5.3', 'glm-5.3', ' GlM-5.3 ']) {
  test(`selects ${JSON.stringify(requested)} by name, ignoring badges, credits and case`, async () => {
    await fixture();
    const r = runner(requested);
    await r._applyDialogOptions();
    assert.equal(r.executionConfig.status, 'verified');
    assert.deepEqual(r.executionConfig.observed.model, ['GLM-5.3']);
    assert.deepEqual(await page.evaluate(() => window.clicks), ['GLM-5.3']);
    assert.equal(await page.locator('#menu').isVisible(), false);
  });
}

test('searches all mounted models and scrolls an offscreen result into view', async () => {
  await fixture({ models: [...Array.from({ length: 16 }, (_, i) => `Model-${i}`), 'GLM-5.3'], hiddenDuplicate: true });
  const r = runner('glm-5.3');
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
  assert.ok(await page.evaluate(() => window.scrollAtClick > 0));
  assert.deepEqual(await page.evaluate(() => window.clicks), ['GLM-5.3']);
});

test('waits for asynchronously populated models beyond the former 600 ms delay', async () => {
  await fixture({ delay: 900 });
  const r = runner('glm-5.3');
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
});

test('legacy item-info configuration still extracts the name without badges', async () => {
  await fixture();
  const r = runner('glm-5.3', { modelOptionNameSelector: '.cr-model-selector__item-info' });
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
});

test('custom name selector excludes sibling descriptions when standard classes are absent', async () => {
  await fixture();
  // 模型列表在点击后挂载，在事件回调完成后重命名名称节点。
  await page.evaluate(() => {
    new MutationObserver(() => {
      document.querySelectorAll('.cr-model-selector__item-name').forEach(el => { el.className = 'custom-name'; });
    }).observe(document.querySelector('#models'), { childList: true, subtree: true });
  });
  const r = runner('glm-5.3', { modelOptionNameSelector: '.custom-name' });
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
});

test('never substitutes another version or suffix; failure lists actual models and blocks sending', async () => {
  await fixture({ models: ['GLM-5.30', 'GLM-5.3-Flash', 'GLM-5.2'], hiddenDuplicate: true });
  const r = runner('GLM-5.3');
  r._dismissShareUi = async () => true;
  const testCase = { question: 'model selection regression' };
  await assert.rejects(r._sendOne(testCase), /找不到模型「GLM-5.3」。当前模型列表：GLM-5.30、GLM-5.3-Flash、GLM-5.2/);
  assert.equal(testCase.executionConfig.status, 'config_error');
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
  assert.equal(await page.locator('[contenteditable]').innerText(), '');
  assert.equal(await page.evaluate(() => window.sends), 0);
  assert.equal(await page.locator('#menu').isVisible(), false);
});

test('reports an unavailable model without clicking it', async () => {
  await fixture({ disabled: true });
  const r = runner('glm-5.3');
  await assert.rejects(r._applyDialogOptions(), /模型「GLM-5.3」在当前列表中不可选/);
  assert.equal(r.executionConfig.status, 'config_error');
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});

test('rejects a click whose selected-model readback never changes', async () => {
  await fixture({ change: false });
  const r = runner('glm-5.3');
  await assert.rejects(r._applyDialogOptions(), /实际控件显示「快速」/);
  assert.equal(r.executionConfig.status, 'config_error');
});

test('unopened/empty menu gives a loading diagnostic instead of selecting hidden text', async () => {
  await fixture({ open: false, hiddenDuplicate: true });
  await assert.rejects(runner('glm-5.3')._applyDialogOptions(), /模型列表未加载或没有可见选项/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});

test('ambiguous same-name models are not silently chosen', async () => {
  await fixture({ models: ['GLM-5.3', 'glm-5.3'] });
  await assert.rejects(runner('GLM-5.3')._applyDialogOptions(), /多个同名模型/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});

test('unspecified model only observes the current selection', async () => {
  await fixture();
  const r = runner(undefined);
  await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'observed');
  assert.deepEqual(r.executionConfig.observed.model, ['快速']);
  assert.equal(await page.locator('#models').count(), 1);
  assert.equal(await page.locator('#menu').isVisible(), false);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});
