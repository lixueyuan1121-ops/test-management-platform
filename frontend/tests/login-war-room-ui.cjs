const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await page.clock.install();
    const errors = [], logins = [];
    let overviewReads = 0;
    let loginOK = false, mode = 'failed';
    page.on('pageerror', error => errors.push(error.message));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      let data = [];
      if (path.endsWith('/auth/login')) {
        logins.push(request.postDataJSON());
        await new Promise(resolve => setTimeout(resolve, 500));
        if (!loginOK) return route.fulfill({ status: 401, json: { detail: 'Mock invalid credentials' } });
        data = { access_token: 'local-test-token', refresh_token: 'local-test-refresh' };
      }
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/stats/overview')) data = {};
      if (path.endsWith('/devices/overview')) {
        overviewReads++;
        if (mode !== 'ready' && mode !== 'empty') return route.fulfill({ status: 500, json: { msg: 'Mock device failure' } });
        data = { total_devices: mode === 'empty' ? 0 : 1, online_devices: mode === 'empty' ? 0 : 1, devices: mode === 'empty' ? [] : [{ id: 1, name: '测试设备'.repeat(12), online: true, run_counts: { running: 1 }, active_runs: [{ run_id: 9, title: '执行中的长标题'.repeat(20), started_at: '2026-09-10T01:00:00Z' }] }] };
      }
      if (path.endsWith('/stats/ai-funnel')) {
        if (mode === 'failed') return route.fulfill({ status: 500, json: { msg: 'Mock funnel failure' } });
        data = { funnel: mode === 'empty' ? [] : [{ stage: 'passed', label: '执行通过', count: 8 }, { stage: 'generated', label: '已生成', count: 20 }, { stage: 'executed', label: '已执行', count: 10 }], bugs_found: 2 };
      }
      if (path.endsWith('/feedback/defense-calendar')) {
        if (mode === 'failed') return route.fulfill({ status: 500, json: { msg: 'Mock calendar failure' } });
        data = { days: [{ date: '2026-09-10', state: 'gray', runs: 0 }], streak: 0, total_guard_days: 0 };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/login`);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    assert.equal(logins.length, 0);
    await page.getByPlaceholder('请输入用户名').fill('mock-user');
    await page.getByPlaceholder('请输入密码').fill('mock-password');
    await page.screenshot({ path: '/tmp/login-desktop.png' });
    await page.getByPlaceholder('请输入密码').press('Enter');
    await page.locator('form').dispatchEvent('submit');
    await page.getByText('登录未成功，请核对账号密码或稍后重试', { exact: true }).waitFor();
    assert.equal(logins.length, 1);
    assert.deepEqual(logins[0], { username: 'mock-user', password: 'mock-password' });
    await page.setViewportSize({ width: 390, height: 480 });
    await page.getByRole('button', { name: '登录', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/tmp/login-mobile.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    loginOK = true;
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL('**/dashboard');
    assert.equal(logins.length, 2);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`${base}/war-room`);
    await page.getByText('尚未获取设备数据', { exact: true }).waitFor();
    assert.equal(await page.locator('.el-message--error').count(), 0);
    assert.equal(await page.getByText('暂无注册设备', { exact: true }).count(), 0);
    assert.deepEqual(await page.locator('.wr-num').allTextContents(), ['—', '—%', '—/ —', '—', '—']);
    mode = 'ready';
    await page.getByRole('button', { name: '刷新大屏', exact: true }).click();
    await page.locator('.wr-dev').waitFor();
    assert.match(await page.locator('.wr-num').nth(0).innerText(), /^10$/);
    assert.match(await page.locator('.wr-num').nth(1).innerText(), /80/);
    const completeAt = await page.locator('.wr-foot').innerText();
    const devicesAt = await page.locator('.wr-ph').nth(1).innerText();
    await page.screenshot({ path: '/tmp/war-room-desktop.png' });
    await page.waitForTimeout(1100);
    mode = 'partial';
    await page.getByRole('button', { name: '刷新大屏', exact: true }).click();
    await page.getByText('设备更新失败', { exact: true }).waitFor();
    assert.equal(await page.locator('.wr-foot').innerText(), completeAt);
    assert.equal(await page.locator('.wr-ph').nth(1).innerText(), devicesAt);
    assert.equal(await page.locator('.wr-dev').count(), 1);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/war-room-mobile.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    mode = 'empty';
    await page.getByRole('button', { name: '刷新大屏', exact: true }).click();
    await page.getByText('暂无注册设备', { exact: true }).waitFor();
    assert.equal(await page.getByText('尚未获取设备数据', { exact: true }).count(), 0);
    const beforePoll = overviewReads;
    const pollResponse = page.waitForResponse(response => response.url().endsWith('/devices/overview'));
    await page.clock.runFor(31000);
    await pollResponse;
    assert(overviewReads > beforePoll);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '收起侧栏', exact: true }).waitFor();
    await page.getByRole('textbox', { name: '查找功能' }).fill('项目管理');
    await page.getByRole('menuitem', { name: '项目管理', exact: true }).click();
    await page.getByRole('heading', { name: '项目管理', exact: true }).waitFor();
    const afterLeave = overviewReads;
    await page.clock.runFor(31000);
    assert.equal(overviewReads, afterLeave);
    assert.deepEqual(errors, []);
    console.log('PASS login validation/duplicate prevention/failure retry, responsive layouts, war room initial failure/stale timestamps/recovery/stage counts');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
