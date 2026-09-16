// WorkBuddy InterceptCard / SandboxInterceptCard 的实际 DOM 结构和提交状态。
// 回归仅模拟 UI 回调，不执行卡片展示的命令、不删除文件。
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
  runner = new WorkbuddyRunner(page, { ...workbuddy, interceptRetryMs: 0 }, { responseTimeout: 4000 });
});
afterEach(async () => { await runner._clearInterceptApproval(); await page.close(); });

async function mount({ title = '检测到批量删除操作：累计删除数量 387 项，阈值 50',
  allow = '允许本次删除', cancel = '取消删除', session = '', selected = 'cancel', disabled = false,
  failTimes = 0, stuck = false, loadingAfter = false } = {}) {
  await page.setContent('<main></main><div class="conversation-finished-footer">中间输出</div><button id="unrelated">允许本次删除</button>');
  await page.evaluate(({ title, allow, cancel, session, selected, disabled, failTimes, stuck, loadingAfter }) => {
    window.approvals = 0; window.wrong = 0;
    document.querySelector('#unrelated').onclick = () => window.wrong++;
    window.mountCard = () => {
      document.querySelector('main').innerHTML = `<div class="_container_fixture_40">
        <div class="_header_fixture_53"><div class="_titleRow_fixture_69">
          <span class="_title_fixture_69">${title}</span><span class="_riskText_fixture_99">高风险命令，可能影响系统稳定性</span>
        </div><span class="_commandInline_fixture_137">synthetic cleanup placeholder</span></div>
        <div class="_optionList_fixture_152">
          <button type="button" class="_optionItem_fixture_159 ${selected === 'allow' ? '_optionSelected_fixture_196' : ''}" ${disabled ? 'disabled' : ''}>
            <span class="_optionIndex_fixture_207">1</span><span class="_optionLabel_fixture_233">${allow}</span><svg></svg></button>
          ${session ? `<button type="button" class="_optionItem_fixture_159 ${selected === 'session' ? '_optionSelected_fixture_196' : ''}">
            <span class="_optionIndex_fixture_207">2</span><span class="_optionLabel_fixture_233">${session}</span><svg></svg></button>` : ''}
          <button type="button" class="_optionItem_fixture_159 ${selected === 'cancel' ? '_optionSelected_fixture_196' : ''}">
            <span class="_optionIndex_fixture_207">${session ? 3 : 2}</span><span class="_optionLabel_fixture_233">${cancel}</span><svg></svg></button>
        </div></div>`;
      const card = document.querySelector('main > div');
      const buttons = card.querySelectorAll('button');
      [...buttons].slice(1).forEach(button => button.onclick = () => window.wrong++);
      buttons[0].onclick = () => {
        window.approvals++;
        buttons.forEach(b => { b.disabled = true; b.classList.remove('_optionSelected_fixture_196'); });
        buttons[0].classList.add('_optionSelected_fixture_196');
        if (stuck) return;
        setTimeout(() => {
          buttons.forEach(b => b.disabled = false);
          if (window.approvals <= failTimes) buttons[0].classList.remove('_optionSelected_fixture_196');
          else {
            card.remove();
            if (loadingAfter) document.body.insertAdjacentHTML('beforeend', '<button class="cr-send-button cr-send-button--sending cr-send-button--stop">停止</button>');
          }
        }, 30);
      };
    };
    window.mountCard();
  }, { title, allow, cancel, session, selected, disabled, failTimes, stuck, loadingAfter });
}

const protectedFile = { title: '检测到受保护文件修改', allow: '允许', session: '本次会话内始终允许', cancel: '拒绝' };
for (const language of [
  protectedFile,
  { title: '檢測到受保護檔案修改', allow: '允許', session: '本次會話內始終允許', cancel: '拒絕' },
  { title: 'Detected modification to a protected file', allow: 'Allow', session: 'Always allow this kind of command for this session', cancel: 'Deny, keep running in the sandbox' },
]) {
  test(`${language.title}：从三个选项中精确选单次允许，不采用会话授权默认项`, async () => {
    await mount({ ...language, selected: 'session' });
    // 选项顺序改变后仍按标签选择，不能依赖“第一项”。
    await page.locator('[class*="_optionList_"]').evaluate(el => el.prepend(el.children[1]));
    assert.equal(await runner._handleIntercept(), true);
    await runner._handleIntercept();
    assert.equal(await page.evaluate(() => approvals), 1);
    assert.equal(await page.evaluate(() => wrong), 0);
  });
}

test('受保护文件确认走完整等待流程，网络提交失败后有限重试', async () => {
  await mount({ ...protectedFile, failTimes: 1 });
  assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
  assert.equal(await page.evaluate(() => approvals), 2);
  assert.equal(await page.evaluate(() => wrong), 0);
});

test('受保护文件卡缺少单次允许或拒绝时，不使用会话级选项替代', async () => {
  for (const overrides of [{ allow: protectedFile.session }, { cancel: '未知' }]) {
    await mount({ ...protectedFile, ...overrides });
    assert.equal(await runner._handleIntercept(), true);
    assert.equal(await page.evaluate(() => approvals), 0);
  }
});

test('同样的允许/拒绝文案不能让未知权限类型被自动批准', async () => {
  for (const title of ['是否允许将敏感凭证发送到外部？', '检测到用户配置黑名单目录', '普通文字中提到检测到受保护文件修改']) {
    await mount({ ...protectedFile, title });
    assert.equal(await runner._handleIntercept(), true);
    assert.equal(await page.evaluate(() => approvals), 0);
    assert.equal(await page.evaluate(() => wrong), 0);
  }
});

for (const language of [
  {},
  { title: '檢測到批次刪除操作：累計刪除數量 387 項，閾值 50', allow: '允許本次刪除', cancel: '取消刪除' },
  { title: 'Detected bulk delete operation: cumulative delete count is 387 items, threshold 50', allow: 'Allow this delete', cancel: 'Cancel delete' },
]) {
  test(`批量删除 ${language.allow || '允许本次删除'}：精确选择单次允许，忽略数字前缀和取消默认项`, async () => {
    await mount(language);
    assert.equal(await runner._handleIntercept(), true);
    await runner._handleIntercept();
    assert.equal(await page.evaluate(() => approvals), 1);
    assert.equal(await page.evaluate(() => wrong), 0);
    await page.locator('main > div').waitFor({ state: 'detached' });
    assert.equal(await runner._handleIntercept(), false);
  });
}

test('提交中保留旧 footer 时等待，不重复允许或误报完成', async () => {
  await mount({ stuck: true });
  runner.execution.responseTimeout = 200;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
  assert.equal(await page.evaluate(() => approvals), 1);
  assert.equal(await page.evaluate(() => wrong), 0);
});

test('按钮禁用时等待可操作；提交已成功但卡片尚未移除时不重复点击', async () => {
  await mount({ disabled: true, stuck: true });
  await runner._handleIntercept();
  assert.equal(await page.evaluate(() => approvals), 0);
  await page.locator('main button').first().evaluate(el => el.disabled = false);
  await runner._handleIntercept();
  await page.locator('main button').first().evaluate(el => el.disabled = false);
  await runner._handleIntercept();
  assert.equal(await page.evaluate(() => approvals), 1);
});

test('客户端明确恢复提交失败态后有限重试并继续任务', async () => {
  await mount({ failTimes: 1 });
  assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
  assert.equal(await page.evaluate(() => approvals), 2);
  assert.equal(await page.evaluate(() => wrong), 0);
});

test('持续提交失败在上限后显式失败，不无限确认', async () => {
  await mount({ failTimes: 99 });
  runner.wb.interceptMaxAttempts = 2;
  await assert.rejects(() => runner._waitComplete(0), /WORKBUDDY_CONFIRM_FAILED/);
  assert.equal(await page.evaluate(() => approvals), 2);
});

test('连续出现相同命令的新卡片仍可确认，旧卡片的提交状态不串用', async () => {
  await mount({ stuck: true });
  await runner._handleIntercept();
  await page.evaluate(() => window.mountCard());
  await runner._handleIntercept();
  assert.equal(await page.evaluate(() => approvals), 2);
});

test('同一个 DOM 切换成下一条拦截命令后重新确认', async () => {
  await mount({ stuck: true });
  await runner._handleIntercept();
  await page.evaluate(() => {
    document.querySelector('[class*="_commandInline_"]').textContent = 'next synthetic cleanup';
    document.querySelectorAll('main button').forEach(el => el.disabled = false);
  });
  await runner._handleIntercept();
  assert.equal(await page.evaluate(() => approvals), 2);
});

test('卡片消失后仍在生成时不按旧 footer 收尾，生成结束才成功', async () => {
  await mount({ loadingAfter: true });
  await runner._handleIntercept();
  await page.locator('button.cr-send-button--stop').waitFor();
  runner.execution.responseTimeout = 200;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
  await page.locator('button.cr-send-button--stop').evaluate(el => el.remove());
  runner.execution.responseTimeout = 3000;
  assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
});

test('隐藏卡片、同名正文按钮和其他拦截类型不会被误确认', async () => {
  await mount();
  await page.locator('main').evaluate(el => el.style.display = 'none');
  assert.equal(await runner._handleIntercept(), false);
  await page.locator('main').evaluate(el => el.style.display = '');
  await page.locator('[class*="_title_fixture_"]').evaluate(el => el.textContent = '是否允许将敏感凭证发送到外部？');
  runner.execution.responseTimeout = 200;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
  assert.equal(await page.evaluate(() => approvals), 0);
  assert.equal(await page.evaluate(() => wrong), 0);
});

test('不完整卡片或会话级授权不能替代单次删除确认', async () => {
  for (const options of [{ allow: '本次会话内始终允许' }, { cancel: '未知选项' }]) {
    await mount(options);
    assert.equal(await runner._handleIntercept(), true);
    assert.equal(await page.evaluate(() => approvals), 0);
  }
});

test('读取过程中卡片卸载时快速退出，不等待旧 locator 重现或误点下一张', async () => {
  await mount();
  await page.locator('main > div').evaluate(el => {
    const query = el.querySelector.bind(el);
    el.querySelector = selector => {
      const result = query(selector);
      el.remove();
      return result;
    };
  });
  const started = Date.now();
  assert.equal(await runner._handleIntercept(), true);
  assert(Date.now() - started < 2000);
  assert.equal(await runner._handleIntercept(), false);
  assert.equal(await page.evaluate(() => approvals), 0);
});
