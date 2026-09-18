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

test('model data arriving after the old 4-second limit is awaited before selecting and sending', async () => {
  await fixture();
  await page.evaluate(() => {
    const button = document.querySelector('button'); button.remove();
    setTimeout(() => document.body.prepend(button), 4300);
  });
  const r = runner('glm-5.3', { modelReadyTimeoutMs: 6500 });
  r._dismissShareUi = async () => true;
  const tc = { question: '就绪后发送' };
  await r._sendOne(tc);
  assert.equal(tc.executionConfig.status, 'verified');
  assert.deepEqual(await page.evaluate(() => window.clicks), ['GLM-5.3']);
  assert.equal(await page.evaluate(() => window.sends), 1);
});

test('hidden first model trigger and editor never block the visible input region', async () => {
  await fixture();
  await page.evaluate(() => {
    const hidden = document.createElement('div'); hidden.style.display = 'none';
    hidden.innerHTML = '<button class="cr-model-selector__trigger">旧模型</button><div contenteditable="true" role="textbox"></div>';
    hidden.querySelector('button').onclick = () => { window.wrong = true; };
    document.body.prepend(hidden);
  });
  const r = runner('glm-5.3', { modelReadyTimeoutMs: 1000 });
  r._dismissShareUi = async () => true;
  await r._sendOne({ question: '只发送一次' });
  assert.equal(await page.evaluate(() => window.wrong), undefined);
  assert.equal(await page.evaluate(() => window.sends), 1);
  assert.equal(await page.locator('[contenteditable]').first().textContent(), '');
  assert.deepEqual(r.executionConfig.observed.model, ['GLM-5.3']);
});

test('model trigger scoped to the visible compose container ignores other page controls', async () => {
  await fixture();
  await page.evaluate(() => {
    const container = document.createElement('div'); container.className = 'cr-input-container';
    container.append(document.querySelector('button'), document.querySelector('[contenteditable]'));
    document.body.append(container);
    const unrelated = document.createElement('button'); unrelated.className = 'cr-model-selector__trigger'; unrelated.textContent = '其他区域';
    unrelated.onclick = () => { window.wrong = true; }; document.body.prepend(unrelated);
  });
  const r = runner('glm-5.3'); await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
  assert.equal(await page.evaluate(() => window.wrong), undefined);
});

test('known semantic model entry works when the configured style selector is absent', async () => {
  await fixture();
  await page.locator('button').evaluate(el => {
    el.className = 'restyled-model-trigger'; el.setAttribute('aria-label', 'Select model'); el.setAttribute('aria-haspopup', 'listbox');
  });
  const r = runner('glm-5.3'); await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
});

test('temporarily disabled model entry waits until it is usable', async () => {
  await fixture();
  await page.locator('button').evaluate(el => { el.disabled = true; setTimeout(() => { el.disabled = false; }, 350); });
  const r = runner('glm-5.3', { modelReadyTimeoutMs: 1500 });
  await r._applyDialogOptions(); assert.equal(r.executionConfig.status, 'verified');
});

test('blocked trigger replaced by React is reacquired through semantic properties', async () => {
  await fixture();
  await page.evaluate(() => {
    const button = document.querySelector('button');
    const overlay = document.createElement('div'); overlay.style.cssText = 'position:fixed;inset:0;z-index:999;background:white'; document.body.append(overlay);
    setTimeout(() => {
      const replacement = button.cloneNode(true); replacement.className = 'new-model-trigger';
      replacement.setAttribute('aria-label', 'Select model'); replacement.setAttribute('aria-haspopup', 'listbox');
      replacement.onclick = button.onclick; button.replaceWith(replacement); overlay.remove();
    }, 150);
  });
  const r = runner('glm-5.3', { modelReadyTimeoutMs: 3000 });
  r._dismissShareUi = async () => true;
  await r._sendOne({ question: '重绘后发送' });
  assert.deepEqual(await page.evaluate(() => window.clicks), ['GLM-5.3']);
  assert.equal(await page.evaluate(() => window.sends), 1);
});

for (const state of ['missing', 'disabled', 'ambiguous', 'obscured']) test(`unready ${state} entry has diagnostics and never sends or falls back to another model`, async () => {
  await fixture();
  await page.evaluate(state => {
    const button = document.querySelector('button');
    if (state === 'missing') button.remove();
    if (state === 'disabled') button.disabled = true;
    if (state === 'ambiguous') document.body.append(button.cloneNode(true));
    if (state === 'obscured') {
      const overlay = document.createElement('div'); overlay.style.cssText = 'position:fixed;inset:0;z-index:999;background:white'; document.body.append(overlay);
    }
  }, state);
  const r = runner('glm-5.3', { modelReadyTimeoutMs: 220 }); r._dismissShareUi = async () => true;
  const tc = { question: '不应发出' };
  await assert.rejects(r._sendOne(tc), /WORKBUDDY_MODEL_NOT_READY.*未发送题目/);
  assert.equal(tc.executionConfig.status, 'ui_not_ready');
  assert.ok(tc.executionConfig.modelControl);
  assert.equal(await page.evaluate(() => window.sends), 0);
  assert.equal(await page.locator('[contenteditable]').innerText(), '');
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});

test('already expanded selector is not toggled shut before choosing a model', async () => {
  await fixture();
  await page.locator('button').click();
  await page.locator('button').evaluate(el => el.setAttribute('aria-expanded', 'true'));
  const r = runner('glm-5.3'); await r._applyDialogOptions();
  assert.equal(r.executionConfig.status, 'verified');
  assert.deepEqual(await page.evaluate(() => window.clicks), ['GLM-5.3']);
});

test('no requested model observes the visible trigger without clicking a hidden duplicate', async () => {
  await fixture();
  await page.evaluate(() => document.body.insertAdjacentHTML('afterbegin', '<button class="cr-model-selector__trigger" hidden>历史模型</button>'));
  const r = runner(undefined); await r._applyDialogOptions();
  assert.deepEqual(r.executionConfig.observed.model, ['快速']);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});
