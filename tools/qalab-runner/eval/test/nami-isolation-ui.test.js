const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DialogRunner = require('../src/dialog-runner');
const DesktopRunner = require('../src/desktop-runner');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
const platform = { inputSelector: '#input', sendBtnSelector: '#send', newTaskSelector: '.new-task',
  answerGroupSelector: '.chat-group.assistant', userGroupSelector: '.chat-group.user', beanCostSelector: '.chat-token-cost' };

async function pageFixture(t) {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.route('https://fixture.work.n.cn/**', route => route.fulfill({ contentType: 'text/html', body: '<main></main>' }));
  await page.goto('https://fixture.work.n.cn/chat?sid=old');
  return page;
}

for (const embedded of [false, true]) test(`visible beans inside shadow DOM are read in ${embedded ? 'iframe' : 'main document'}`, async t => {
  const page = await pageFixture(t);
  let frame = page.mainFrame();
  if (embedded) {
    await page.route('https://embedded.work.n.cn/**', route => route.fulfill({ contentType: 'text/html', body: '<main></main>' }));
    await page.setContent('<iframe src="https://embedded.work.n.cn/chat?sid=embedded"></iframe>');
    await page.frameLocator('iframe').locator('main').waitFor({ state: 'attached' });
    frame = page.frames().find(f => f.url().includes('embedded.work.n.cn'));
  }
  await frame.evaluate(() => {
    const root = document.querySelector('main').attachShadow({ mode: 'open' });
    root.innerHTML = '<div class="chat-group user">当前问题</div><div class="chat-group assistant"><cost-widget></cost-widget></div>';
    const cost = root.querySelector('cost-widget').attachShadow({ mode: 'open' });
    cost.innerHTML = '<div class="chat-token-cost">本次回答消耗：279.9K tokens（<span>23</span>\u200b 算力豆）</div><div class="chat-token-cost" hidden>-- 算力豆</div>';
  });
  const dr = new DialogRunner(null, platform, { beanCostTimeoutMs: 0, beanCostReloadOnce: false }).attachToPage(page);
  dr.frame = frame; dr._liveFrame = () => frame;
  assert.equal(await frame.evaluate(() => document.querySelectorAll('.chat-group.assistant').length), 0, 'old native DOM lookup misses these visible groups');
  assert.equal((await dr.extractBeanCost()).value, '23');
  assert.ok(await dr._beanCostTurnKey());
});

test('turn identity survives tool group consolidation but rejects another session with identical question', async t => {
  const page = await pageFixture(t);
  await page.setContent('<div class="chat-group user">同一个问题</div><div class="chat-group assistant"></div><div class="chat-group assistant"><span class="chat-token-cost">23 算力豆</span></div>');
  const dr = new DialogRunner(null, platform, { beanCostTimeoutMs: 0 }).attachToPage(page);
  const key = await dr._beanCostTurnKey();
  await page.locator('.chat-group.assistant').first().evaluate(el => el.remove());
  assert.equal(await dr._beanCostTurnKey(), key);
  assert.equal((await dr._pollBeanCost(key)).value, '23');
  await page.evaluate(() => history.replaceState({}, '', '?sid=another'));
  assert.equal((await dr._pollBeanCost(key)).value, '');
});

async function desktopFixture(t) {
  const page = await pageFixture(t);
  await page.setContent('<openclaw-app></openclaw-app><button class="new-task" hidden>旧隐藏入口</button><button class="new-task" id="new">新建任务</button><main><div class="chat-group user">旧问题</div><div class="chat-group assistant">旧答案</div></main><div id="input" contenteditable="true"></div><button id="send">发送</button>');
  await page.evaluate(() => {
    window.sent = []; window.nextId = 0;
    document.querySelector('#new').onclick = () => {
      document.querySelector('main').innerHTML = ''; document.querySelector('#input').textContent = '';
      history.replaceState({}, '', '/chat');
    };
    document.querySelector('#send').onclick = () => {
      if (!new URL(location.href).searchParams.get('sid')) history.replaceState({}, '', '?sid=new-' + (++window.nextId));
      const text = document.querySelector('#input').textContent;
      window.sent.push({ text, sid: new URL(location.href).searchParams.get('sid') });
      const user = document.createElement('div'); user.className = 'chat-group user'; user.textContent = text;
      const assistant = document.createElement('div'); assistant.className = 'chat-group assistant'; assistant.textContent = '答案';
      document.querySelector('main').append(user, assistant); document.querySelector('#input').textContent = '';
    };
  });
  const d = new DesktopRunner(page.context(), page, platform, { copyAnswer: false, answerOnly: true });
  d._focus = async () => {}; d._sleep = () => page.waitForTimeout(5);
  d.dr._applyDialogOptions = async () => {};
  d.dr._typeQuestion = (input, text) => input.fill(text);
  d.dr._dismissConfirmDialogs = async () => false;
  d.dr.waitForGenerationStart = async () => true;
  return { d, page };
}

test('two independent cases each open a different task using the visible entry', async t => {
  const { d, page } = await desktopFixture(t);
  for (let i = 1; i <= 2; i++) {
    assert.equal(await d._openCleanConversation(), true);
    await d._sendOne({ caseId: 'RUN-' + i, question: '问题' + i });
    assert.equal(await page.locator('.chat-group.user').count(), 1);
  }
  assert.deepEqual(await page.evaluate(() => window.sent), [{ text: '问题1', sid: 'new-1' }, { text: '问题2', sid: 'new-2' }]);
});

test('explicit follow-up stays in its own conversation and a changed session blocks sending', async t => {
  const { d, page } = await desktopFixture(t);
  assert.equal(await d._openCleanConversation(), true);
  await d._sendOne({ question: '首轮' }, { multiTurn: true });
  const expectedSession = await d._conversationKey();
  await d._sendOne({ question: '追问' }, { followUp: true, multiTurn: true, expectedSession });
  assert.equal(await page.locator('.chat-group.user').count(), 2);
  assert.deepEqual(await page.evaluate(() => window.sent.map(s => s.sid)), ['new-1', 'new-1']);
  await page.evaluate(() => history.replaceState({}, '', '?sid=foreign'));
  await assert.rejects(d._sendOne({ question: '不该发出' }, { followUp: true, expectedSession }), /NAMI_CONVERSATION_CHANGED/);
  assert.equal(await page.evaluate(() => window.sent.length), 2);
});

test('history loading, unchanged old session and stale messages all fail the empty-conversation check', async t => {
  const { d, page } = await desktopFixture(t);
  const previous = await d._conversationKey();
  await page.locator('main').evaluate(el => el.innerHTML = '');
  assert.equal(await d._isCleanConversation(previous), false, 'empty DOM under old sid is not a new task');
  await page.evaluate(() => { history.replaceState({}, '', '/chat'); document.querySelector('openclaw-app').chatLoading = true; });
  assert.equal(await d._isCleanConversation(previous), false);
  await page.locator('openclaw-app').evaluate(el => el.chatLoading = false);
  assert.equal(await d._isCleanConversation(previous), true);
  await page.locator('main').evaluate(el => el.innerHTML = '<div class="chat-group user">历史又恢复了</div>');
  assert.equal(await d._isCleanConversation(previous), false);
});

test('failed new-task click and launcher fallback never declare a historical conversation clean', async t => {
  const { d, page } = await desktopFixture(t);
  await page.locator('#new').evaluate(el => el.onclick = () => {});
  await page.route('https://fixture.work.n.cn/launcher', route => route.fulfill({ contentType: 'text/html', body: '<button class="new-task">新建任务</button><div id="input" contenteditable></div><div class="chat-group user">历史</div>' }));
  d.platform.chatUrl = 'https://fixture.work.n.cn/launcher';
  assert.equal(await d._openCleanConversation(), false);
  await assert.rejects(d._sendOne({ question: '不能追加' }), /NAMI_NEW_CONVERSATION/);
});

for (const stage of ['options', 'typing']) test(`restored history during ${stage} is caught before send`, async t => {
  const { d, page } = await desktopFixture(t);
  assert.equal(await d._openCleanConversation(), true);
  const restore = () => page.locator('main').evaluate(el => el.innerHTML = '<div class="chat-group user">迟到的历史消息</div>');
  if (stage === 'options') d.dr._applyDialogOptions = restore;
  else d.dr._typeQuestion = async (input, text) => { await input.fill(text); await restore(); };
  await assert.rejects(d._sendOne({ question: '不能追加' }), /NAMI_NEW_CONVERSATION/);
  assert.equal(await page.evaluate(() => window.sent.length), 0);
});
