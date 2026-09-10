const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => { localStorage.setItem('tp_token', 'mock-local-only'); localStorage.setItem('tp_sidebar_collapsed', '0'); });
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/stats/overview') data = {};
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/projects`);
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.waitForTimeout(300);
    assert.equal(Math.round((await page.locator('.aside').boundingBox()).width), 64);
    await page.getByRole('button', { name: '展开侧栏', exact: true }).click();
    await page.getByPlaceholder('查找功能').fill('我的设备');
    await page.getByRole('menuitem', { name: '我的设备', exact: true }).click();
    await page.waitForURL('**/my-devices');
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('tp_sidebar_collapsed')), '0');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '收起侧栏', exact: true }).waitFor();
    await page.getByRole('menuitem', { name: '我的设备', exact: true }).waitFor();
    await page.getByPlaceholder('查找功能').fill('发版');
    await page.getByRole('menuitem', { name: '发版记录', exact: true }).waitFor();
    await page.getByPlaceholder('查找功能').fill('不存在的功能');
    await page.getByText('无匹配功能', { exact: true }).waitFor();
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    assert.equal(await page.evaluate(() => localStorage.getItem('tp_sidebar_collapsed')), '1');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('tp_sidebar_collapsed')), '1');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/navigation-responsive-mobile.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS narrow default, mobile navigation collapse, independent desktop preference, grouped search and viewport transitions');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
