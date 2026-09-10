const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failUsage = true;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '项目一' }];
      if (path === '/api/devices') data = [{ id: 1, name: '测试机', runner_id: 'mock-runner' }];
      if (path === '/api/selectors/manage') data = { shared: [{ id: 11, key: 'login', page: '登录页', frame: 'auto', candidates: [{ by: 'text', value: '登录' }] }], by_sub: {} };
      if (path === '/api/selectors/11/usage') {
        if (failUsage) return route.fulfill({ status: 500, json: { msg: '模拟引用查询失败' } });
        data = { count: 1, cases: [{ title: '登录验证' }] };
      }
      if (req.method() !== 'GET') {
        writes.push({ path, method: req.method(), project: url.searchParams.get('project_id'), body: req.postData() ? req.postDataJSON() : null });
        if (path === '/api/selectors/import-legacy') data = { imported: 1, skipped: 2 };
        if (path === '/api/selectors/11' && req.method() === 'DELETE') data = { downgraded: 1 };
        if (path === '/api/probe') data = { id: 31 };
      }
      if (path === '/api/probe/31') data = { status: 'done', result: { groups: [{ frame: 'shell', elements: [{ tag: 'button', text: '新按钮', best: { by: 'css', value: '#new' }, candidates: [{ by: 'css', value: '#new' }] }] }] } };
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/selectors`);
    await page.getByRole('button', { name: '删除', exact: true }).click();
    await page.getByText('无法确认引用范围，暂未删除，请重试', { exact: true }).waitFor();
    assert.equal(writes.length, 0);
    failUsage = false;
    await page.getByRole('button', { name: '删除', exact: true }).click();
    await page.locator('.el-message-box').getByText(/登录验证/).waitFor();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 0);
    await page.getByRole('button', { name: '删除', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认删除', exact: true }).click();
    await page.getByText('已删除,1 条用例已降级为「选择器待补」', { exact: true }).waitFor();
    assert.equal(writes.at(-1).path, '/api/selectors/11');
    const count = writes.length;
    await page.getByRole('button', { name: '导入内置纳米Work注册表', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, count);
    await page.getByRole('button', { name: '导入内置纳米Work注册表', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认导入', exact: true }).click();
    await page.getByText('导入完成：新增 1 个，跳过 2 个', { exact: true }).waitFor();
    assert.equal(writes.at(-1).project, '1');
    await page.getByRole('tab', { name: '设备探测', exact: true }).click();
    await page.locator('.el-select').filter({ hasText: '选择在线设备' }).click();
    await page.getByRole('option', { name: '测试机（mock-runner）', exact: true }).click();
    await page.getByRole('button', { name: '探测(扫当前页)', exact: true }).click();
    await page.getByRole('button', { name: '加为 key', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: '加为 key', exact: true });
    await dialog.getByLabel('key 名', { exact: true }).fill('new_button');
    await dialog.getByLabel('说明', { exact: true }).fill('新按钮');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.filter(w => w.path === '/api/selectors').at(-1).body, { project_id: 1, sub_product: '', platform: 'web', key: 'new_button', frame: 'shell', page: '', desc: '新按钮', candidates: [{ by: 'css', value: '#new' }] });
    await page.getByRole('button', { name: '加为 key', exact: true }).click();
    await dialog.getByText('更新已有 key', { exact: true }).click();
    await dialog.locator('.el-select').click();
    await page.getByRole('option', { name: 'login（1 候选）', exact: true }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.filter(w => w.path === '/api/selectors/11' && w.method === 'PATCH').at(-1).body, { candidates: [{ by: 'css', value: '#new' }, { by: 'text', value: '登录' }] });
    assert.deepEqual(errors, []);
    console.log('PASS selector usage failure blocks deletion, impact confirmation/cancel, legacy import and discovered key create/update candidate payloads');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
