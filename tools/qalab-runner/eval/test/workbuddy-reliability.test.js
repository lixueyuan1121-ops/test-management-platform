const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { workbuddy } = require('../config/default.config');

let browser, page, runner;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage();
  runner = new WorkbuddyRunner(page, { ...workbuddy, newTaskTimeout: 200, messageClickTimeout: 200, messageCopyTimeout: 200 });
});
afterEach(async () => { await page.close(); });

async function mountNavigation({ windows = false, stuck = false, delay = 0, recover = false } = {}) {
  await page.setContent(`
    <div style="display:none"><button data-track-id="agent_new_task_button_clicked">新建任务</button></div>
    <button aria-label="新建任务" onclick="window.wrong=true">项目新建任务</button>
    <div ${windows ? 'id="workbuddy-titlebar-left-slot"' : 'class="workbuddy-topbar"'}>
      <button id="new" data-track-id="agent_new_task_button_clicked" onclick="openTask()">+</button>
    </div>
    <div id="old" data-message-request-id="previous"><div class="conversation-finished-footer">旧回答</div></div>
    <div class="wb-home-page" style="display:none">首页</div>
    <div contenteditable="true" role="textbox" onkeydown="if(event.key==='Enter')window.sent++"></div>
    <script>
      window.sent=0;window.opens=0;window.wrong=false;
      function openTask(){
        window.opens++;
        if(${stuck} || (${recover} && window.opens===1))return;
        setTimeout(()=>{document.querySelector('#old')?.remove();document.querySelector('.wb-home-page').style.display='';},${delay});
      }
    </script>`);
}

for (const windows of [false, true]) {
  test(`${windows ? 'Windows 标题栏' : 'Mac 收起侧栏'}纯图标入口，无文字也能新建，不误点隐藏/项目入口`, async () => {
    await mountNavigation({ windows });
    await assert.rejects(page.locator('text=新建任务').first().click({ timeout: 80 }), /Timeout/);
    await runner._openCleanConversation();
    assert.equal(await page.locator('#old').count(), 0);
    assert.equal(await page.evaluate(() => wrong), false);
    assert.equal(await page.evaluate(() => opens), 1);
  });
}

test('24 条任务交替展开文字入口与收起图标入口，每次均进入空白页', async () => {
  for (let i = 0; i < 24; i++) {
    await mountNavigation({ windows: i % 3 === 0 });
    if (i % 2 === 0) await page.locator('#new').evaluate(el => {
      el.innerHTML = '<span class="conversation-list-tab-button-label">新建任务</span>';
      el.setAttribute('role', 'tab');
      el.className = 'conversation-list-tab-button';
    });
    await runner._openCleanConversation();
    assert.equal(await page.locator('#old').count(), 0);
    assert.equal(await page.evaluate(() => opens), 1);
    assert.equal(await page.evaluate(() => wrong), false);
  }
});

test('点击后等空白页异步就绪，不因旧输入框可见提前发送', async () => {
  await mountNavigation({ delay: 80 });
  await runner._openCleanConversation();
  assert.equal(await page.locator('#old').count(), 0);
});

test('第一次未切换时仅重试导航，成功后才允许发送', async () => {
  await mountNavigation({ recover: true });
  await runner._openCleanConversation();
  assert.equal(await page.evaluate(() => opens), 2);
  assert.equal(await page.evaluate(() => sent), 0);
});

test('导航永久失效时保留阶段和细节，不把题目写进旧会话', async () => {
  await mountNavigation({ stuck: true });
  const r = await runner.runOne({ question: '绝不能进入旧会话' });
  assert.equal(r.success, false);
  assert.match(r.errorMessage, /WORKBUDDY_NEW_TASK.*新建任务失败/s);
  assert.equal(r.errorCode, 'WORKBUDDY_NEW_TASK');
  assert.equal(await page.locator('[contenteditable]').innerText(), '');
  assert.equal(await page.evaluate(() => opens), 2);
  assert.equal(runner.trace.buildTrace().execution_error.stage, '新建任务');
});

test('新任务页面残留草稿或附件时不发送，避免混题', async () => {
  await mountNavigation();
  await page.locator('[contenteditable]').fill('未清空草稿');
  await assert.rejects(runner._openCleanConversation(), /无法确认空白会话/);
  assert.equal(await page.evaluate(() => sent), 0);
});

test('发送已发生后抛错，不重发；多轮后续题跳过', async () => {
  let opens = 0, sends = 0;
  runner._openCleanConversation = async () => { opens++; };
  runner._footerCount = async () => 0;
  runner._sendOne = async () => { sends++; if (sends === 2) throw Error('发送后连接断开'); };
  runner._waitComplete = async () => ({ completed: true, reason: 'footer' });
  runner.trace.captureTurn = async () => { runner.trace._data.answer = '第一轮答案'; };
  runner._captureRawMessage = runner._captureShareLink = async () => '';
  const results = await runner.runConversationTurns([0, 1, 2].map(turnIndex => ({ turnIndex, question: '自检' })));
  assert.equal(opens, 1);
  assert.equal(sends, 2);
  assert.equal(results[0].success, true);
  assert.equal(results[1].success, false);
  assert.equal(results[2].completeReason, 'skipped');
  assert.equal(runner.trace.buildTrace().answer, '');
  assert.equal(runner.trace.buildTrace().execution_error, null);
});

const payload = JSON.stringify({ requestId: 'current', messages: [{ messageType: 'assistant', requestId: 'current', content: [{ type: 'text', text: '回答' }] }] });
async function mountCopy({ denied = false, invalid = false, noItem = false } = {}) {
  await page.setContent(`<div class="assistantFeedback"><button aria-label="更多操作" onclick="openMenu()">更多</button></div>
    <div class="assistantFeedback" style="display:none"><button aria-label="更多操作" onclick="wrong=true">旧消息</button></div>
    <div class="_item_fixture" style="display:none" onclick="wrong=true">复制 message</div>
    <script>
      window.wrong=false;window.opens=0;window.copies=0;window.clip=${JSON.stringify(payload)};window.markers=[];
      Object.defineProperty(navigator,'clipboard',{value:{
        writeText:async value=>{if(${denied})throw Error('denied');window.clip=value;markers.push(value);},
        readText:async()=>window.clip
      }});
      function openMenu(){opens++;document.body.insertAdjacentHTML('beforeend','<div id="menu">'+
        (${noItem} ? '没有复制项' : '<button class="_item_fixture" onclick="copyMessage()">复制 message</button>')+'</div>');}
      function copyMessage(){copies++;window.clip=${JSON.stringify(invalid ? '不是 JSON 的旧剪贴板' : payload)};}
      document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelector('#menu')?.remove();});
    </script>`);
}

test('复制本轮结构化消息，忽略隐藏末条和菜单项，成功后退出菜单', async () => {
  await mountCopy();
  assert.equal(await runner._captureRawMessage(), payload);
  assert.equal(await page.locator('#menu').count(), 0);
  assert.equal(await page.evaluate(() => wrong), false);
  await runner._captureRawMessage();
  const markers = await page.evaluate(() => markers);
  assert.equal(new Set(markers).size, 2);
});

for (const opts of [{ denied: true }, { invalid: true }, { noItem: true }]) {
  test(`复制失败不能接收旧内容且必须退出菜单：${JSON.stringify(opts)}`, async () => {
    await mountCopy(opts);
    assert.equal(await runner._captureRawMessage(), '');
    assert.equal(await page.locator('#menu').count(), 0);
    if (opts.denied) assert.equal(await page.evaluate(() => opens), 0);
  });
}
