const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [], uploads = [];
    let failList = true, failDetail = true, failUpload = true;
    const batch = { id: 1, trigger: 'manual', batch_id: 'batch-long-'.repeat(20), case_count: 2, stats: { passed: 1, failed: 1, blocked: 0, pending: 0, running: 0, finished: true, total: 2 } };
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/feedback/runs' || path === '/api/feedback/imports') {
        if (failList) return route.fulfill({ status: 500, json: { msg: '模拟列表失败' } });
        data = path.endsWith('/runs') ? [batch] : [];
      }
      if (path === '/api/feedback/runs/1') {
        if (failDetail) return route.fulfill({ status: 500, json: { msg: '模拟详情失败' } });
        data = { ...batch, items: [{ run_id: 10, title: '可读完整结果', reason: '原因说明\n'.repeat(20) + '原因结尾', status: 'failed', kind: 'gui', evidence_url: '/uploads/evidence.png' }, { run_id: 11, title: '不安全链接', status: 'passed', kind: 'gui', evidence_url: 'javascript:alert(1)' }] };
      }
      if (path === '/api/feedback/ingest') {
        uploads.push({ token: req.headers()['x-bot-token'], body: req.postData() });
        if (failUpload) return route.fulfill({ status: 400, json: { detail: '模拟解析失败' } });
        data = { case_count: 2, script_total: 1 };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/feedback-results`);
    await page.getByText('回归记录加载失败', { exact: true }).waitFor();
    failList = false;
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.getByRole('button', { name: '详情', exact: true }).click();
    await page.getByText('批次详情加载失败', { exact: true }).waitFor();
    failDetail = false;
    await page.getByRole('button', { name: '重新加载详情', exact: true }).click();
    const drawer = page.locator('.el-drawer');
    await drawer.getByText('不安全链接', { exact: true }).waitFor();
    assert.equal(await drawer.locator('a[href^="javascript:"]').count(), 0);
    assert.equal(await drawer.getByRole('link', { name: '证据', exact: true }).getAttribute('href'), '/uploads/evidence.png');
    await drawer.locator('.el-table__expand-icon').first().click();
    await drawer.locator('.full-reason').waitFor();
    assert.match(await drawer.locator('.full-reason').innerText(), /原因结尾/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/feedback-result-mobile.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    failList = true;
    await page.goto(`${base}/feedback-imports`);
    await page.getByText('导入记录加载失败', { exact: true }).waitFor();
    failList = false;
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.getByRole('button', { name: '手动上传 md/zip', exact: true }).click();
    const dialog = page.locator('.el-dialog');
    await dialog.getByPlaceholder('X-Bot-Token').fill('mock-bot-token');
    await dialog.locator('input[type=file]').setInputFiles({ name: 'feedback.md', mimeType: 'text/markdown', buffer: Buffer.from('# local fixture') });
    await dialog.getByRole('button', { name: '上传', exact: true }).click();
    await page.getByText('模拟解析失败', { exact: true }).waitFor();
    assert(await dialog.isVisible());
    assert.equal(await dialog.locator('input[type=file]').evaluate(input => input.files[0].name), 'feedback.md');
    failUpload = false;
    await dialog.getByRole('button', { name: '上传', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(uploads.length, 2);
    assert(uploads.every(upload => upload.token === 'mock-bot-token' && upload.body.includes('filename="feedback.md"') && upload.body.includes('name="source_bot"')));
    assert.deepEqual(errors, []);
    console.log('PASS feedback list/detail failure retries, full reason, safe evidence, mobile drawer, upload failure retention and multipart retry');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
