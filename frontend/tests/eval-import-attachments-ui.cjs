// Mocked APIs: validates attachment selection/import without production writes.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [], imports = [], uploads = [];
    const attachment = { name: '资料.pdf', url: 'https://platform.example/uploads/eval_inputs/1/random.bin' };
    let queries = [], failUpload = false, invalidImport = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/ai/eval-queries')) data = queries;
      if (path.endsWith('/ai/eval-queries/import-attachment')) {
        uploads.push(req.postDataBuffer());
        assert.ok(req.headers()['content-type'].includes('multipart/form-data'));
        assert.equal(new URL(req.url()).searchParams.get('project_id'), '1');
        if (failUpload) return route.fulfill({ status: 400, json: { code: 400, msg: '附件上传失败' } });
        data = attachment;
      }
      if (path.endsWith('/ai/eval-queries/import')) {
        const body = req.postDataJSON();
        imports.push(body);
        assert.deepEqual(body.uploaded_files, { '资料.pdf': attachment.url });
        if (invalidImport) return route.fulfill({ json: { code: 0, data: {
          count: 0, skipped: [{ line: 1, reason: '附件未上传：missing.pdf，整行跳过' }], queries: [],
        } } });
        const row = { id: 1, title: '分析资料', prompt: '总结资料', attachments: [attachment] };
        data = { count: 1, skipped: [], preview: [row] };
        if (!body.dry_run) { queries = [row]; data.queries = queries; }
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5198'}/eval-library`);
    await page.getByRole('button', { name: '导入用例', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '导入对话测评用例' });
    await dialog.getByRole('textbox', { name: '用例模板内容' }).fill('标题,提问prompt,附件\n分析资料,总结资料,资料.pdf');
    const fileInput = dialog.locator('input[type=file]').last();
    const file = { name: '资料.pdf', mimeType: 'application/pdf', buffer: Buffer.from('example attachment') };
    await fileInput.setInputFiles([file]);
    await fileInput.setInputFiles([file]);
    await page.getByText('附件文件名重复：资料.pdf', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.el-upload-list__item').length === 1);
    assert.equal(await dialog.locator('.el-upload-list__item').count(), 1);
    await dialog.getByRole('button', { name: '预览解析', exact: true }).click();
    await dialog.getByText('解析成功：将导入 1 条', { exact: true }).waitFor();
    await dialog.locator('.preview-box').getByText('资料.pdf', { exact: true }).waitFor();
    await page.screenshot({ path: '/tmp/eval-import-attachments-preview.png' });
    await dialog.getByRole('button', { name: '确认导入 1 条', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(uploads.length, 1, 'preview and confirmation reuse uploaded file');
    assert.deepEqual(imports.map(body => body.dry_run), [true, false]);
    await page.locator('.case-title').getByText('分析资料').click();
    await page.getByRole('heading', { name: '附件', exact: true }).waitFor();
    await page.locator('.el-drawer').getByText('资料.pdf', { exact: true }).waitFor();
    await page.locator('.el-drawer__close-btn').click();
    await page.getByRole('button', { name: '导入用例', exact: true }).click();
    assert.equal(await dialog.locator('.el-upload-list__item').count(), 0);
    assert.equal(await dialog.getByRole('textbox', { name: '用例模板内容' }).inputValue(), '');
    failUpload = true;
    await dialog.getByRole('textbox', { name: '用例模板内容' }).fill('标题,提问prompt,附件\n分析资料,总结资料,资料.pdf');
    await fileInput.setInputFiles([file]);
    await dialog.getByRole('button', { name: '确认导入', exact: true }).click();
    await page.getByText('附件上传失败', { exact: true }).waitFor();
    assert.equal(imports.length, 2, 'upload failure must not import a case without its attachments');
    assert.ok(await dialog.isVisible());
    failUpload = false; invalidImport = true;
    await dialog.getByRole('textbox', { name: '用例模板内容' }).fill('标题,提问prompt,附件\n分析资料,总结资料,missing.pdf');
    await dialog.getByRole('button', { name: '确认导入', exact: true }).click();
    await dialog.getByText('第 1 行：附件未上传：missing.pdf，整行跳过', { exact: true }).waitFor();
    assert.ok(await dialog.isVisible(), 'zero valid cases must keep the dialog and show reasons');
    assert.deepEqual(errors, []);
    console.log('PASS: file selection, duplicate rejection, attachment preview, cached upload, import payload, detail, reset, upload failure');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
