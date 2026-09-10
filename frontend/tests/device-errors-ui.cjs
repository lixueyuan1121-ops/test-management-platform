const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failBoard = true, failList = true, failWrite = true, boardReads = 0;
    let devices = [{ id: 1, name: '测试设备', runner_id: 'runner-1', token: 'masked', online: true, owner: { name: '测试员' }, active_runs: [], active_kinds: [], run_counts: { running: 0, pending: 2 }, today: { passed: 3, failed: 1 } }];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [{ project_id: 1, role: 'admin' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/devices/overview') {
        boardReads++;
        if (failBoard) return route.fulfill({ status: 500, json: { msg: '模拟看板读取失败' } });
        data = { total_devices: 1, online_devices: 1, running_devices: 0, devices };
      }
      if (path === '/api/devices') {
        if (failList) return route.fulfill({ status: 500, json: { msg: '模拟设备读取失败' } });
        data = devices;
      }
      if (req.method() !== 'GET') {
        writes.push({ path, method: req.method(), body: req.postData() ? req.postDataJSON() : null });
        if (failWrite) return route.fulfill({ status: 500, json: { msg: '模拟设备操作失败' } });
        if (path.endsWith('/reset-token')) data = { token: 'synthetic-new-token' };
        if (req.method() === 'DELETE') devices = [];
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.clock.install();
    await page.goto(`${base}/device-board`);
    await page.getByText('设备数据加载失败', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('.kpi-num').allTextContents(), ['—', '—', '—', '—']);
    assert.equal(await page.locator('.empty').count(), 0);
    assert.equal(await page.locator('.el-message--error').count(), 0);
    failBoard = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.getByText('测试设备', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('.kpi-num').allTextContents(), ['1', '1', '0', '4']);
    const updated = await page.locator('.updated-at').first().innerText();
    failBoard = true;
    await page.clock.runFor(5100);
    await page.getByText('刷新失败，当前显示的是上次获取的数据', { exact: true }).waitFor();
    assert.equal(await page.locator('.updated-at').first().innerText(), updated);
    assert.equal(await page.getByText('测试设备', { exact: true }).count(), 1);
    assert.equal(await page.locator('.el-message--error').count(), 0);
    await page.getByRole('menuitem', { name: '我的设备', exact: true }).click();
    await page.getByText('设备列表加载失败', { exact: true }).waitFor();
    const reads = boardReads;
    await page.clock.runFor(10100);
    assert.equal(boardReads, reads);
    failList = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const dialog = page.locator('.el-dialog').filter({ hasText: '编辑执行设备' });
    assert(await dialog.getByLabel('runner_id', { exact: true }).isDisabled());
    await dialog.getByLabel('设备名', { exact: true }).fill('修改设备名');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟设备操作失败', { exact: true }).waitFor();
    assert(await dialog.isVisible());
    assert.equal(await dialog.getByLabel('设备名', { exact: true }).inputValue(), '修改设备名');
    failWrite = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1), { path: '/api/devices/1', method: 'PATCH', body: { name: '修改设备名', platform: 'web' } });
    for (const action of ['重置 token', '删除']) {
      const count = writes.length;
      await page.getByRole('button', { name: action, exact: true }).click();
      await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
      assert.equal(writes.length, count);
    }
    await page.getByRole('button', { name: '重置 token', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认重置', exact: true }).click();
    await page.locator('.token-text').getByText('synthetic-new-token', { exact: true }).waitFor();
    assert.equal(writes.at(-1).path, '/api/devices/1/reset-token');
    await page.getByRole('button', { name: '我已保存', exact: true }).click();
    await page.getByRole('button', { name: '删除', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认删除', exact: true }).click();
    await page.getByText('已删除', { exact: true }).waitFor();
    assert.equal(writes.at(-1).method, 'DELETE');
    await page.getByText('还没有登记设备,点右上角『注册设备』', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS device first failure/stale data/poll cleanup, list retry, edit failure retention, immutable runner id, reset/delete cancellation and payloads');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
