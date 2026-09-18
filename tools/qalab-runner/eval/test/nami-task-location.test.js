const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const DesktopRunner = require('../src/desktop-runner');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function fixture(t, { embedded = false } = {}) {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.route('https://**.work.n.cn/**', route => route.fulfill({ contentType: 'text/html', body: '<body></body>' }));
  await page.goto('https://fixture.work.n.cn/chat?sid=A');
  let frame = page.mainFrame();
  if (embedded) {
    await page.goto('about:blank');
    await page.setContent('<iframe src="https://embedded.work.n.cn/chat?sid=A" style="width:100%;height:800px"></iframe>');
    await page.frameLocator('iframe').locator('body').waitFor();
    frame = page.frames().find(f => f.url().includes('embedded.work.n.cn'));
  }
  await frame.evaluate(() => {
    const app = document.createElement('openclaw-app'); document.body.append(app); app.chatLoading = false;
    window.root = app.attachShadow({ mode: 'open' });
    window.root.innerHTML = '<nav></nav><main></main>';
    window.clicks = []; window.tasks = { A: '使用微信公众号技能制作计划', B: '使用微信公众号技能制作计划' };
    window.render = (sid, delay = 0) => {
      history.replaceState({}, '', '?sid=' + sid); app.chatLoading = true;
      window.root.querySelectorAll('.task').forEach(el => el.classList.toggle('is-selected', el.dataset.sessionId === sid));
      const finish = () => {
        window.root.querySelector('main').innerHTML = '<div class="chat-thread" data-session-id="' + sid + '"><div class="chat-group user"></div><div class="chat-group assistant"><span class="answer">答案 ' + sid + '</span><span class="cost">1 算力豆</span></div></div>';
        window.root.querySelector('.user').textContent = window.tasks[sid]; app.chatLoading = false;
      };
      if (delay) setTimeout(finish, delay); else finish();
    };
    window.addItem = (sid, delay = 0) => {
      const item = document.createElement('button'); item.className = 'task'; item.dataset.sessionId = sid;
      item.textContent = '已自动改写的标题 ' + sid;
      item.onclick = () => { window.clicks.push(sid); window.render(sid, delay); };
      window.root.querySelector('nav').append(item);
    };
    window.render('A');
  });
  const platform = { taskListItemSelector: '.task', taskListRunningSelector: '.running',
    answerGroupSelector: '.chat-group.assistant', answerSelector: '.answer', userGroupSelector: '.chat-group.user', costSelector: '.cost' };
  const d = new DesktopRunner(page.context(), page, platform, { answerOnly: true, copyAnswer: false, desktopTaskSwitchTimeoutMs: 1200 });
  await d._ensureCtx(); d.dr.frame = d._fl();
  d._focus = async () => {};
  d.dr._beginTurn({ groupCount: 0, footerCount: 0 });
  const task = { case: { caseId: 'RUN-1636', question: '/plan 使用技能[微信公众号 & 视频号 内容]' },
    question: '/plan 使用技能[微信公众号 & 视频号 内容]', turnState: d.dr._captureTurnState(), startTime: Date.now() };
  await d._bindTaskConversation(task);
  return { page, frame, d, task };
}

for (const embedded of [false, true]) test(`bound task with transformed /plan query needs no sidebar in ${embedded ? 'iframe' : 'main'}`, async t => {
  const { frame, d, task } = await fixture(t, { embedded });
  assert.equal(task.sessionId, 'A');
  assert.equal(await d._switchToTask(task), true);
  assert.deepEqual(await frame.evaluate(() => window.clicks), []);
});

test('session ID finds renamed task beyond first 12 rows despite stale cached index', async t => {
  const { frame, d, task } = await fixture(t);
  await frame.evaluate(() => {
    for (let i = 0; i < 15; i++) window.addItem('old-' + i);
    window.addItem('A', 150); window.addItem('B'); window.render('B');
  });
  task.listIndex = 0;
  assert.equal(await d._readItemRunning(task.listIndex, task.sessionId), null);
  assert.equal(await d._switchToTask(task), true);
  assert.deepEqual(await frame.evaluate(() => window.clicks), ['A']);
  assert.equal(task.listIndex, 15);
  assert.equal(await frame.locator('.answer').innerText(), '答案 A');
});

test('same question in another session is never accepted as the target', async t => {
  const { frame, d, task } = await fixture(t);
  await frame.evaluate(() => { window.addItem('B'); window.render('B'); });
  assert.equal(await d._switchToTask(task), false);
  assert.deepEqual(await frame.evaluate(() => window.clicks), []);
  let extracted = false; d._extractCurrent = async () => { extracted = true; return { answer: 'wrong' }; };
  task.completeReason = 'timeout'; task.completed = false;
  await d._finishTask(task);
  assert.equal(extracted, false);
  assert.equal(task.result.success, false);
  assert.match(task.result.answer, /NAMI_TASK_NOT_FOUND/);
  assert.equal(task.result.shareLink, '');
});

test('URL change alone cannot accept stale rendered messages or loading history', async t => {
  const { frame, d, task } = await fixture(t);
  await frame.evaluate(() => { window.render('B'); history.replaceState({}, '', '?sid=A'); });
  assert.equal(await d._isTaskCurrent(task), false, 'thread sid still B');
  await frame.evaluate(() => { window.render('A'); document.querySelector('openclaw-app').chatLoading = true; });
  assert.equal(await d._isTaskCurrent(task), false);
  d.execution.desktopTaskSwitchTimeoutMs = 250;
  assert.equal(await d._switchToTask(task), false);
});

test('no-op sidebar click does not accept an identically worded foreign task', async t => {
  const { frame, d, task } = await fixture(t);
  await frame.evaluate(() => {
    window.addItem('A'); window.render('B'); window.root.querySelector('.task').onclick = () => {};
  });
  d.execution.desktopTaskSwitchTimeoutMs = 250;
  assert.equal(await d._switchToTask(task), false);
});

test('missing session ID fails binding explicitly', async t => {
  const { frame, d, task } = await fixture(t);
  await frame.evaluate(() => history.replaceState({}, '', '/chat'));
  d.execution.desktopSessionBindTimeoutMs = 0;
  await assert.rejects(d._bindTaskConversation({ ...task, sessionKey: '', sessionId: '' }), /NAMI_TASK_BIND_FAILED/);
});

test('switching conversations during extraction discards all foreign fields', async t => {
  const { frame, d, task } = await fixture(t);
  d._extractCurrent = async () => {
    await frame.evaluate(() => window.render('B'));
    return { answer: '其他任务答案', beanCost: '99', shareLink: 'https://fixture.work.n.cn/share/foreign' };
  };
  task.completed = true; task.completeReason = 'footer';
  await d._finishTask(task);
  assert.equal(task.result.success, false);
  assert.match(task.result.answer, /NAMI_CONVERSATION_CHANGED/);
  assert.equal(task.result.beanCost, '');
  assert.equal(task.result.shareLink, '');
});

for (const pendingApproval of [false, true]) test(`desktop lifecycle binds each case and extracts its own transformed query (pending approval: ${pendingApproval})`, async t => {
  const { frame, d } = await fixture(t);
  Object.assign(d.execution, { sendIntervalMs: 0, desktopPatrolMs: 1, responseTimeout: 5000 });
  d._openCleanConversation = async () => {
    await frame.evaluate(() => { window.root.querySelector('main').innerHTML = ''; history.replaceState({}, '', '/chat'); }); return true;
  };
  d._sendOne = async tc => {
    d.dr._beginTurn({ groupCount: 0, footerCount: 0 });
    if (pendingApproval && tc.caseId === 'A') d.dr._protectedOperation.pending = { key: 'submitted', submittedAt: Date.now() };
    await frame.evaluate(id => { window.addItem(id); window.render(id); }, tc.caseId);
  };
  if (pendingApproval) d._readItemRunning = async (_index, sid) => sid === 'A';
  d.dr._settleCompletion = async () => true;
  d._extractCurrent = async () => ({ answer: await frame.locator('.answer').innerText() });
  const results = await d.runConcurrent(['A', 'B'].map(caseId => ({ caseId, question: '/plan 原始命令完全不同' })));
  assert.deepEqual(results.map(r => [r.success, r.answer]), [[true, '答案 A'], [true, '答案 B']]);
  assert.deepEqual(results.map(r => r.completeReason), ['footer', 'footer']);
  assert.deepEqual(await frame.evaluate(() => window.clicks), ['A', 'B']);
});

test('currently visible running task checks interaction without waiting three-minute fallback', async t => {
  const { frame, d } = await fixture(t);
  Object.assign(d.execution, { sendIntervalMs: 0, desktopPatrolMs: 1, responseTimeout: 3000 });
  d._openCleanConversation = async () => true;
  d._sendOne = async () => { await frame.evaluate(() => { window.addItem('A'); window.render('A'); }); };
  d._readItemRunning = async () => true;
  let handled = 0;
  d.dr._dismissConfirmDialogs = async () => ++handled === 1;
  d.dr._settleCompletion = async () => true;
  d._extractCurrent = async () => ({ answer: '处理确认后完成' });
  const [result] = await d.runConcurrent([{ caseId: 'A', question: '/plan 测试' }]);
  assert.equal(result.success, true);
  assert.equal(result.completeReason, 'footer');
  assert.ok(handled >= 2);
});
