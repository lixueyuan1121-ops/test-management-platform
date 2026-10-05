const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const writes = [], errors = [];
    let row = { id: 1, project_id: 1, title: '前置脚本验证', exec_kind: 'gui', precondition: '进入历史记录', steps: '下载文件', expected: '下载完成', script: JSON.stringify([{ action: 'assert_text', target: { selector: '#download' }, args: { expected: '完成' } }]) };
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'fixture-only'));
    await page.route(u => u.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [{ project_id: 1, role: 'admin' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/ai/cases') data = { items: [row], total: 1 };
      if (path === '/api/ai/testcases/1') {
        if (req.method() === 'PATCH') { const body = req.postDataJSON(); writes.push(body); row = { ...row, ...body, script: JSON.stringify(body.script) }; }
        data = row;
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto((process.env.UI_BASE_URL || 'http://127.0.0.1:5193') + '/case-library?project_id=1');
    await page.getByRole('button', { name: '详情', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '用例详情' });
    await drawer.getByRole('button', { name: '编辑', exact: true }).click();
    await drawer.getByText('将前置操作合入脚本', { exact: true }).click();
    await drawer.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('前置步骤必须是非空数组，并包含确认起始页面就绪的断言', { exact: true }).waitFor();
    assert.equal(writes.length, 0);
    await drawer.getByRole('textbox', { name: '前置步骤 JSON' }).fill(JSON.stringify([{ action: 'click', target: { selector: '#history' } }, { action: 'assert_visible', target: { selector: '#history-ready' } }]));
    await drawer.getByRole('button', { name: '保存', exact: true }).click();
    assert.equal(writes.length, 0, 'explicit confirmation before changing execution semantics');
    await page.getByRole('button', { name: '保存脚本', exact: true }).click();
    await drawer.getByText('脚本已保存，待重新验证', { exact: true }).waitFor();
    assert.equal(writes.length, 1); assert.equal(writes[0].precondition, '');
    assert.equal(writes[0].script.length, 3); assert.match(writes[0].steps, /进入历史记录/);
    assert.deepEqual(errors, []);
    console.log('PASS: setup editor validates readiness assertion, confirms merge, retains requirement and invalidates verification');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
