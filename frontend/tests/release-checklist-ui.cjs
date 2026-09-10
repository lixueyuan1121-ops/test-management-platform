const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRead = false, failRemove = true, holdOld = false, heldRoute;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '项目一' }, { id: 2, name: '项目二' }];
      if (path === '/api/releases') data = { items: [{ id: 11, version: 'v1', release_date: '2026-09-10' }] };
      if (path === '/api/release-checklist') {
        if (holdOld && url.searchParams.get('project_id') === '1') { heldRoute = route; return; }
        if (failRead) return route.fulfill({ status: 500, json: { msg: '模拟读取失败' } });
        data = [{ test_case_id: 21, title: `项目${url.searchParams.get('project_id')}自动用例`, exec_kind: 'gui', priority: 'P1' }, { test_case_id: 22, title: '人工用例', exec_kind: 'manual', priority: 'P2' }];
      }
      if (req.method() === 'POST') {
        writes.push({ path, project: url.searchParams.get('project_id'), body: req.postDataJSON() });
        if (path.endsWith('/remove') && failRemove) return route.fulfill({ status: 500, json: { msg: '模拟移出失败' } });
        data = path.endsWith('/remove') ? { removed: 2 } : { run_ids: [51] };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/release-checklist`);
    await page.getByText('项目1自动用例', { exact: true }).waitFor();
    await page.locator('thead .el-checkbox').click();
    await page.locator('.dispatch-bar .el-select').first().click();
    await page.getByRole('option', { name: '自动调度(按平台挑在线空闲设备)', exact: true }).click();
    await page.locator('.dispatch-bar .el-select').last().click();
    await page.getByRole('option', { name: 'v1（2026-09-10）', exact: true }).click();
    await page.getByRole('button', { name: '执行', exact: true }).click();
    await page.locator('.el-message-box').getByText(/跳过 1 条 manual/).waitFor();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 0);
    await page.getByRole('button', { name: '执行', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认执行', exact: true }).click();
    await page.getByText(/已下发 1 条到 auto/).waitFor();
    assert.deepEqual(writes.at(-1).body, { project_id: 1, runner: 'auto', test_case_ids: [21], release_id: 11 });
    await page.getByRole('button', { name: '移出清单', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 1);
    await page.getByRole('button', { name: '移出清单', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认移出', exact: true }).click();
    await page.getByText('模拟移出失败', { exact: true }).waitFor();
    await page.getByText('已选 2 条', { exact: true }).waitFor();
    failRemove = false;
    await page.getByRole('button', { name: '移出清单', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认移出', exact: true }).click();
    await page.getByText('已移出 2 条(不影响回归用例)', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/release-checklist/remove', project: '1', body: { test_case_ids: [21, 22] } });
    async function selectProject(name) {
      await page.locator('.el-select').first().click();
      await page.getByRole('option', { name, exact: true }).click();
    }
    failRead = true;
    await selectProject('项目二');
    await page.getByText('上线清单加载失败', { exact: true }).waitFor();
    assert.equal(await page.getByText('项目1自动用例', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '执行', exact: true }).count(), 0);
    failRead = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.getByText('项目2自动用例', { exact: true }).waitFor();
    holdOld = true;
    await selectProject('项目一');
    await page.locator('.el-loading-mask').waitFor();
    await selectProject('项目二');
    await page.getByText('项目2自动用例', { exact: true }).waitFor();
    assert(heldRoute);
    await heldRoute.fulfill({ json: { code: 0, data: [{ test_case_id: 99, title: '迟到旧项目用例' }] } });
    await page.waitForTimeout(250);
    assert.equal(await page.getByText('迟到旧项目用例', { exact: true }).count(), 0);
    await page.locator('tbody tr').filter({ hasText: '人工用例' }).locator('.el-checkbox').click();
    await page.getByRole('button', { name: '执行', exact: true }).click();
    await page.getByText('选中项里没有可执行的用例(manual 不可自动化)', { exact: true }).waitFor();
    assert.equal(writes.length, 3);
    assert.deepEqual(errors, []);
    console.log('PASS checklist execution/manual skip/release payload, confirm cancellation, remove failure retry, project isolation and read retry');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
