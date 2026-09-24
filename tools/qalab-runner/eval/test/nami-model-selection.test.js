const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
let browser, page;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { page = await browser.newPage(); });
afterEach(async () => { await page.close(); });
const target = 'DeepSeek-V4-Flash';

// Based on run 3279: .item-name is covered by button.item-hit-area.
async function fixture({ legacy = false, delay = 0, disabled = false, overlay = false,
  hiddenDuplicate = false, models = [target], change = true, current = '模型A', shadow = false } = {}) {
  await page.setContent('<div id="host"></div>');
  await page.evaluate(opts => {
    const root = opts.shadow ? document.querySelector('#host').attachShadow({ mode: 'open' }) : document.querySelector('#host');
    root.innerHTML = `<style>
      .row {position:relative;height:45px;width:250px}.item-name {height:100%;display:flex;align-items:center}
      .item-hit-area {position:absolute;inset:0;width:100%;height:100%;background:transparent;border:0}
      #menu {height:100px;overflow:auto;width:280px} #overlay {position:fixed;inset:0;z-index:999;background:#eee}
      </style><button id="trigger"><span id="selected"></span></button><div id="menu" role="listbox" hidden></div>`;
    window.clicks = []; window.opens = 0;
    root.querySelector('#selected').textContent = opts.current;
    const menu = root.querySelector('#menu');
    const populate = () => {
      menu.replaceChildren();
      for (const name of opts.models) {
        const row = document.createElement('div'); row.className = 'row';
        const label = document.createElement('div'); label.className = 'item-name'; label.textContent = name; row.append(label);
        let action = label;
        if (!opts.legacy) {
          action = document.createElement('button'); action.className = 'item-hit-area';
          action.dataset.testid = 'model-selector-option'; action.setAttribute('aria-label', name);
          action.disabled = opts.disabled; row.append(action);
        }
        action.onclick = () => {
          window.clicks.push(name); window.scrollAtClick = menu.scrollTop;
          if (opts.change) root.querySelector('#selected').textContent = name;
          menu.hidden = true;
        };
        menu.append(row);
      }
    };
    root.querySelector('#trigger').onclick = () => {
      window.opens++; menu.hidden = !menu.hidden;
      if (opts.delay) setTimeout(populate, opts.delay); else populate();
      if (opts.overlay && !root.querySelector('#overlay')) {
        const overlay = document.createElement('div'); overlay.id = 'overlay'; root.append(overlay);
      }
    };
    if (opts.hiddenDuplicate) {
      const hidden = document.createElement('div'); hidden.hidden = true;
      hidden.innerHTML = '<button id="trigger">隐藏入口</button><div class="item-name"></div><button class="item-hit-area" data-testid="model-selector-option"></button>';
      hidden.querySelector('.item-name').textContent = opts.models[0];
      hidden.querySelector('.item-hit-area').setAttribute('aria-label', opts.models[0]);
      document.body.prepend(hidden);
    }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') menu.hidden = true; });
  }, { legacy, delay, disabled, overlay, hiddenDuplicate, models, change, current, shadow });
}
function runner(model = target, timeout = 1800) {
  // Deliberately use the legacy config, as installed runners retain user config files.
  return new DialogRunner(null, { modelDropdownSelector: '#trigger', modelOptionSelector: '.item-name' },
    { dialogOptions: { model }, dialogOptionTimeoutMs: timeout }).attachToPage(page);
}

test('3279: clicks the actual model button above .item-name with old config', async () => {
  await fixture(); const r = runner();
  await r._applyDialogOptions();
  assert.deepEqual(await page.evaluate(() => window.clicks), [target]);
  assert.equal(r.executionConfig.status, 'verified');
  assert.deepEqual(r.executionConfig.observed.model, [target]);
});
test('legacy text options still work, hidden duplicate controls/options are ignored', async () => {
  await fixture({ legacy: true, hiddenDuplicate: true });
  await runner()._applyDialogOptions();
  assert.deepEqual(await page.evaluate(() => window.clicks), [target]);
});
test('new buttons work across open shadow roots and scroll to the target', async () => {
  await fixture({ shadow: true, models: [...Array.from({ length: 12 }, (_, i) => `Other-${i}`), target] });
  await runner()._applyDialogOptions();
  assert.ok(await page.evaluate(() => window.scrollAtClick > 0));
});
test('waits for menu population beyond 600ms without toggling it closed', async () => {
  await fixture({ delay: 900 }); await runner()._applyDialogOptions();
  assert.equal(await page.evaluate(() => window.opens), 1);
});
test('already-selected model is read back without opening the menu', async () => {
  await fixture({ current: target }); await runner()._applyDialogOptions();
  assert.equal(await page.evaluate(() => window.opens), 0);
});
test('waits for a delayed or temporarily disabled trigger', async () => {
  await fixture();
  await page.evaluate(() => {
    const trigger = document.querySelector('#trigger'); trigger.hidden = true; trigger.disabled = true;
    setTimeout(() => { trigger.hidden = false; }, 200);
    setTimeout(() => { trigger.disabled = false; }, 500);
  });
  await runner()._applyDialogOptions();
  assert.deepEqual(await page.evaluate(() => window.clicks), [target]);
});
test('selection is scoped to the work iframe, ignoring a matching outer-page control', async () => {
  await fixture();
  const markup = await page.locator('#host').innerHTML();
  await page.setContent('<button id="trigger">外层入口</button><iframe id="work"></iframe>');
  const frame = page.frames()[1];
  await frame.setContent(markup);
  await frame.evaluate(name => {
    const menu = document.querySelector('#menu');
    document.querySelector('#trigger').onclick = () => { menu.hidden = false; };
    menu.innerHTML = '<button data-testid="model-selector-option"></button>';
    const option = menu.querySelector('button'); option.setAttribute('aria-label', name);
    option.textContent = name;
    option.onclick = () => { document.querySelector('#selected').textContent = name; menu.hidden = true; };
  }, target);
  const r = runner(); r.frame = page.frameLocator('#work');
  await r._applyDialogOptions();
  assert.deepEqual(r.executionConfig.observed.model, [target]);
  assert.equal(await page.locator('#trigger').innerText(), '外层入口');
});
test('menu already open is used without toggling it shut', async () => {
  await fixture(); await page.locator('#trigger').click();
  await runner()._applyDialogOptions();
  assert.equal(await page.evaluate(() => window.opens), 1);
});
test('option replaced during a transient overlay is located again', async () => {
  await fixture({ overlay: true });
  await page.evaluate(() => setTimeout(() => {
    const option = document.querySelector('.item-hit-area');
    const replacement = option.cloneNode(true); replacement.onclick = option.onclick;
    option.replaceWith(replacement); document.querySelector('#overlay')?.remove();
  }, 500));
  await runner()._applyDialogOptions();
  assert.deepEqual(await page.evaluate(() => window.clicks), [target]);
});
test('does not substitute a similar model/version for the requested model', async () => {
  await fixture({ models: ['DeepSeek-V4', 'DeepSeek-V4-Flash-Pro'] });
  await assert.rejects(runner(target, 450)._applyDialogOptions(), /没有精确匹配.*DeepSeek-V4-Flash/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});
test('disabled model fails with an actionable reason, without selecting another model', async () => {
  await fixture({ disabled: true });
  await assert.rejects(runner(target, 450)._applyDialogOptions(), /目标选项不可用/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});
test('real overlay is not bypassed with force or DOM click', async () => {
  await fixture({ overlay: true }); const r = runner(target, 500);
  await assert.rejects(r._applyDialogOptions(), /目标选项被遮挡/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
  assert.match(r.executionConfig.controls['模型'].last_error, /intercepts pointer events/);
});
test('temporary overlay can disappear and selection is retried', async () => {
  await fixture({ overlay: true });
  await page.evaluate(() => setTimeout(() => document.querySelector('#overlay')?.remove(), 600));
  await runner()._applyDialogOptions();
  assert.deepEqual(await page.evaluate(() => window.clicks), [target]);
});
test('ambiguous visible model options fail instead of guessing', async () => {
  await fixture({ models: [target, target] });
  await assert.rejects(runner()._applyDialogOptions(), /多个可见选项/);
  assert.deepEqual(await page.evaluate(() => window.clicks), []);
});
test('clicking without changing the selected model still fails readback', async () => {
  await fixture({ change: false });
  await assert.rejects(runner()._applyDialogOptions(), /实际控件显示/);
});
test('changing mode must not silently reset an explicitly selected model', async () => {
  await fixture();
  await page.evaluate(() => {
    const mode = document.createElement('button'); mode.id = 'mode'; mode.textContent = '默认';
    mode.onclick = () => { document.querySelector('#mode-option').hidden = false; };
    const option = document.createElement('button'); option.id = 'mode-option'; option.hidden = true; option.textContent = '边想边做';
    option.onclick = () => { mode.textContent = '边想边做'; document.querySelector('#selected').textContent = '模型A'; option.hidden = true; };
    document.body.append(mode, option);
  });
  const r = runner(); r.platform.chatModeTriggerSelector = '#mode'; r.platform.chatModeOptionSelector = '#mode-option';
  r.execution.dialogOptions.chatMode = '边想边做';
  await assert.rejects(r._applyDialogOptions(), /实际控件显示/);
  assert.equal(r.executionConfig.status, 'config_error');
});
