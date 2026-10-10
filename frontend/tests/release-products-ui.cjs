const { chromium } = require('../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(15000);
    const errors = [], writes = [], products = ['纳米Work Android端', '纳米Work iOS端'];
    let admin = true, failSave = true;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: admin, memberships: [{ project_id: 1, role: 'member' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '应用项目', platform_type: 'app' }];
      if (path === '/api/releases') data = { items: [], total: 0 };
      if (path === '/api/releases/stats') data = { total_releases: 0, total_reqs: 0, this_month: 0, latest_date: null, trend: [] };
      if (path === '/api/releases/quality') data = { items: [] };
      if (path === '/api/releases/products') {
        data = products;
        if (req.method() === 'POST') {
          writes.push(req.postDataJSON());
          if (failSave) return route.fulfill({ status: 500, json: { msg: '模拟新增失败' } });
          products.push(writes.at(-1).name);
          data = { id: 1, ...writes.at(-1) };
        }
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://localhost:8000';
    await page.goto(`${base}/releases`);
    await page.getByRole('button', { name: '新增产品', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '新增产品', exact: true });
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('请输入产品名称', { exact: true }).waitFor();
    assert.equal(writes.length, 0);
    await dialog.getByPlaceholder('请输入产品名称').fill('纳米Work iOS端');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('该产品已存在', { exact: true }).waitFor();
    assert.equal(writes.length, 0);
    await dialog.getByPlaceholder('请输入产品名称').fill('  测试新产品  ');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟新增失败', { exact: true }).waitFor();
    assert.equal(await dialog.getByPlaceholder('请输入产品名称').inputValue(), '  测试新产品  ');
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1), { project_id: 1, name: '测试新产品' });
    assert(await page.getByRole('radio', { name: '测试新产品', exact: true }).isChecked());
    await page.reload();
    await page.getByRole('radio', { name: '测试新产品', exact: true }).waitFor();
    await page.getByRole('button', { name: '登记发版', exact: true }).click();
    const releaseDialog = page.getByRole('dialog', { name: '登记发版', exact: true });
    await releaseDialog.locator('.el-form-item').filter({ has: page.locator('label', { hasText: /^子产品$/ }) }).locator('.el-select__wrapper').click();
    await page.getByRole('option', { name: '测试新产品', exact: true }).click();
    await releaseDialog.getByRole('button', { name: '取消', exact: true }).click();
    admin = false;
    await page.reload();
    await page.getByRole('radio', { name: '测试新产品', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '新增产品', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '登记发版', exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS product validation, failed-save retention, add/filter, refresh, release options, non-admin controls');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
