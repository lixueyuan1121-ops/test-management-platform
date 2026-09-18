const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
const DesktopRunner = require('../src/desktop-runner');
const { shareSelectionState, ensureAllSelected } = require('../src/share-select');
const defaultPlatform = require('../config/default.config').platform;

let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
const platform = { answerGroupSelector: '.chat-group.assistant', answerSelector: '.chat-bubble.has-copy',
  costSelector: '.chat-token-cost', stopSignalSelector: '#stop', taskListItemSelector: '.task' };

async function fixture(t, { card = true, disabled = false, error = '', delay = null, surface = 'page', sessionUrl = false } = {}) {
  const page = await browser.newPage();
  t.after(() => page.close());
  if (sessionUrl) {
    await page.route('https://fixture.work.n.cn/**', route => route.fulfill({ contentType: 'text/html', body: '<body></body>' }));
    await page.goto('https://fixture.work.n.cn/chat');
  }
  let ctx = page;
  if (surface === 'iframe') {
    await page.setContent('<iframe name="nami-work" style="width:100%;height:800px" srcdoc="<body></body>"></iframe>');
    ctx = page.frame('nami-work');
  }
  await ctx.evaluate(({ card, disabled, error, delay, surface }) => {
    window.fixtureRoot = document.body;
    if (surface !== 'page') {
      const app = document.createElement('openclaw-app');
      document.body.append(app);
      window.fixtureRoot = app.attachShadow({ mode: 'open' });
    }
    window.fixtureRoot.innerHTML = '<main></main><button id="stop" style="display:none">停止</button>';
    window.clicks = 0;
    customElements.define('ui-tooltip', class extends HTMLElement {
      connectedCallback() { this.attachShadow({ mode: 'open' }).innerHTML = '<slot></slot>'; }
    });
    customElements.define('work-continuation-card', class extends HTMLElement {
      connectedCallback() {
        if (this.shadowRoot) return;
        this.attachShadow({ mode: 'open' }).innerHTML = `<style>:host { display:block } ui-tooltip { display:inline-flex }</style>
          <section class="card" aria-label="需要更深入的结果？继续使用「工作」"><div class="title">需要更深入的结果？继续使用「工作」</div>
          <div role="alert">${error}</div><ui-tooltip><span class="action"><button type="button" aria-label="继续工作" ${disabled ? 'disabled' : ''}>继续工作</button></span></ui-tooltip></section>`;
        this.shadowRoot.querySelector('button').onclick = () => {
          window.clicks++;
          this.shadowRoot.querySelector('button').disabled = true;
          if (delay !== null) setTimeout(() => window.appendAnswer('最终产物', '23 算力豆'), delay);
        };
      }
    });
    window.appendAnswer = (answer, cost, withCard = false) => {
      window.fixtureRoot.querySelector('main').insertAdjacentHTML('beforeend', `<div class="chat-group user">追加提问</div>
        <div class="chat-group assistant"><div class="chat-bubble has-copy">${answer}</div>
        ${withCard ? '<work-continuation-card></work-continuation-card>' : ''}
        ${cost == null ? '' : `<div class="chat-token-cost">${cost}</div>`}</div>`);
    };
    window.appendAnswer('历史测评', '999 算力豆');
    window.appendAnswer('首轮文字答案', '1 算力豆', card);
  }, { card, disabled, error, delay, surface });
  const r = new DialogRunner(null, platform, { beanCostTimeoutMs: 0, beanCostReloadOnce: false,
    completionSettleMs: 150, responseTimeout: 8000 });
  r.attachToPage(page);
  r.frame = ctx;
  r._beginTurn({ groupCount: 1, footerCount: 1 });
  return { page, r, ctx };
}

async function useRealCompose(page, r, state = 'send') {
  r.platform = { ...r.platform, stopSignalSelector: defaultPlatform.stopSignalSelector };
  await page.locator('#stop').evaluate((el, state) => {
    el.className = 'btn send-btn';
    el.style.display = 'block';
    // 真实客户端的暂停按钮目前也可能被标成 send-button，不能仅依赖 testid。
    el.setAttribute('data-testid', 'send-button');
    el.innerHTML = `<img class="send-btn__icon" src="/icon_${state}.svg" aria-hidden="true">`;
  }, state);
}

test('enabled send arrow must not prevent clicking Continue work or completing its final answer', async t => {
  const { page, r } = await fixture(t, { delay: 100 });
  await useRealCompose(page, r);
  assert.equal(await r._probeGenerating(), false, 'an enabled Send arrow is not a Stop signal');
  assert.deepEqual(await r.waitForResponseComplete(), { completed: true, reason: 'footer' });
  assert.equal(await page.evaluate(() => window.clicks), 1);
  assert.equal((await r.extractLastAnswer()).text, '最终产物');
  assert.equal((await r.extractBeanCost()).value, '24');
});

test('an actionable continuation card takes precedence over a stale Stop signal', async t => {
  const { page, r } = await fixture(t);
  await useRealCompose(page, r, 'pause');
  assert.equal(await r._probeGenerating(), true);
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await page.evaluate(() => window.clicks), 1);
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await page.evaluate(() => window.clicks), 1, 'pending continuation is never sent twice');
});

test('desktop probe distinguishes Send from Pause using actual icon even with incorrect testid', async t => {
  const { page, r } = await fixture(t, { card: false });
  const desktop = Object.create(DesktopRunner.prototype);
  desktop.dr = r; desktop._fl = () => page;
  await useRealCompose(page, r);
  desktop.platform = r.platform;
  assert.equal((await desktop._probeState()).generating, false);
  await useRealCompose(page, r, 'pause');
  assert.equal((await desktop._probeState()).generating, true);
});

test('legacy broad selectors still distinguish send arrows, old stop SVGs and disabled stopping spinners', async t => {
  const { page, r } = await fixture(t, { card: false });
  await useRealCompose(page, r);
  r.platform.stopSignalSelector = 'button.send-btn:not(.send-btn--noop):not([disabled])';
  assert.equal(await r._probeGenerating(), false);
  await page.locator('#stop').evaluate(el => el.innerHTML = '<svg><rect rx="4" width="16" height="16"></rect></svg>');
  assert.equal(await r._probeGenerating(), true);
  r.platform.stopSignalSelector = defaultPlatform.stopSignalSelector;
  await page.locator('#stop').evaluate(el => {
    el.disabled = true;
    el.innerHTML = '<svg class="send-btn-spinner"><circle cx="12" cy="12" r="9"></circle></svg>';
  });
  assert.equal(await r._probeGenerating(), true);
  await page.locator('#stop').evaluate(el => el.innerHTML = '<img src="/icon_send.svg">');
  assert.equal(await r._probeGenerating(), false);
});

for (const surface of ['shadow', 'iframe']) {
  test(`continuation crosses nested shadows in ${surface} and preserves trace boundary and costs`, async t => {
    const { r, ctx } = await fixture(t, { delay: 100, surface });
    await useRealCompose(ctx, r);
    let boundaries = 0;
    r.beforeContinueWork = async () => boundaries++;
    assert.equal((await r.waitForResponseComplete()).completed, true);
    assert.equal(await ctx.evaluate(() => window.clicks), 1);
    assert.equal(boundaries, 1);
    assert.equal((await r.extractLastAnswer()).text, '最终产物');
    assert.equal((await r.extractBeanCost()).value, '24');
  });
}

test('open-shadow continuation is clicked once; old footer cannot complete the automatic follow-up', async t => {
  const { page, r } = await fixture(t);
  let traceBoundaries = 0;
  r.beforeContinueWork = async () => { traceBoundaries++; assert.equal(await page.evaluate(() => window.clicks), 0); };
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await page.evaluate(() => window.clicks), 1);
  assert.equal(traceBoundaries, 1);
  assert.equal(await r._hasCurrentCompletionFooter(), false);
  assert.equal((await r.extractBeanCost()).value, '');
  await page.evaluate(() => window.appendAnswer('后续工作', null));
  assert.equal(await r._dismissConfirmDialogs(), false);
  assert.equal(await r._hasCurrentCompletionFooter(), false, 'old footer is still present');
  await page.locator('.chat-group.assistant').last().evaluate(el => el.insertAdjacentHTML('beforeend', '<div class="chat-token-cost">23 算力豆</div>'));
  assert.equal(await r._hasCurrentCompletionFooter(), true);
  assert.equal((await r.extractBeanCost()).value, '24');
  assert.equal((await r.extractLastAnswer()).text, '后续工作');
});

test('response loop waits through continuation and returns only the final answer', async t => {
  const { page, r } = await fixture(t, { delay: 250 });
  assert.deepEqual(await r.waitForResponseComplete(), { completed: true, reason: 'footer' });
  assert.equal((await r.extractLastAnswer()).text, '最终产物');
  assert.equal((await r.extractBeanCost()).value, '24');
  assert.equal(await page.evaluate(() => window.clicks), 1);
});

test('a card rendered after the first footer is rechecked before finishing', async t => {
  const { page, r } = await fixture(t, { card: false, delay: 100 });
  await page.evaluate(() => setTimeout(() => {
    document.querySelectorAll('.chat-group.assistant')[1].insertAdjacentHTML('beforeend', '<work-continuation-card></work-continuation-card>');
  }, 50));
  assert.equal((await r.waitForResponseComplete()).completed, true);
  assert.equal(await page.evaluate(() => window.clicks), 1);
  assert.equal((await r.extractLastAnswer()).text, '最终产物');
});

test('failed, unavailable and unstarted continuations fail explicitly without repeat clicks', async t => {
  const { r, page } = await fixture(t);
  await r._dismissConfirmDialogs();
  r._workContinuation.pending.clickedAt -= 120001;
  await assert.rejects(() => r._dismissConfirmDialogs(), /WORK_CONTINUATION_TIMEOUT/);
  assert.equal(await page.evaluate(() => window.clicks), 1);
  const unavailable = await fixture(t, { disabled: true });
  await unavailable.r._dismissConfirmDialogs();
  unavailable.r._workContinuation.waitingSince -= 120001;
  await assert.rejects(() => unavailable.r._dismissConfirmDialogs(), /WORK_CONTINUATION_UNAVAILABLE/);
  assert.equal(await unavailable.page.evaluate(() => window.clicks), 0);
  const failed = await fixture(t, { error: '继续工作失败，请重试' });
  await assert.rejects(() => failed.r._dismissConfirmDialogs(), /WORK_CONTINUATION_FAILED/);
});

test('repeated continuation is bounded, while historical cards are ignored', async t => {
  const { page, r } = await fixture(t);
  r.execution.workContinuationMaxRounds = 2;
  await r._dismissConfirmDialogs();
  await page.evaluate(() => window.appendAnswer('第二轮', '2 算力豆', true));
  await r._dismissConfirmDialogs();
  await page.evaluate(() => window.appendAnswer('第三轮', '3 算力豆', true));
  await assert.rejects(() => r._dismissConfirmDialogs(), /WORK_CONTINUATION_LIMIT/);
  assert.equal(await page.evaluate(() => window.clicks), 2);
  r._beginTurn(await r._captureBaseline());
  assert.equal(await r._dismissConfirmDialogs(), false, 'prior evaluation card must not be clicked');
});

test('a card disabled during generation does not consume the idle-card timeout', async t => {
  const { page, r } = await fixture(t, { disabled: true });
  await page.locator('#stop').evaluate(el => el.style.display = 'block');
  r._workContinuation.waitingSince = Date.now() - 120001;
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(r._workContinuation.waitingSince, null);
  assert.equal(await page.evaluate(() => window.clicks), 0);
});

test('existing tool confirmation is handled before a continuation card so generation can keep moving', async t => {
  const { page, r } = await fixture(t);
  let blocked = true;
  r._handleToolConfirm = async () => { const pending = blocked; blocked = false; return pending; };
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await page.evaluate(() => window.clicks), 0);
  assert.equal(await r._dismissConfirmDialogs(), true);
  assert.equal(await page.evaluate(() => window.clicks), 1);
});

test('desktop completion probe uses the current continuation stage', async t => {
  const { page, r } = await fixture(t);
  const desktop = Object.create(DesktopRunner.prototype);
  desktop.dr = r; desktop.platform = platform; desktop._fl = () => page;
  assert.equal((await desktop._probeState()).footerN, 1);
  await r._dismissConfirmDialogs();
  const pending = await desktop._probeState();
  assert.equal(pending.footerN, 0);
  assert.equal(pending.hasBubble, false);
  await page.evaluate(() => window.appendAnswer('后续工作', null));
  assert.equal((await desktop._probeState()).footerN, 0);
});

test('desktop patrol preserves continuation and bean boundaries across two tasks', async t => {
  const { page, r } = await fixture(t, { card: false, sessionUrl: true });
  await page.evaluate(() => {
    window.tasks = {};
    window.renderTask = id => {
      window.currentTask = id;
      history.replaceState({}, '', '?sid=' + id);
      const task = window.tasks[id];
      document.querySelector('main').innerHTML = `<div class="chat-group user">${id}</div>`;
      window.appendAnswer(`${id}首轮`, `${id === 'A' ? 1 : 7} 算力豆`, id === 'A' && !task.continued);
      if (task.continued) window.appendAnswer('A最终答案', '23 算力豆');
      const card = document.querySelector('work-continuation-card');
      if (card) card.shadowRoot.querySelector('button').onclick = () => { window.clicks++; task.continued = true; };
    };
    const nav = document.createElement('nav'); document.body.prepend(nav);
    for (const id of ['A', 'B']) {
      const button = document.createElement('button'); button.className = 'task'; button.textContent = id;
      button.dataset.sessionId = id;
      button.onclick = () => window.renderTask(id); nav.append(button);
    }
  });
  const d = Object.create(DesktopRunner.prototype);
  Object.assign(d, { dr: r, page, platform, execution: { sendIntervalMs: 0, desktopPatrolMs: 1, responseTimeout: 10000 },
    _fl: () => page, _focus: async () => {}, _log() {}, _warn() {}, _sleep: ms => page.waitForTimeout(ms),
    watcher: { _waitSwitchSettled: async () => {} },
    _readFirstQuery: () => page.locator('.chat-group.user').first().innerText(),
    _openCleanConversation: async () => { await page.locator('main').evaluate(el => el.innerHTML = ''); return true; },
    _currentSelectedIndex: async () => page.evaluate(() => window.currentTask === 'A' ? 0 : 1),
    _readItemRunning: async () => false,
    _sendOne: async tc => {
      r._beginTurn(await r._captureBaseline());
      await page.evaluate(id => { window.tasks[id] = {}; window.renderTask(id); }, tc.caseId);
    },
    _extractCurrent: async () => ({ answer: (await r.extractLastAnswer()).text, beanCost: (await r.extractBeanCost()).value }),
  });
  const results = await d.runConcurrent(['A', 'B'].map(caseId => ({ caseId, question: caseId })));
  assert.equal(results[0].success, true);
  assert.equal(results[0].answer, 'A最终答案');
  assert.equal(results[0].beanCost, '24');
  assert.equal(results[1].beanCost, '7');
  assert.equal(await page.evaluate(() => window.clicks), 1);
});

test('share all requires the toolbar checkmark AND every item, supports shadow DOM, and never toggles already checked content', async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.setContent('<share-fixture></share-fixture>');
  await page.evaluate(() => {
    const root = document.querySelector('share-fixture').attachShadow({ mode: 'open' });
    const tick = '<svg><path d="M4.5 8.5L6.5 10.5L11.5 5.5"></path></svg>';
    const partial = '<svg><line x1="4" y1="8" x2="12" y2="8"></line></svg>';
    root.innerHTML = '<div class="chat-share-panel">' + [0, 1, 2, 3].map(i => `<div class="chat-share-panel__item"><div class="chat-share-panel__checkbox">${i < 2 ? tick : '<svg></svg>'}</div></div>`).join('') +
      `<div class="chat-share-panel__toolbar"><div class="chat-share-panel__checkbox">${partial}</div><button>全部</button></div></div>`;
    root.querySelector('button').onclick = () => root.querySelectorAll('.chat-share-panel__checkbox').forEach(el => el.innerHTML = tick);
  });
  const boxes = page.locator('.chat-share-panel__checkbox');
  const isAllChecked = async () => (await boxes.evaluateAll(shareSelectionState)).allChecked;
  assert.equal(await isAllChecked(), false);
  const select = () => ensureAllSelected({ isAllChecked, clickSelectAll: () => page.getByRole('button', { name: '全部' }).click() });
  assert.equal(await select(), 1);
  assert.equal(await isAllChecked(), true);
  assert.equal(await select(), 0);
  await boxes.last().evaluate(el => el.innerHTML = '<svg><path d="M4 8L12 8"></path></svg>');
  assert.equal(await isAllChecked(), false, 'a path shaped as a dash is not a checkmark');
  await select();
  await boxes.first().evaluate(el => el.innerHTML = '<svg></svg>');
  assert.equal(await isAllChecked(), false, 'toolbar check alone does not prove complete selection');
});
