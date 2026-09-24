const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
const DesktopRunner = require('../src/desktop-runner');
const platform = require('../config/default.config').platform;
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function fixture(t, { embedded = false, field = 'input', pages = 1, stay = false, extraOption = false } = {}) {
  const page = await browser.newPage(); t.after(() => page.close());
  let frame = page.mainFrame();
  if (embedded) {
    await page.setContent('<iframe name="work" style="width:100%;height:900px" srcdoc="<body></body>"></iframe>');
    frame = page.frame('work');
  }
  await frame.evaluate(({ field, pages, stay, extraOption }) => {
    const host = document.createElement('openclaw-app'); document.body.append(host);
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = '<button id="new-task">新建任务</button><textarea id="composer">原始聊天输入</textarea><div class="chat-ask-form-floating"><chat-question-form-card></chat-question-form-card></div>';
    window.events = []; window.answers = []; window.card = root.querySelector('chat-question-form-card');
    root.querySelector('#new-task').onclick = () => window.events.push('new-task');
    window.render = n => {
      const card = window.card; card.questionKey = `question-${n}`;
      card.innerHTML = '<h3 class="ask-form__title">核心营销问题是什么？</h3><div class="ask-form__options" aria-multiselectable="false">' +
        '<div class="ask-form__option"><span class="ask-form__option-label">其它</span></div>' +
        (extraOption ? '<div class="ask-form__option"><span class="ask-form__option-label">稍后</span></div>' : '') +
        '</div><button class="ask-form__btn--ok" disabled>提交</button>';
      const option = card.querySelector('.ask-form__option');
      option.onclick = () => {
        option.classList.add('is-selected'); option.setAttribute('aria-selected', 'true');
        if (!option.querySelector('.answer')) {
          const input = document.createElement(field === 'editable' ? 'div' : field);
          if (field === 'editable') input.contentEditable = 'true';
          input.className = 'answer'; input.style.cssText = 'display:block;min-height:30px;border:1px solid';
          input.setAttribute('placeholder', '请填写'); option.append(input);
          input.oninput = () => card.querySelector('button').disabled = !(input.value || input.textContent).trim();
        }
      };
      option.click();
      card.querySelector('button').onclick = () => {
        const input = card.querySelector('.answer'); window.answers.push(input.value || input.textContent);
        window.events.push('submit');
        if (stay) return;
        if (n < pages) window.render(n + 1); else card.remove();
      };
    };
    window.render(1);
    // 隐藏的历史卡片不能抢走当前卡片的提交。
    const old = document.createElement('chat-question-form-card'); old.style.display = 'none'; root.append(old);
  }, { field, pages, stay, extraOption });
  const r = new DialogRunner(null, platform, {}).attachToPage(page); r.frame = frame; r._liveFrame = () => frame;
  r._beginTurn({ groupCount: 0, footerCount: 0 }, { question: '为轻食品牌编写 Campaign Brief，强调健康便捷。' });
  return { page, frame, r };
}

for (const embedded of [false, true]) for (const field of ['input', 'textarea', 'editable']) {
  test(`${embedded ? 'iframe' : 'main'} shadow DOM fills ${field} then submits without touching composer`, async t => {
    const { r, frame } = await fixture(t, { embedded, field });
    assert.equal(await r._dismissConfirmDialogs(), true);
    assert.equal(await r._dismissConfirmDialogs(), false);
    const answers = await frame.evaluate(() => window.answers);
    assert.equal(answers.length, 1); assert.match(answers[0], /轻食品牌/); assert.match(answers[0], /假设/);
    assert.equal(await frame.locator('#composer').inputValue(), '原始聊天输入');
    assert.equal(r._askForm.history[0].status, 'card_closed_or_advanced');
    assert.equal(r._askForm.history[0].answers[0].source, 'runner_default_assumption');
  });
}

test('six consecutive pages with identical titles submit once each before accepting completion', async t => {
  const { r, frame } = await fixture(t, { pages: 6 });
  r.execution = { responseTimeout: 20000, completionSettleMs: 1 };
  r._probeGenerating = async () => false;
  r._hasCurrentCompletionFooter = async () => true;
  assert.deepEqual(await r.waitForResponseComplete(), { completed: true, reason: 'footer' });
  assert.equal(await frame.evaluate(() => window.answers.length), 6);
  assert.equal(r._askForm.history.length, 6);
  assert.ok(r._askForm.history.every(x => x.status === 'card_closed_or_advanced'));
});

test('text question with two options is not mistaken for a protected operation', async t => {
  const { r, frame } = await fixture(t, { extraOption: true });
  assert.equal(await r._handleProtectedOperation(), false);
  await r._dismissConfirmDialogs();
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
});

test('prefilled input is preserved and exact test reply overrides default without using expected answer', async t => {
  const { r, frame } = await fixture(t, { pages: 2 });
  r._askForm.answers = { 'question-2': '提升新品认知度，目标人群是城市白领。' };
  await frame.locator('.answer').fill('当前目标是提升门店客流。');
  await r._dismissConfirmDialogs(); await r._dismissConfirmDialogs(); await r._dismissConfirmDialogs();
  assert.deepEqual(await frame.evaluate(() => window.answers), ['当前目标是提升门店客流。', '提升新品认知度，目标人群是城市白领。']);
  assert.deepEqual(r._askForm.history.map(x => x.answers[0].source), ['existing_input', 'test_case']);
});

test('slow submission does not double click, release generation gate, or open a new conversation', async t => {
  const { r, frame, page } = await fixture(t, { stay: true });
  await r._dismissConfirmDialogs(); await r._dismissConfirmDialogs();
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
  r._askForm.pending.submittedAt -= 16000;
  const d = new DesktopRunner(page.context(), page, platform, {}); d.dr = r;
  await assert.rejects(d._clickNewTask(frame, '#new-task'), /NAMI_ASK_FORM.*未关闭/);
  r._probeGenerating = async () => true;
  await assert.rejects(r.waitForGenerationStart(), /NAMI_ASK_FORM/);
  assert.deepEqual(await frame.evaluate(() => window.events), ['submit']);
});

test('disabled submit after filling fails explicitly, without falling through to generic confirmation', async t => {
  const { r, frame } = await fixture(t);
  r.execution.askFormReadyTimeoutMs = 10;
  await frame.evaluate(() => window.card.querySelector('.answer').oninput = () => {});
  r._handleToolConfirm = () => assert.fail('must not fall through');
  await assert.rejects(r._dismissConfirmDialogs(), /NAMI_ASK_FORM.*仍不可用/);
  assert.equal(await frame.evaluate(() => window.answers.length), 0);
  assert.equal(r._askForm.history[0].status, 'failed');
});

test('clarification history, context and pending submit remain isolated when switching desktop tasks', async t => {
  const { r, frame } = await fixture(t, { stay: true });
  await r._dismissConfirmDialogs(); const a = r._captureTurnState();
  r._beginTurn({ groupCount: 0, footerCount: 0 }, { question: '另一个任务' });
  assert.equal(r._askForm.history.length, 0);
  r._restoreTurnState(a); await r._dismissConfirmDialogs();
  assert.match(r._askForm.question, /轻食品牌/);
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
  const d = new DesktopRunner(null, null, platform, {});
  const result = d._buildResult({ clarificationInteractions: a.askForm.history }, {}, {});
  assert.equal(result.clarificationInteractions.length, 1);
  result.clarificationInteractions[0].status = 'changed';
  assert.equal(a.askForm.history[0].status, 'submitted');
});

test('new conversation waits until filled popup actually closes', async t => {
  const { r, frame, page } = await fixture(t, { stay: true });
  await frame.evaluate(() => {
    const btn = window.card.querySelector('button'), original = btn.onclick;
    btn.onclick = () => { original(); setTimeout(() => { window.events.push('closed'); window.card.remove(); }, 350); };
  });
  const d = new DesktopRunner(page.context(), page, platform, {}); d.dr = r;
  await d._clickNewTask(frame, '#new-task');
  assert.deepEqual(await frame.evaluate(() => window.events), ['submit', 'closed', 'new-task']);
});

test('mixed choice and multiple text fields are scoped to the active card', async t => {
  const { r, frame } = await fixture(t);
  await frame.evaluate(() => {
    const card = window.card;
    const group = document.createElement('div'); group.className = 'ask-form__options';
    group.innerHTML = '<div class="ask-form__option"><span class="ask-form__option-label">A</span></div><div class="ask-form__option"><span class="ask-form__option-label">B</span></div>';
    group.firstElementChild.onclick = () => group.firstElementChild.classList.add('is-selected'); card.prepend(group);
    const second = document.createElement('textarea'); second.setAttribute('aria-label', '补充信息'); card.append(second);
    window.moreAnswers = [];
    card.querySelector('button').onclick = () => {
      window.moreAnswers.push([...card.querySelectorAll('input, textarea')].map(e => e.value));
      card.remove();
    };
  });
  await r._dismissConfirmDialogs();
  const answers = await frame.evaluate(() => window.moreAnswers[0]);
  assert.equal(answers.length, 2); assert.ok(answers.every(x => x.includes('轻食品牌')));
  assert.deepEqual(r._askForm.history[0].selections, ['A', '其它']);
});

test('unselected Other reveals its input asynchronously before submitting', async t => {
  const { r, frame } = await fixture(t);
  await frame.evaluate(() => {
    const card = window.card, option = card.querySelector('.ask-form__option');
    const input = option.querySelector('.answer'); input.remove();
    option.classList.remove('is-selected'); option.setAttribute('aria-selected', 'false');
    option.onclick = () => {
      option.classList.add('is-selected'); option.setAttribute('aria-selected', 'true');
      setTimeout(() => option.append(input), 350);
    };
  });
  await r._dismissConfirmDialogs();
  assert.equal(await frame.evaluate(() => window.answers.length), 1);
  assert.equal(r._askForm.history[0].answers.length, 1);
});

test('ordinary choice-only form retains the selected option and submits without text', async t => {
  const { r, frame } = await fixture(t);
  await frame.evaluate(() => {
    window.card.querySelector('.answer').remove();
    const submit = window.card.querySelector('button'); submit.disabled = false;
    submit.onclick = () => { window.events.push('choice'); window.card.remove(); };
  });
  await r._dismissConfirmDialogs();
  assert.deepEqual(await frame.evaluate(() => window.events), ['choice']);
  assert.deepEqual(r._askForm.history[0].answers, []);
});

test('web retry cannot reload or start another conversation after clarification failure', async () => {
  const r = new DialogRunner(null, platform, { retryTimes: 2, retryDelay: 1 });
  let sends = 0;
  r.sendMessage = async () => { sends++; return { success: false, errorMessage: '[NAMI_ASK_FORM] 提交不可用' }; };
  r.page = { reload: () => assert.fail('must preserve pending conversation') };
  const result = await r.runWithRetry({ question: '任务', caseId: 'case-1' });
  assert.equal(sends, 1); assert.equal(result.attempt, 1);
});
