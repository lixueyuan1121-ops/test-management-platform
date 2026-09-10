const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failCases = true, failCandidates = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      const fail = () => route.fulfill({ status: 500, json: { msg: '模拟需求读取失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/releases') data = { items: [] };
      if (path === '/api/requirements') data = [{ id: 11, title: '测试需求', state: 'notrun', case_count: 1, release_id: null }];
      if (path === '/api/requirements/11/cases') {
        if (req.method() === 'GET' && failCases) return fail();
        data = [{ id: 21, title: '已挂用例', exec_kind: 'gui' }];
      }
      if (path === '/api/ai/cases') {
        if (failCandidates) return fail();
        data = { items: [{ id: 21, title: '已挂用例' }, url.searchParams.get('keyword') ? { id: 23, title: '搜索用例' } : { id: 22, title: '默认用例' }] };
      }
      if (req.method() !== 'GET') { writes.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null }); data = { linked: 2 }; }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/requirements`);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: '编辑需求', exact: true });
    await dialog.getByLabel('标题', { exact: true }).fill('修改需求');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { title: '修改需求', url: null, release_id: 0 });
    await page.getByRole('button', { name: '用例', exact: true }).click();
    await page.getByText('需求用例加载失败', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '挂用例', exact: true }).isDisabled());
    failCases = false;
    await page.getByRole('button', { name: '重试用例', exact: true }).click();
    await page.getByRole('button', { name: '挂用例', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '挂用例到需求', exact: true });
    await dialog.getByText('默认用例', { exact: true }).waitFor();
    assert.equal(await dialog.getByText('已挂用例', { exact: true }).count(), 0);
    await dialog.locator('tbody .el-checkbox').first().click();
    await dialog.getByPlaceholder('按标题搜索(已采纳用例)').fill('搜索');
    await dialog.getByRole('button', { name: '查询', exact: true }).click();
    await dialog.getByText('搜索用例', { exact: true }).waitFor();
    await dialog.locator('tbody .el-checkbox').first().click();
    await dialog.getByRole('button', { name: '挂上选中（2）', exact: true }).waitFor();
    await dialog.getByText('已选（2）', { exact: true }).click();
    await dialog.getByText('默认用例', { exact: true }).waitFor();
    await dialog.getByText('搜索用例', { exact: true }).waitFor();
    await dialog.getByText('候选用例', { exact: true }).click();
    failCandidates = true;
    await dialog.getByRole('button', { name: '查询', exact: true }).click();
    await dialog.getByText('候选加载失败，已选用例已保留', { exact: true }).waitFor();
    assert(await dialog.getByRole('button', { name: '挂上选中（2）', exact: true }).isDisabled());
    failCandidates = false;
    await dialog.getByRole('button', { name: '重试候选', exact: true }).click();
    await dialog.getByRole('button', { name: '挂上选中（2）', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1), { path: '/api/requirements/11/cases', method: 'POST', body: { case_ids: [22, 23] } });
    await page.locator('.el-drawer tbody .el-checkbox').first().click();
    const before = writes.length;
    await page.getByRole('button', { name: '摘除选中（1）', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, before);
    await page.getByRole('button', { name: '摘除选中（1）', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认摘除', exact: true }).click();
    await page.getByText('已摘除', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/requirements/11/cases', method: 'DELETE', body: { case_ids: [21] } });
    assert.deepEqual(errors, []);
    console.log('PASS requirement edit unassign semantics, case retry, exclude linked cases, cross-filter selection, candidate failure retention and link/unlink confirmation payloads');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
