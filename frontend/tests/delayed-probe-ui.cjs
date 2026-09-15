const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
    page.setDefaultTimeout(90000);
    const errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname;
      let data = [];
      if (path === '/api/projects') data = [{ id: 2, name: '验证项目' }];
      if (path === '/api/devices') data = [{ id: 1, runner_id: 'bendi-win', name: '本地版' }];
      if (path === '/api/selectors/manage') data = { shared: [], by_sub: {} };
      if (path === '/api/ai/cases') data = { items: [], total: 0 };
      if (path === '/api/probe' && route.request().method() === 'POST') {
        requests.push(route.request().postDataJSON()); data = { id: requests.length };
      }
      if (/^\/api\/probe\/\d+$/.test(path)) data = { status: 'done', result: { groups: [] } };
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto((process.env.UI_BASE_URL || 'http://127.0.0.1:5197') + '/tests/fixtures/selector-targets.html');
    await page.waitForFunction(() => !!window.fixtureRouter);
    const enter = () => page.evaluate(() => window.fixtureRouter.push({ name: 'selectors', query: { project_id: '2', fix_keys: 'copyToast', bulk: '1' } }));
    await enter();
    const delay = page.getByRole('button', { name: '5 秒后探测', exact: true });
    await delay.waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === '5 秒后探测' && !b.disabled));
    assert.equal(requests.length, 0);
    await delay.click();
    await page.getByText('5 秒后发起探测，请切到客户端并保持悬浮', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '探测(扫当前页)', exact: true }).isDisabled());
    assert(await delay.isDisabled());
    assert(await page.getByPlaceholder('关键词过滤(可选，按文本 contains)').isDisabled());
    await page.getByRole('button', { name: '取消倒计时', exact: true }).click();
    await page.waitForTimeout(5300);
    assert.equal(requests.length, 0, '取消后不得提交任务');
    const select = page.getByRole('combobox', { name: '延时探测等待时间' });
    await page.locator('.el-select').filter({ has: select }).click();
    for (const seconds of [3, 5, 10]) await page.getByRole('option', { name: `${seconds} 秒`, exact: true }).waitFor({ state: 'visible' });
    await page.getByRole('option', { name: '3 秒', exact: true }).click();
    const started = Date.now();
    await page.getByRole('button', { name: '3 秒后探测', exact: true }).click();
    await page.locator('.probe-countdown').waitFor();
    if (process.env.UI_SCREENSHOT_DIR) await page.screenshot({ path: process.env.UI_SCREENSHOT_DIR + '/countdown.png', fullPage: true });
    await page.waitForTimeout(1500);
    assert.equal(requests.length, 0, '等待期间不得提前发送');
    await page.getByText('探测完成,共识别 0 个元素', { exact: true }).waitFor();
    assert(Date.now() - started >= 3000);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].runner, 'bendi-win');
    assert.equal(requests[0].runner_device_id, 1);
    assert.equal(requests[0].project_id, 2);
    await page.getByRole('button', { name: '3 秒后探测', exact: true }).click();
    await page.evaluate(() => window.fixtureRouter.push('/'));
    await page.waitForTimeout(3500);
    assert.equal(requests.length, 1, '离开页面后不得提交任务');
    await enter();
    await page.getByRole('button', { name: '5 秒后探测', exact: true }).waitFor();
    assert.equal(await page.locator('.probe-countdown').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: 默认5秒、3/5/10选择、倒计时锁定、取消、单次提交、完成解锁、离页取消');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
