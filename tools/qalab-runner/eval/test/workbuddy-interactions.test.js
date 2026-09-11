// 真实 Chromium DOM 回归：结构来自 WorkBuddy 5.5.6 的反问卡和分享底栏。
// node --test tools/qalab-runner/eval/test/workbuddy-interactions.test.js
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
  runner = new WorkbuddyRunner(page, { ...workbuddy, shareClickTimeout: 300, shareCopyTimeout: 300 }, { responseTimeout: 4000 });
});
afterEach(async () => { await page.close(); });

function question({ selected = 1, multi = false, advance = true, disabled = false } = {}) {
  return `<div class="_questionFloating_fixture_1">
    <div class="_title_fixture_1">请选择</div>
    <div class="_options_fixture_1">${['默认一', '默认二'].map((label, i) =>
      `<div role="button" tabindex="0" class="_optionItem_fixture_1 ${selected === i ? '_selected_fixture_1' : ''}"
        onclick="pick(this,${i})">${multi ? `<span role="checkbox" aria-checked="${selected === i}"></span>` : ''}${label}</div>`).join('')}</div>
    ${advance ? `<button ${disabled ? 'disabled' : ''} onclick="advance()">继续</button>` : ''}
  </div>`;
}

async function mountQuestion(opts = {}) {
  await page.setContent(`<button id="unrelated" onclick="window.wrong=true">继续</button>${question(opts)}
    <script>
    window.picks=[]; window.submitted=[];
    function pick(el, i) {
      picks.push(i);
      if (${!!opts.multi}) {
        el.classList.toggle('_selected_fixture_1');
        el.querySelector('[role=checkbox]').setAttribute('aria-checked',el.classList.contains('_selected_fixture_1'));
      } else {
        document.querySelectorAll('._optionItem_fixture_1').forEach(o=>o.classList.remove('_selected_fixture_1'));
        el.classList.add('_selected_fixture_1');
      }
    }
    function advance() {
      submitted=[...document.querySelectorAll('._optionItem_fixture_1._selected_fixture_1')].map(e=>e.textContent);
      document.querySelector('._questionFloating_fixture_1').remove();
      document.body.insertAdjacentHTML('beforeend','<div class="conversation-finished-footer">完成</div>');
    }
    </script>`);
}

test('保留已有默认项，点击卡内继续，不误点整页其他继续按钮', async () => {
  await mountQuestion({ selected: 1 });
  assert.equal(await runner._handleQuestion(), true);
  assert.deepEqual(await page.evaluate(() => submitted), ['默认二']);
  assert.deepEqual(await page.evaluate(() => picks), []);
  assert.equal(await page.evaluate(() => !!window.wrong), false);
  assert.equal(await runner._handleQuestion(), false);
});

test('无默认的多选题先选第一项，下一轮继续，不把已选项反选', async () => {
  await mountQuestion({ selected: -1, multi: true });
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => picks), [0]);
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => submitted), ['默认一']);
});

test('继续暂不可点时保留多选默认，不重复切换选项', async () => {
  await mountQuestion({ selected: 1, multi: true, disabled: true });
  await runner._handleQuestion();
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => picks), []);
  await page.locator('._questionFloating_fixture_1 button').evaluate(e => { e.disabled = false; });
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => submitted), ['默认二']);
});

test('新版中间题无继续按钮，点默认项自动翻页，末题通过发送提交', async () => {
  await mountQuestion({ selected: 1, advance: false });
  await page.evaluate(() => {
    window.pick = (el, i) => {
      picks.push(i);
      document.querySelector('._questionFloating_fixture_1').innerHTML =
        '<div role="button" class="_optionItem_fixture_1 _selected_fixture_1">中文</div><button aria-label="发送" onclick="advance()"></button>';
    };
  });
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => picks), [1]);
  await runner._handleQuestion();
  assert.deepEqual(await page.evaluate(() => submitted), ['中文']);
});

test('等待完成期间自动回答反问，最终 footer 稳定后才成功', async () => {
  await mountQuestion();
  assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
  assert.deepEqual(await page.evaluate(() => submitted), ['默认二']);
});

test('有新 footer 但反问仍待回答时不误判完成，保持总超时限制', async () => {
  await page.setContent('<div class="_questionFloating_fixture_1"><input placeholder="自由输入"></div><div class="conversation-finished-footer">中间态</div>');
  runner.execution.responseTimeout = 200;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
});

test('反问超时不抓分享、不追加后续轮，并回写未完成/跳过结果', async () => {
  let sent = 0, captures = 0;
  runner._openCleanConversation = async () => {};
  runner._footerCount = async () => 0;
  runner._sendOne = async () => { sent++; };
  runner._waitComplete = async () => ({ completed: false, reason: 'timeout' });
  runner.trace.captureTurn = async () => {};
  runner._captureShareLink = runner._captureRawMessage = async () => { captures++; return 'unexpected'; };
  const reports = [];
  const results = await runner.runConversationTurns([
    { caseId: 'q1', turnIndex: 0, question: '第一轮' },
    { caseId: 'q2', turnIndex: 1, question: '第二轮' },
  ], async r => { reports.push(r); });
  assert.equal(sent, 1);
  assert.equal(captures, 0);
  assert.equal(results[0].success, false);
  assert.equal(results[0].completeReason, 'timeout');
  assert.equal(results[1].completeReason, 'skipped');
  assert.equal(reports.length, 2);
});

test('隐藏卡片及其他确认类型不作为自动反问点击', async () => {
  await mountQuestion();
  await page.locator('._questionFloating_fixture_1').evaluate(e => { e.style.display = 'none'; });
  assert.equal(await runner._handleQuestion(), false);
  await page.locator('._questionFloating_fixture_1').evaluate(e => { e.style.display = ''; e.classList.add('_confirmVariant_fixture_1'); });
  assert.equal(await runner._handleQuestion(), false);
  assert.deepEqual(await page.evaluate(() => submitted), []);
});

// 工作流结构来自客户端 ExitPlanModeFloating / ExitPlanModePanel / PendingPlanPanel。
async function mountWorkflow(prefix = 'exit-plan-mode-floating', { selected = true, busy = false } = {}) {
  await page.setContent(`<button onclick="window.wrong=true">开始执行</button>
    <div class="${prefix}">
      <div class="${prefix}__title">确认按此计划执行？</div>
      <div role="button" tabindex="${busy ? '-1' : '0'}" class="${prefix}__option ${selected ? `${prefix}__option--selected` : ''}"
        onclick="approve(this)">1 开始执行</div>
      <input class="${prefix}__adjust-input" placeholder="修改计划">
      <button aria-label="发送" onclick="window.wrong=true">发送</button>
    </div><script>
      window.approvals=0;window.wrong=false;
      function approve(el) {
        window.approvals++; el.setAttribute('tabindex','-1');
        setTimeout(()=>{
          if ('${prefix}' === 'pending-plan-panel') el.parentElement.remove();
          else el.parentElement.innerHTML='<div class="${prefix}__decision--approved">方案已批准，开始执行...</div>';
          document.body.insertAdjacentHTML('beforeend','<div class="conversation-finished-footer">工作流完成</div>');
        },50);
      }
    </script>`);
}

for (const prefix of ['exit-plan-mode-floating', 'conversation-exit-plan-panel', 'pending-plan-panel']) {
  test(`工作流 ${prefix}：默认执行项直接提交，提交期间不重复点击`, async () => {
    await mountWorkflow(prefix);
    assert.equal(await runner._handleWorkflow(), true);
    await runner._handleWorkflow();
    assert.equal(await page.evaluate(() => approvals), 1);
    assert.equal(await page.evaluate(() => wrong), false);
    await page.locator('.conversation-finished-footer').waitFor();
  });
}

test('工作流无预选时选择默认执行项', async () => {
  await mountWorkflow('exit-plan-mode-floating', { selected: false });
  await runner._handleWorkflow();
  assert.equal(await page.evaluate(() => approvals), 1);
});

test('工作流已批准面板仍可见时不阻塞后续完成判定', async () => {
  await mountWorkflow();
  assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
  assert.equal(await page.evaluate(() => approvals), 1);
  assert.equal(await runner._handleWorkflow(), false);
});

test('跳过历史已批准面板，继续处理后续工作流确认', async () => {
  await mountWorkflow('conversation-exit-plan-panel');
  await page.evaluate(() => document.body.insertAdjacentHTML('afterbegin',
    '<div class="exit-plan-mode-floating"><div class="exit-plan-mode-floating__decision--approved">已批准</div></div>'));
  await runner._handleWorkflow();
  assert.equal(await page.evaluate(() => approvals), 1);
});

test('工作流提交中不能误判旧 footer 完成，也不重复提交', async () => {
  await mountWorkflow('exit-plan-mode-floating', { busy: true });
  await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div class="conversation-finished-footer">中间输出</div>'));
  runner.execution.responseTimeout = 200;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
  assert.equal(await page.evaluate(() => approvals), 0);
});

test('工作流新组件 aria-disabled 时等待可操作再提交', async () => {
  await mountWorkflow('conversation-exit-plan-panel');
  const option = page.locator('.conversation-exit-plan-panel__option');
  await option.evaluate(e => e.setAttribute('aria-disabled', 'true'));
  await runner._handleWorkflow();
  assert.equal(await page.evaluate(() => approvals), 0);
  await option.evaluate(e => e.setAttribute('aria-disabled', 'false'));
  await runner._handleWorkflow();
  assert.equal(await page.evaluate(() => approvals), 1);
});

test('工作流隐藏时及无关确认面板不点击', async () => {
  await mountWorkflow();
  await page.locator('.exit-plan-mode-floating').evaluate(e => { e.style.display = 'none'; });
  await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div role="dialog"><button onclick="window.wrong=true">开始执行</button></div>'));
  assert.equal(await runner._handleWorkflow(), false);
  assert.equal(await page.evaluate(() => approvals), 0);
  assert.equal(await page.evaluate(() => wrong), false);
});

async function mountShare({ failFirst = false, neverCopy = false, autoClose = false, stale = false,
  selection = 'true', selectionDelay = 0, selectionStuck = false, missingSelection = false } = {}) {
  await page.setContent(`
    <div class="assistantFeedback"><button aria-label="分享" onclick="openShare()">分享</button></div>
    <button class="wb-share-channel-btn" style="display:none" onclick="window.wrong=true">复制链接</button>
    <button id="new-task" onclick="window.newTasks++;">新建任务</button>
    <div contenteditable="true" role="textbox" onkeydown="if(event.key==='Enter')window.sent++"></div>
    <script>
      window.opens=0; window.closes=0; window.copies=0; window.sent=0; window.newTasks=0;
      window.selectionClicks=0; window.copySelections=[];
      window.clip='https://workbuddy.link/p/OLD'; window.markers=[];
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{
        writeText:async value=>{ if(${stale})throw Error('denied'); markers.push(value); window.clip=value; },
        readText:async()=>window.clip
      }});
      function closeShare(){ document.querySelector('.wb-share-bar__inner')?.remove(); window.closes++; document.querySelector('[contenteditable]').style.display=''; }
      function openShare(){
        window.opens++; document.querySelector('[contenteditable]').style.display='none';
        setTimeout(()=>{
          document.body.insertAdjacentHTML('beforeend',
            '<div class="wb-share-bar__inner" style="position:fixed;bottom:0;background:white">'+
            (${missingSelection} ? '' : '<label class="wb-share-bar__left"><button role="checkbox" aria-checked="${selection}" onclick="toggleAll(this)"></button><span>全选</span></label>')+
            '<button class="wb-share-channel-btn" data-track-id="share_copy_link" disabled onclick="copyShare()">复制链接</button>'+
            '<button aria-label="退出分享" onclick="closeShare()">退出分享</button></div>');
          if(!(${failFirst} && opens===1))setTimeout(()=>document.querySelector('[data-track-id=share_copy_link]').disabled=false,60);
        },30);
      }
      function toggleAll(el){
        window.selectionClicks++;
        const selected=el.getAttribute('aria-checked')!=='true';
        if(!${selectionStuck})setTimeout(()=>el.setAttribute('aria-checked',String(selected)),${selectionDelay});
      }
      function copyShare(){
        window.copies++;
        const selected=document.querySelector('.wb-share-bar__left [role=checkbox]')?.getAttribute('aria-checked');
        window.copySelections.push(selected);
        if(selected!=='true' || ${neverCopy})return;
        window.clip='无关复制内容 https://example.com/not-a-share';
        setTimeout(()=>{window.clip='【WorkBuddy】回归 https://workbuddy.link/p/NEW?ext2=copy_link'; if(${autoClose})closeShare();},70);
      }
    </script>`);
}

test('等复制按钮可点击、忽略隐藏同名按钮和无关剪贴板内容，退出底栏', async () => {
  await mountShare();
  assert.equal(await runner._captureShareLink(), 'https://workbuddy.link/p/NEW?ext2=copy_link');
  assert.equal(await page.locator('.wb-share-bar__inner').count(), 0);
  assert.equal(await page.locator('[contenteditable]').isVisible(), true);
  assert.equal(await page.evaluate(() => copies), 1);
  assert.equal(await page.evaluate(() => selectionClicks), 0);
  assert.equal(await page.evaluate(() => !!window.wrong), false);
});

for (const selection of ['false', 'mixed']) {
  test(`全选框 ${selection} 时先勾选并等待确认再复制，底栏关闭且下一轮可发送`, async () => {
    await mountShare({ selection, selectionDelay: 100 });
    assert.equal(await runner._captureShareLink(), 'https://workbuddy.link/p/NEW?ext2=copy_link');
    assert.equal(await page.evaluate(() => selectionClicks), 1);
    assert.deepEqual(await page.evaluate(() => copySelections), ['true']);
    assert.equal(await page.locator('.wb-share-bar__inner').count(), 0);
    await runner._sendOne({ question: '全选分享后的下一轮' });
    assert.equal(await page.evaluate(() => sent), 1);
  });
}

for (const options of [{ selectionStuck: true }, { missingSelection: true }]) {
  test(`全选${options.selectionStuck ? '点击后未生效' : '控件缺失'}时不复制，有限重试后退出底栏`, async () => {
    await mountShare({ selection: 'false', ...options });
    const warnings = [];
    runner.logger = { info() {}, warn(m) { warnings.push(m); } };
    assert.equal(await runner._captureShareLink(), '');
    assert.equal(await page.evaluate(() => copies), 0);
    assert.equal(await page.evaluate(() => opens), 2);
    assert.equal(await page.evaluate(() => closes), 2);
    assert.equal(await page.evaluate(() => selectionClicks), options.selectionStuck ? 2 : 0);
    assert.equal(await page.locator('[contenteditable]').isVisible(), true);
    assert.equal(warnings.filter(m => m.includes('分享全选未确认')).length, 2);
  });
}

test('复制成功自动关闭底栏也正常返回', async () => {
  await mountShare({ autoClose: true });
  assert.equal(await runner._captureShareLink(), 'https://workbuddy.link/p/NEW?ext2=copy_link');
  assert.equal(await page.evaluate(() => closes), 1);
});

test('首轮按钮一直禁用时关闭重开并重试，不 force 点击', async () => {
  await mountShare({ failFirst: true, selection: 'false' });
  assert.equal(await runner._captureShareLink(), 'https://workbuddy.link/p/NEW?ext2=copy_link');
  assert.equal(await page.evaluate(() => opens), 2);
  assert.equal(await page.evaluate(() => copies), 1);
  assert.equal(await page.evaluate(() => selectionClicks), 2);
  assert.deepEqual(await page.evaluate(() => copySelections), ['true']);
  assert.equal(await page.evaluate(() => new Set(markers).size), 2);
  assert.equal(await page.locator('.wb-share-bar__inner').count(), 0);
});

test('剪贴板不更新时有限重试并关闭底栏，不返回旧链接', async () => {
  await mountShare({ neverCopy: true });
  assert.equal(await runner._captureShareLink(), '');
  assert.equal(await page.evaluate(() => copies), 2);
  assert.equal(await page.evaluate(() => closes), 2);
  assert.equal(await page.locator('[contenteditable]').isVisible(), true);
});

test('无法写入哨兵时拒收旧链接，不触发分享复制', async () => {
  await mountShare({ stale: true });
  assert.equal(await runner._captureShareLink(), '');
  assert.equal(await page.evaluate(() => opens), 0);
  assert.equal(await page.evaluate(() => copies), 0);
});

test('分享底栏遗留时，新任务和同会话下一轮都先关闭再发送', async () => {
  await mountShare();
  await page.evaluate(() => openShare());
  await page.locator('.wb-share-bar__inner').waitFor();
  await runner._openCleanConversation();
  assert.equal(await page.evaluate(() => newTasks), 1);
  await page.evaluate(() => openShare());
  await page.locator('.wb-share-bar__inner').waitFor();
  await runner._sendOne({ question: '下一轮' });
  assert.equal(await page.evaluate(() => sent), 1);
  assert.equal(await page.evaluate(() => closes), 2);
});

test('无法点击新建任务时明确失败，不默默复用旧会话', async () => {
  await mountShare();
  runner.wb.newTaskSelector = '#missing-task';
  // 只替换该 locator 的点击超时，仍在真实 DOM 上验证丢失入口。
  const locate = page.locator.bind(page);
  page.locator = (sel, ...args) => {
    const loc = locate(sel, ...args);
    if (sel === '#missing-task') loc.first = () => ({ click: () => locate(sel).click({ timeout: 100 }) });
    return loc;
  };
  await assert.rejects(runner._openCleanConversation(), /Timeout/);
  assert.equal(await page.evaluate(() => sent), 0);
});
