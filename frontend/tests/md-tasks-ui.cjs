const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = [], payloads = [];
    page.on('pageerror', err => errors.push(err.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'local-ui-fixture'));
    await page.route('**/api/**', async route => {
      const req = route.request(), pathname = new URL(req.url()).pathname;
      let data = [];
      if (pathname.endsWith('/auth/me')) data = { user: { id: 1, name: '测试' }, is_platform_admin: true, memberships: [] };
      if (pathname.endsWith('/projects')) data = [{ id: 1, name: 'QA' }];
      if (pathname.endsWith('/devices')) data = [{ id: 1, name: '甲', runner_id: 'mac' }, { id: 2, name: '乙', runner_id: 'win' }];
      if (pathname.endsWith('/md-runs/batch')) {
        const payload = req.postDataJSON();
        payloads.push(payload); data = payload.runners.map(runner => ({ runner }));
      }
      await route.fulfill({ json: { code: 0, data }, status: 200 });
    });
    await page.goto((process.env.UI_BASE_URL || 'http://127.0.0.1:5173') + '/md-tasks');
    const icon = page.getByRole('menuitem', { name: '提测执行', exact: true }).locator('svg');
    await icon.waitFor();
    await page.getByRole('button', { name: '下发提测文档' }).click();
    const dialog = page.getByRole('dialog');
    const formItem = label => dialog.locator('.el-form-item').filter({ has: page.locator('label').getByText(label, { exact: true }) });
    await dialog.locator('input[type=file]').setInputFiles({
      name: '版本提测.md', mimeType: 'text/markdown', buffer: Buffer.from('---\nenv_group: work-test\n---\n# 操作')
    });
    assert.match(await formItem('环境分组').innerText(), /test/);
    await formItem('执行机').locator('.el-select__wrapper').click();
    await page.getByRole('option', { name: /乙/ }).click();
    await page.keyboard.press('Escape');
    await formItem('环境分组').locator('.el-select__wrapper').click();
    const envListId = await formItem('环境分组').getByRole('combobox').getAttribute('aria-controls');
    assert.deepEqual(await page.locator('#' + envListId).getByRole('option').allTextContents(), ['online', 'pre', 'test']);
    await page.getByRole('option', { name: 'pre', exact: true }).click();
    await dialog.getByRole('button', { name: '下发', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(payloads.length, 1);
    assert.deepEqual(payloads[0].runners, ['mac', 'win']);
    assert.equal(payloads[0].env_group, 'pre');
    assert.deepEqual(errors, []);
    console.log('PASS navigation icon, imported legacy group, environment options and two-runner dispatch');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
