const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
const platform = require('../config/default.config').platform;
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function fixture(t, { embedded = false, delay = 0, stay = false, reversed = false } = {}) {
  const page = await browser.newPage(); t.after(() => page.close());
  let frame = page.mainFrame();
  if (embedded) {
    await page.setContent('<iframe name="work" style="width:100%;height:800px" srcdoc="<body></body>"></iframe>');
    frame = page.frame('work');
  }
  await frame.evaluate(({ delay, stay, reversed }) => {
    const app = document.createElement('openclaw-app'); document.body.append(app);
    const root = app.attachShadow({ mode: 'open' });
    root.innerHTML = '<div class="chat-group user">当前测试</div><div class="chat-group assistant">等待确认</div><chat-question-form-card></chat-question-form-card>';
    const card = root.querySelector('chat-question-form-card');
    card.questionKey = 'protected-request-1';
    // 与产品 DOM 一致：卡片本身 light DOM，外围 openclaw-app 是 open shadow root。
    card.innerHTML = '<p class="ask-form__title">【单选】是否允许执行本次受保护操作？</p>' +
      '<div class="ask-form__desc">NamiWork 的统一安全策略要求确认。\n触发来源：namiguard_sdk / file_access\n创建：C:\\work\\pipeline.mjs</div>' +
      '<div class="ask-form__options" role="listbox" aria-multiselectable="false"></div>' +
      '<button class="ask-form__btn--cancel">取消</button><button class="ask-form__btn--ok">提交</button>';
    const labels = ['拒绝本次操作', '允许本次操作']; if (reversed) labels.reverse();
    for (const label of labels) {
      const option = document.createElement('div'); option.className = 'ask-form__option'; option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(label === '拒绝本次操作'));
      if (label === '拒绝本次操作') option.classList.add('is-selected');
      option.innerHTML = '<span class="ask-form__option-label"></span><span class="ask-form__option-desc">只影响本次请求</span>';
      option.querySelector('span').textContent = label;
      option.onclick = () => setTimeout(() => {
        card.querySelectorAll('.ask-form__option').forEach(el => {
          el.setAttribute('aria-selected', String(el === option)); el.classList.toggle('is-selected', el === option);
        });
      }, delay);
      card.querySelector('.ask-form__options').append(option);
    }
    window.answers = []; window.card = card;
    card.querySelector('.ask-form__btn--ok').onclick = () => {
      window.answers.push(card.querySelector('[aria-selected="true"] .ask-form__option-label').textContent);
      if (!stay) card.remove();
    };
  }, { delay, stay, reversed });
  const r = new DialogRunner(null, platform, {}).attachToPage(page);
  r.frame = frame; r._liveFrame = () => frame;
  r._beginTurn({ groupCount: 0, footerCount: 0 });
  return { page, frame, r };
}

for (const embedded of [false, true]) test(`protected operation overrides default deny in ${embedded ? 'iframe' : 'main'} shadow DOM`, async t => {
  const { r, frame } = await fixture(t, { embedded, delay: 100 });
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.deepEqual(await frame.evaluate(() => window.answers), ['允许本次操作']);
  assert.equal(await r._dismissConfirmDialogs(), false);
});

test('permission choice follows the exact label even if option order changes', async t => {
  const { r, frame } = await fixture(t, { reversed: true });
  await r._dismissConfirmDialogs();
  assert.deepEqual(await frame.evaluate(() => window.answers), ['允许本次操作']);
});

test('submitted card is not clicked twice; stalled submission fails explicitly', async t => {
  const { r, frame } = await fixture(t, { stay: true });
  await r._dismissConfirmDialogs();
  for (let i = 0; i < 3; i++) assert.equal(await r._dismissConfirmDialogs(), true);
  assert.deepEqual(await frame.evaluate(() => window.answers), ['允许本次操作']);
  r._protectedOperation.pending.submittedAt -= 16000;
  await assert.rejects(r._dismissConfirmDialogs(), /NAMI_PROTECTED_OPERATION.*15 秒/);
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
});

for (const failure of ['unknown-option', 'selection-failed', 'disabled-submit', 'multi-choice']) test(`${failure} never falls through to generic default-deny submit`, async t => {
  const { r, frame } = await fixture(t);
  await frame.evaluate(failure => {
    if (failure === 'unknown-option') window.card.querySelectorAll('.ask-form__option-label')[1].textContent = '永久允许';
    if (failure === 'selection-failed') window.card.querySelectorAll('.ask-form__option')[1].onclick = () => {};
    if (failure === 'disabled-submit') window.card.querySelector('.ask-form__btn--ok').disabled = true;
    if (failure === 'multi-choice') window.card.querySelector('.ask-form__options').setAttribute('aria-multiselectable', 'true');
  }, failure);
  r._handleAskForms = async () => { assert.fail('must not use generic submit'); };
  await assert.rejects(r._dismissConfirmDialogs(), /NAMI_PROTECTED_OPERATION/);
  assert.deepEqual(await frame.evaluate(() => window.answers), []);
});

test('ordinary AskUser continues through its existing handler', async t => {
  const { r, frame } = await fixture(t);
  await frame.evaluate(() => window.card.querySelector('.ask-form__title').textContent = '请选择输出格式');
  let calls = 0; r._handleAskForms = async () => { calls++; return true; };
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(calls, 1);
  assert.deepEqual(await frame.evaluate(() => window.answers), []);
});

test('pending approval state stays with its task when desktop switches conversations', async t => {
  const { r, frame } = await fixture(t, { stay: true });
  await r._dismissConfirmDialogs(); const first = r._captureTurnState();
  r._beginTurn({ groupCount: 0, footerCount: 0 });
  assert.equal(r._protectedOperation.pending, null);
  r._restoreTurnState(first);
  await r._dismissConfirmDialogs();
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
  await frame.evaluate(() => window.card.questionKey = 'protected-request-2');
  await r._dismissConfirmDialogs();
  assert.equal(await frame.evaluate(() => window.answers.length), 2, 'a distinct request can be approved');
});

test('one task handles consecutive requests with identical descriptions without an empty poll', async t => {
  const { r, frame } = await fixture(t, { stay: true });
  for (let request = 1; request <= 3; request++) {
    await frame.evaluate(request => {
      window.card.questionKey = `protected-request-${request}`;
      window.card.querySelectorAll('.ask-form__option').forEach(option => {
        const denied = option.textContent.startsWith('拒绝本次操作');
        option.setAttribute('aria-selected', String(denied));
        option.classList.toggle('is-selected', denied);
      });
    }, request);
    assert.equal(await r._dismissConfirmDialogs(), true);
    assert.equal(await r._dismissConfirmDialogs(), true, 'pending request is not resubmitted');
    assert.equal(await frame.evaluate(() => window.answers.length), request);
  }
  assert.deepEqual(await frame.evaluate(() => window.answers), Array(3).fill('允许本次操作'));
});

test('identical card can appear again after its previous submission has closed', async t => {
  const { r, frame } = await fixture(t);
  await r._dismissConfirmDialogs();
  assert.equal(await r._dismissConfirmDialogs(), false);
  await frame.evaluate(() => {
    document.querySelector('openclaw-app').shadowRoot.append(window.card);
    window.card.querySelectorAll('.ask-form__option').forEach(option => {
      const denied = option.textContent.startsWith('拒绝本次操作');
      option.setAttribute('aria-selected', String(denied));
      option.classList.toggle('is-selected', denied);
    });
  });
  await r._dismissConfirmDialogs();
  assert.deepEqual(await frame.evaluate(() => window.answers), Array(2).fill('允许本次操作'));
});

test('response wait processes three approvals before accepting a completion footer', async t => {
  const { r, frame } = await fixture(t, { stay: true, embedded: true });
  await frame.evaluate(() => {
    const card = window.card;
    card.querySelector('.ask-form__btn--ok').onclick = () => {
      window.answers.push(card.querySelector('[aria-selected="true"] .ask-form__option-label').textContent);
      if (window.answers.length === 3) { card.remove(); return; }
      card.questionKey = `protected-request-${window.answers.length + 1}`;
      card.querySelectorAll('.ask-form__option').forEach(option => {
        const denied = option.textContent.startsWith('拒绝本次操作');
        option.setAttribute('aria-selected', String(denied));
        option.classList.toggle('is-selected', denied);
      });
    };
  });
  r.execution = { responseTimeout: 15000, completionSettleMs: 10 };
  // A stale completion signal must not end the task while an approval is visible.
  r._probeGenerating = async () => false;
  r._hasCurrentCompletionFooter = async () => true;
  assert.deepEqual(await r.waitForResponseComplete(), { completed: true, reason: 'footer' });
  assert.deepEqual(await frame.evaluate(() => window.answers), Array(3).fill('允许本次操作'));
});
