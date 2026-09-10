const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRead = true, failWrite = true;
    const row = { id: 11, title: '测试问题', status: 'open', severity: 'major', external_ref: '' };
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      const fail = () => route.fulfill({ status: 500, json: { msg: '模拟问题操作失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/issues') { if (failRead) return fail(); data = [row]; }
      if (req.method() !== 'GET') {
        const body = req.postData() ? req.postDataJSON() : null;
        writes.push({ path, body });
        if (failWrite) return fail();
        if (body?.status) row.status = body.status;
        if (body?.external_ref) row.external_ref = body.external_ref;
        data = { geelib_sync: { ok: false, msg: '极库云同步待重试' } };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/issues`);
    await page.getByText('遗留问题加载失败', { exact: true }).waitFor();
    failRead = false;
    await page.getByRole('button', { name: '重试问题', exact: true }).click();
    await page.getByRole('button', { name: '上报极库云', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 0);
    await page.getByRole('button', { name: '关联缺陷', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '关联外部缺陷', exact: true });
    await dialog.getByLabel('缺陷链接', { exact: true }).fill('https://example.test/bug');
    const failed = page.waitForResponse(r => r.url().endsWith('/api/issues/11') && r.status() === 500);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await failed;
    assert.equal(await dialog.getByLabel('缺陷链接', { exact: true }).inputValue(), 'https://example.test/bug');
    failWrite = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { external_ref: 'https://example.test/bug' });
    await page.getByRole('button', { name: '标记解决', exact: true }).click();
    await page.getByText('已标记解决，但极库云同步待重试', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1).body, { status: 'resolved' });
    await page.getByRole('button', { name: '重开', exact: true }).click();
    await page.getByText('已重开', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1).body, { status: 'open' });
    assert.deepEqual(errors, []);
    console.log('PASS issue read retry, external report cancellation, failed edit retention, status payloads and preserved partial Geelib sync warning');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
