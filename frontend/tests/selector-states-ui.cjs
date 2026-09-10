const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRegistry = true, failLearned = true, failSave = true;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      const fail = () => route.fulfill({ status: 500, json: { msg: '模拟选择器失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '项目一' }];
      if (path === '/api/devices') data = [{ id: 1, name: '测试机', runner_id: 'mock-runner' }];
      if (path === '/api/selectors/manage') {
        if (failRegistry) return fail();
        data = { shared: [{ id: 11, key: 'login', page: '登录页', frame: 'auto', platform: 'web', candidates: [{ by: 'text', value: '登录' }] }], by_sub: {} };
      }
      if (path === '/api/selectors/learned') {
        if (failLearned) return fail();
        data = [{ id: 21, key: 'login', candidate: { by: 'text', value: '登录' }, evidence: {}, hit_count: 1 }];
      }
      if (req.method() !== 'GET') {
        writes.push({ path, body: req.postData() ? req.postDataJSON() : null });
        if (path === '/api/selectors/11' && failSave) return fail();
        if (path === '/api/probe') data = { id: 31 };
      }
      if (path === '/api/probe/31') return fail();
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.clock.install();
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/selectors`);
    await page.getByText('注册表加载失败', { exact: true }).waitFor();
    await page.getByRole('tab', { name: '候选评审', exact: true }).click();
    await page.getByText('候选加载失败', { exact: true }).waitFor();
    failLearned = false;
    await page.getByRole('button', { name: '重试候选', exact: true }).click();
    await page.getByRole('button', { name: '转正', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 0);
    await page.getByRole('button', { name: '转正', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认转正', exact: true }).click();
    await page.getByText('已转正', { exact: true }).filter({ visible: true }).last().waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/selectors/learned/21', body: { action: 'approve' } });
    await page.getByRole('button', { name: '拒绝', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认拒绝', exact: true }).click();
    await page.getByText('已拒绝', { exact: true }).filter({ visible: true }).last().waitFor();
    assert.deepEqual(writes.at(-1).body, { action: 'reject' });
    failRegistry = false;
    await page.getByRole('tab', { name: '注册表', exact: true }).click();
    await page.getByRole('button', { name: '重试注册表', exact: true }).click();
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '编辑 key', exact: true });
    await dialog.getByLabel('说明', { exact: true }).fill('修改说明');
    const failed = page.waitForResponse(r => r.url().endsWith('/api/selectors/11') && r.status() === 500);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await failed;
    assert.equal(await dialog.getByLabel('说明', { exact: true }).inputValue(), '修改说明');
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.filter(w => w.path === '/api/selectors/11').at(-1).body, { platform: 'web', frame: 'auto', page: '登录页', desc: '修改说明', candidates: [{ by: 'text', value: '登录' }] });
    await page.getByRole('tab', { name: '设备探测', exact: true }).click();
    await page.locator('.el-select').filter({ hasText: '选择在线设备' }).click();
    await page.getByRole('option', { name: '测试机（mock-runner）', exact: true }).click();
    await page.getByRole('button', { name: '探测(扫当前页)', exact: true }).click();
    await page.waitForResponse(r => r.url().endsWith('/api/probe') && r.request().method() === 'POST');
    await page.clock.runFor(63000);
    await page.getByText('探测超时（60s）：请确认设备 runner 在线且停留在目标页面', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '探测(扫当前页)', exact: true }).isEnabled());
    assert.deepEqual(writes.filter(w => w.path === '/api/probe').at(-1).body, { project_id: 1, sub_product: '', runner: 'mock-runner', params: { contains: '' } });
    assert.deepEqual(errors, []);
    console.log('PASS selector independent read retries, review cancel/approve/reject, edit retry/payload and probe timeout after repeated read failures');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
