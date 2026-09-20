const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    let role = 'member', bound = false, reported = false, completeBody;
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      const account = () => ({ enabled: true, configured: true, bound, account_name: bound ? 'corporate-user' : null });
      if (path === '/api/auth/me') data = { user: { id: 2, name: '测试成员', username: 'member' }, is_platform_admin: false, memberships: [{ project_id: 1, role }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目', code: 'test' }];
      if (path === '/api/issues') data = [{ id: 1, title: '登录失败', severity: 'major', status: 'open', external_ref: reported ? 'geelib#123' : null }];
      if (path === '/api/auth/geelib') {
        if (req.method() === 'DELETE') bound = false;
        data = account();
      }
      if (path.endsWith('/geelib/authorize')) data = { state: 'one-time-state', authorization_url: 'https://sso.example.com/authorize', expires_in: 300 };
      if (path.endsWith('/geelib/complete')) { completeBody = req.postDataJSON(); bound = true; data = account(); }
      if (path.endsWith('/report-geelib')) { assert(bound); reported = true; data = { external_ref: 'geelib#123' }; }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5186';
    await page.goto(`${base}/issues`);
    await page.getByRole('button', { name: '上报极库云', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '标记解决', exact: true }).count(), 0);
    // 未绑定时直接报送应打开绑定窗口，而不是调用报送接口弹配置错误。
    await page.getByRole('button', { name: '上报极库云', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '我的极库云账号' });
    await dialog.getByRole('button', { name: '开始个人授权', exact: true }).click();
    await dialog.getByRole('link', { name: '打开 SSO 授权页面' }).waitFor();
    await dialog.getByPlaceholder('粘贴本次授权码').fill('one-time-code');
    await dialog.getByRole('button', { name: '完成绑定', exact: true }).click();
    await dialog.getByText('已绑定：corporate-user。手动报送及状态同步将使用此账号。', { exact: true }).waitFor();
    assert.deepEqual(completeBody, { state: 'one-time-state', code: 'one-time-code' });
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }));
    assert(!stored.includes('one-time-code'));
    await page.screenshot({ path: '/tmp/geelib-account-ui.png' });
    await dialog.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('button', { name: '上报极库云', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认上报', exact: true }).click();
    await page.getByText('geelib#123', { exact: true }).waitFor();
    await page.getByRole('button', { name: '极库云：corporate-user', exact: true }).click();
    await dialog.getByRole('button', { name: '解除绑定', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认解绑', exact: true }).click();
    await dialog.getByRole('button', { name: '开始个人授权', exact: true }).waitFor();
    assert(!bound);
    role = 'guest'; reported = false;
    await page.goto(`${base}/issues`);
    await page.getByText('登录失败', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '上报极库云', exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS personal binding / member reporting / disconnect / guest restriction');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
