const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const errors = [], payloads = [];
    const reports = [];
    const run = { id: 5, project_id: 1, title: '冒烟用例', status: 'failed', verdict: 'fail',
      findings: [{ title: '按钮无响应', severity: 'major', report_state: 'uncertain' },
        { title: '保存失败', external_ref: 'geelib#731',
          report_receipt: { skill: 'jikuyun-bug-report', priority: 'P1', sub_id: 7087, type_id: 21732 } }] };
    page.on('pageerror', err => errors.push(err.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'local-ui-fixture'));
    await page.route('**/api/**', async route => {
      const req = route.request(), pathname = new URL(req.url()).pathname;
      let data = [];
      if (pathname.endsWith('/auth/me')) data = { user: { id: 1, name: '测试' }, is_platform_admin: true, memberships: [] };
      if (pathname.endsWith('/projects')) data = [{ id: 1, name: 'QA' }];
      if (pathname.endsWith('/devices')) data = [{ id: 1, name: '甲', runner_id: 'mac' }, { id: 2, name: '乙', runner_id: 'win' }];
      if (pathname.endsWith('/md-runs')) data = [run];
      if (pathname.endsWith('/md-runs/5')) data = run;
      if (pathname.endsWith('/md-runs/5/report-bugs')) {
        const payload = req.postDataJSON();
        reports.push(payload);
        let result = { index: 0, ok: false, status: 'review', review_key: 'review-5',
          matches: [{ id: 729, status: '新建', created_at: '2026-11-25 18:56:02', description: '已记录按钮无响应' }] };
        if (payload.receipt_action === 'link') {
          run.findings[0] = { title: '按钮无响应', external_ref: 'geelib#729' };
          result = { index: 0, ok: true, skipped: true, status: 'linked', external_ref: 'geelib#729' };
        }
        data = { reported: 0, results: [result], run };
      }
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
    await page.getByRole('button', { name: '查看', exact: true }).click();
    await page.getByText('极库云提 Bug 技能 · P1').waitFor();
    assert.equal(await page.getByRole('link', { name: 'geelib#731' }).getAttribute('href'),
      'https://geelib.qihoo.net/geelib/project/matterType/21732?subId=7087&matterId=731&typeId=21732');
    await page.getByRole('button', { name: '核对回执' }).click();
    const review = page.getByRole('dialog', { name: '核对极库云回执' });
    await review.getByText('已记录按钮无响应').waitFor();
    await review.getByRole('button', { name: '关联所选工单' }).click();
    await page.getByText('geelib#729', { exact: true }).waitFor();
    assert.deepEqual(reports, [{ indexes: [0], receipt_action: 'check' },
      { indexes: [0], receipt_action: 'link', review_key: 'review-5', matter_id: 729 }]);
    assert.deepEqual(errors, []);
    console.log('PASS dispatch, receipt lookup and explicit link to existing defect');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
