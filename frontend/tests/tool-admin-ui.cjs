const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failCats = true, failTools = true, failWrite = true;
    const tool = { id: 2, category_id: 1, category_name: '测试分类', name: '测试工具', status: 'online', sort_order: 0, version: '1.0' };
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      const fail = () => route.fulfill({ status: 500, json: { msg: '模拟工具操作失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/tools/categories') { if (failCats) return fail(); data = [{ id: 1, name: '测试分类', sort_order: 0, is_active: true }]; }
      if (path === '/api/tools') { if (failTools) return fail(); data = [tool]; }
      if (req.method() !== 'GET') {
        writes.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null });
        if (failWrite) return fail();
        if (path.endsWith('/toggle')) tool.status = tool.status === 'online' ? 'offline' : 'online';
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/tool-admin`);
    await page.getByText('工具加载失败', { exact: true }).waitFor();
    failTools = false;
    await page.getByRole('button', { name: '重试工具', exact: true }).click();
    await page.getByText('测试工具', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '新建工具', exact: true }).isDisabled());
    failCats = false;
    await page.getByRole('button', { name: '重试分类', exact: true }).filter({ visible: true }).click();
    await page.getByRole('button', { name: '编辑', exact: true }).filter({ visible: true }).click();
    let dialog = page.locator('.el-dialog');
    await dialog.getByLabel('工具名称', { exact: true }).fill('修改工具名称');
    const failedSave = page.waitForResponse(r => r.url().endsWith('/api/tools/2') && r.request().method() === 'PATCH' && r.status() === 500);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await failedSave;
    assert.equal(await dialog.getByLabel('工具名称', { exact: true }).inputValue(), '修改工具名称');
    failWrite = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { category_id: 1, name: '修改工具名称', description: '', download_url: '', doc_url: '', icon: '', version: '1.0', sort_order: 0 });
    await page.getByRole('button', { name: '下线', exact: true }).click();
    await page.getByRole('button', { name: '上线', exact: true }).waitFor();
    assert.equal(writes.at(-1).path, '/api/tools/2/toggle');
    const count = writes.length;
    await page.getByRole('button', { name: '删除', exact: true }).filter({ visible: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, count);
    await page.getByRole('button', { name: '删除', exact: true }).filter({ visible: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认删除', exact: true }).click();
    await page.getByText('已删除', { exact: true }).waitFor();
    assert.equal(writes.at(-1).path, '/api/tools/2');
    await page.getByRole('tab', { name: '分类管理', exact: true }).click();
    await page.getByRole('button', { name: '编辑', exact: true }).filter({ visible: true }).click();
    dialog = page.locator('.el-dialog');
    await dialog.getByLabel('名称', { exact: true }).fill('修改分类名称');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { name: '修改分类名称', sort_order: 0, is_active: true });
    const before = writes.length;
    await page.getByRole('button', { name: '删除', exact: true }).filter({ visible: true }).click();
    await page.locator('.el-message-box').getByText(/其下工具也会删除/).waitFor();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, before);
    await page.getByRole('button', { name: '删除', exact: true }).filter({ visible: true }).click();
    const deletedCategory = page.waitForResponse(r => r.url().endsWith('/api/tools/categories/1') && r.request().method() === 'DELETE');
    await page.locator('.el-message-box').getByRole('button', { name: '确认删除', exact: true }).click();
    await deletedCategory;
    assert.equal(writes.at(-1).path, '/api/tools/categories/1');
    await page.getByRole('tab', { name: '工具列表', exact: true }).click();
    await page.getByRole('button', { name: '新建工具', exact: true }).click();
    dialog = page.locator('.el-dialog');
    await dialog.locator('.el-select').click();
    await page.getByRole('option', { name: '测试分类', exact: true }).filter({ visible: true }).click();
    await dialog.getByLabel('工具名称', { exact: true }).fill('新工具');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(writes.at(-1).path, '/api/tools');
    assert.equal(writes.at(-1).method, 'POST');
    assert.equal(writes.at(-1).body.category_id, 1);
    assert.equal(writes.at(-1).body.name, '新工具');
    assert.deepEqual(errors, []);
    console.log('PASS tool/category read retries, failed edit retention and payload, toggle, delete confirmation/cancellation and category edit');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
