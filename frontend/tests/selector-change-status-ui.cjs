const { chromium } = require(process.env.PLAYWRIGHT_PATH || '../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  let context;
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [], writes = [];
    const rows = ['new', 'updated', 'retired', ''].map((status, index) => ({ id: index + 1, key: `control_${index}`, page: '测试页', frame: 'shell', platform: 'web', change_status: status, desc: `[连接器]-[安全设置]-[安全设置页]-[安全设置行${index}]`, revision: `rev-${index}`, candidates: [{ by: 'testid', value: `control-${index}` }] }));
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '状态验证' }];
      if (path === '/api/selectors/manage') data = { shared: rows, by_sub: {}, scope: {} };
      if (req.method() === 'PATCH') {
        const body = req.postDataJSON(); writes.push(body);
        Object.assign(rows[0], body); data = rows[0];
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:8000'}/selectors`);
    await page.getByRole('button', { name: '编辑', exact: true }).first().waitFor();
    assert.equal(await page.getByRole('columnheader', { name: '候选数', exact: true }).count(), 0);
    assert.equal(await page.getByRole('columnheader', { name: '状态', exact: true }).count(), 1);
    for (const label of ['新增', '更新', '废弃']) assert.equal(await page.locator('.el-table .el-tag').filter({ hasText: label }).count(), 1);
    assert(!(await page.locator('.el-table').allInnerTexts()).join(' ').includes('[DOM'));
    await page.getByRole('button', { name: '编辑', exact: true }).first().click();
    const dialog = page.getByRole('dialog', { name: '编辑 key', exact: true });
    assert.equal(await dialog.getByRole('textbox', { name: '元素命名及类型', exact: true }).inputValue(), '安全设置行0');
    const statusField = dialog.locator('.el-form-item').filter({ has: page.locator('.el-form-item__label', { hasText: /^状态$/ }) });
    await statusField.locator('.el-select').click();
    await page.getByRole('option', { name: '废弃', exact: true }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(writes.at(-1).change_status, 'retired');
    assert.equal(writes.at(-1).desc, '[连接器]-[安全设置]-[安全设置页]-[安全设置行0]');
    assert.equal(writes.at(-1).expected_revision, 'rev-0');
    assert.deepEqual(errors, []);
    await page.screenshot({ path: '../backend/artifacts/selector-management/status-ui.png', fullPage: true });
    console.log('PASS status column, new/updated/retired rendering, clean descriptions and status editing; API fixtures only');
  } finally { if (context) await context.close(); await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
