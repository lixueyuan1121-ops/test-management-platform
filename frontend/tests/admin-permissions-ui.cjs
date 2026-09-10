const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let admin = false, role = 'member', failSave = true;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (req.method() !== 'GET') {
        writes.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null });
        if (failSave) return route.fulfill({ status: 500, json: { msg: '模拟管理保存失败' } });
      }
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: admin, memberships: [{ project_id: 1, role }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目', code: 'test', status: 'active' }];
      if (path.endsWith('/stats/overview')) data = {};
      if (path === '/api/users') data = [{ id: 2, username: 'mock-user', name: '原姓名', email: '', status: 'active', is_platform_admin: false }];
      if (path === '/api/projects/1/members') data = [{ user_id: 2, username: 'mock-user', name: '原姓名', role: 'member' }];
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    for (const path of ['projects', 'users', 'selectors', 'api-env', 'tool-admin', 'fail-clusters', 'rts']) {
      await page.goto(`${base}/${path}`);
      await page.waitForURL('**/dashboard');
      assert.equal(await page.getByRole('menuitem', { name: '系统管理', exact: true }).count(), 0);
    }
    for (const memberRole of ['member', 'guest']) {
      role = memberRole;
      await page.goto(`${base}/projects/1/members`);
      await page.getByText('原姓名', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '添加成员', exact: true }).count(), 0);
      assert.equal(await page.getByRole('button', { name: '改角色', exact: true }).count(), 0);
    }
    role = 'admin';
    await page.goto(`${base}/projects/1/members`);
    await page.getByRole('button', { name: '改角色', exact: true }).click();
    let dialog = page.locator('.el-dialog');
    await dialog.locator('.el-select').click();
    await page.getByRole('option', { name: '嘉宾（只读）', exact: true }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟管理保存失败', { exact: true }).waitFor();
    assert(await dialog.isVisible());
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { role: 'guest' });
    await page.getByRole('button', { name: '移除', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert(!writes.some(write => write.method === 'DELETE'));
    admin = true;
    await page.goto(`${base}/users`);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    dialog = page.locator('.el-dialog');
    await dialog.getByLabel('姓名', { exact: true }).fill('修改姓名');
    failSave = true;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟管理保存失败', { exact: true }).last().waitFor();
    assert(await dialog.isVisible());
    assert.equal(await dialog.getByLabel('姓名', { exact: true }).inputValue(), '修改姓名');
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { name: '修改姓名', email: '', is_platform_admin: false });
    await page.getByRole('button', { name: '重置密码', exact: true }).click();
    dialog = page.locator('.el-dialog');
    await dialog.getByPlaceholder('至少6位').fill('local-test-password');
    failSave = true;
    await dialog.getByRole('button', { name: '确定', exact: true }).click();
    await page.getByText('模拟管理保存失败', { exact: true }).last().waitFor();
    assert(await dialog.isVisible());
    failSave = false;
    await dialog.getByRole('button', { name: '确定', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(writes.at(-1).path, '/api/users/2/password');
    assert.deepEqual(writes.at(-1).body, { password: 'local-test-password' });
    await page.getByRole('button', { name: '禁用', exact: true }).click();
    await page.getByText('已禁用', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1).body, { status: 'disabled' });
    await page.goto(`${base}/projects`);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    dialog = page.locator('.el-dialog');
    failSave = true;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟管理保存失败', { exact: true }).last().waitFor();
    assert(await dialog.isVisible());
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(writes.at(-1).path, '/api/projects/1');
    assert.deepEqual(errors, []);
    console.log('PASS seven admin route guards, member/guest read-only controls, project-admin role edit, failure retention, remove cancellation and admin user/project writes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
