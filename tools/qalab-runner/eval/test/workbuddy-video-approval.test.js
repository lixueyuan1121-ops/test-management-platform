// 结构来自 WorkBuddy 5.5.6 的 HighCreditApprovalFloatingPanel / PendingHighCreditApproval。
// 仅模拟权限请求和生成状态，不调用真实视频服务、不消耗积分。
const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { workbuddy } = require('../config/default.config');

let browser, page, runner, logs;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage(); logs = [];
  runner = new WorkbuddyRunner(page, { ...workbuddy, videoApprovalRetryMs: 0 }, { responseTimeout: 5000 },
    { info: text => logs.push(text), warn: text => logs.push(text) });
});
afterEach(async () => { await runner._clearVideoApproval?.(); await page.close(); });

const zh = { title: '确认开始生成视频？', confirm: '确认', session: '本次会话始终允许', reject: '拒绝' };
const variants = ['high-credit-approval-floating', 'conversation-high-credit-approval'];
async function mount({ variant = variants[1], language = zh, disabled = false, failTimes = 0,
  stuck = false, loadingAfter = false } = {}) {
  await page.setContent('<main></main><div class="conversation-finished-footer">旧轮次消耗</div><button id="unrelated">确认</button>');
  await page.evaluate(({ variant, language, disabled, failTimes, stuck, loadingAfter }) => {
    window.approvals = 0; window.wrong = 0;
    document.querySelector('#unrelated').onclick = () => window.wrong++;
    window.mountCard = () => {
      const tag = variant === 'high-credit-approval-floating' ? 'div' : 'button';
      document.querySelector('main').innerHTML = `<div class="${variant}" role="dialog" aria-label="${language.title}">
        <div class="${variant}__header"><span class="${variant}__title">${language.title}</span>
          <span class="${variant}__risk-tag">积分消耗较高</span>
          <div class="${variant}__description">生成视频将消耗额外积分，每 5 秒约 50-100 积分，请谨慎操作</div></div>
        <div class="${variant}__content">${[language.confirm, language.session, language.reject].map((label, i) =>
          `<${tag} class="${variant}__option${i === 1 ? ` ${variant}__option--selected` : ''}" role="button"
            tabindex="${disabled ? -1 : 0}" ${tag === 'button' && disabled ? 'disabled' : ''}>
            <span class="${variant}__option-num">${i + 1}</span><span class="${variant}__option-text">${label}</span>
          </${tag}>`).join('')}</div></div>`;
      const card = document.querySelector('main > div');
      const options = [...card.querySelectorAll(`.${variant}__option`)];
      window.setReady = value => options.forEach(el => { el.tabIndex = value ? 0 : -1; if (tag === 'button') el.disabled = !value; });
      window.resolveApproval = () => {
        card.innerHTML = `<div class="${variant}__decision ${variant}__decision--approved">已确认</div>`;
        if (loadingAfter) document.body.insertAdjacentHTML('beforeend', '<button class="cr-send-button--stop">停止</button>');
      };
      options.slice(1).forEach(el => el.onclick = () => window.wrong++);
      options[0].onmouseenter = () => options[0].classList.add(`${variant}__option--selected`);
      options[0].onclick = () => {
        window.approvals++;
        window.setReady(false);
        if (stuck) return;
        setTimeout(() => {
          if (window.approvals <= failTimes) window.setReady(true);
          else window.resolveApproval();
        }, 30);
      };
    };
    window.mountCard();
  }, { variant, language, disabled, failTimes, stuck, loadingAfter });
}

test('视频积分卡走完整等待流程：确认一次，等待生成结束后才完成', async () => {
  await mount({ loadingAfter: true });
  const completed = runner._waitComplete(0);
  await page.locator('.cr-send-button--stop').waitFor({ timeout: 2000 });
  assert.equal(await page.evaluate(() => approvals), 1);
  await page.waitForTimeout(1600);
  let settled = false; completed.then(() => settled = true);
  await page.waitForTimeout(0);
  assert.equal(settled, false, '旧 footer 不能让视频生成提前完成');
  await page.locator('.cr-send-button--stop').evaluate(el => el.remove());
  assert.deepEqual(await completed, { completed: true, reason: 'footer' });
  assert.equal(await page.evaluate(() => wrong), 0);
  assert(logs.some(text => /视频生成确认.*已点击.*确认/.test(text)));
});

for (const variant of variants) {
  for (const language of [zh,
    { title: '確認開始生成影片？', confirm: '確認', session: '本次會話始終允許', reject: '拒絕' },
    { title: 'Confirm video generation?', confirm: 'Confirm', session: 'Always allow this session', reject: 'Reject' },
  ]) {
    test(`${variant} ${language.confirm}：按文案选单次确认，兼容默认选中会话授权及选项换序`, async () => {
      await mount({ variant, language, stuck: true });
      await page.locator(`.${variant}__content`).evaluate(el => el.prepend(el.children[1]));
      assert.equal(await runner._handleVideoApproval(), true);
      await runner._handleVideoApproval();
      assert.equal(await page.evaluate(() => approvals), 1);
      assert.equal(await page.evaluate(() => wrong), 0);
    });
  }

  test(`${variant}：禁用时等待，提交中不重试`, async () => {
    await mount({ variant, disabled: true, stuck: true });
    assert.equal(await runner._handleVideoApproval(), true);
    assert.equal(await page.evaluate(() => approvals), 0);
    await page.evaluate(() => window.setReady(true));
    await runner._handleVideoApproval();
    await runner._handleVideoApproval();
    assert.equal(await page.evaluate(() => approvals), 1);
  });

  test(`${variant}：失败恢复可操作后有限重试，hover 选中不代表已经提交`, async () => {
    await mount({ variant, failTimes: 1 });
    assert.deepEqual(await runner._waitComplete(0), { completed: true, reason: 'footer' });
    assert.equal(await page.evaluate(() => approvals), 2);
    assert.equal(await page.evaluate(() => wrong), 0);
  });
}

test('持续失败到上限后明确报错，避免无限重复确认', async () => {
  await mount({ failTimes: 99 });
  runner.wb.videoApprovalMaxAttempts = 2;
  await assert.rejects(() => runner._waitComplete(0), /WORKBUDDY_CONFIRM_FAILED.*视频生成/);
  assert.equal(await page.evaluate(() => approvals), 2);
});

test('同标题新卡片可再次确认，已确认卡片不阻塞等待', async () => {
  await mount({ stuck: true });
  await runner._handleVideoApproval();
  await page.evaluate(() => window.resolveApproval());
  assert.equal(await runner._handleVideoApproval(), false);
  await page.evaluate(() => window.mountCard());
  await runner._handleVideoApproval();
  assert.equal(await page.evaluate(() => approvals), 2);
});

test('缺少单次确认或未知类型不能点击会话授权和正文同名按钮', async () => {
  for (const language of [{ ...zh, confirm: zh.session }, { ...zh, title: '确认开始生成图片？' },
    { ...zh, title: '确认开始处理图片？' }, { ...zh, reject: '未知选项' }]) {
    await mount({ language });
    assert.equal(await runner._handleVideoApproval(), true);
    assert.equal(await page.evaluate(() => approvals + wrong), 0);
  }
  await page.locator('main').evaluate(el => el.style.display = 'none');
  assert.equal(await runner._handleVideoApproval(), false);
});

test('视频确认未处理时不能因旧 footer 误报成功', async () => {
  await mount({ disabled: true });
  runner.execution.responseTimeout = 150;
  assert.deepEqual(await runner._waitComplete(0), { completed: false, reason: 'timeout' });
  assert.equal(await page.evaluate(() => approvals), 0);
});

test('读取过程中卡片卸载时不等待旧按钮重现或误点其他确认', async () => {
  await mount();
  await page.locator('main > div').evaluate(el => {
    const query = el.querySelector.bind(el);
    el.querySelector = selector => {
      const found = query(selector);
      el.remove();
      return found;
    };
  });
  assert.equal(await runner._handleVideoApproval(), true);
  assert.equal(await runner._handleVideoApproval(), false);
  assert.equal(await page.evaluate(() => approvals + wrong), 0);
});
