const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, timezoneId: process.env.UI_TIMEZONE || 'America/Los_Angeles' });
    const errors = [], payloads = [];
    const reports = [];
    const retests = [];
    let executorQueries = 0;
    const logFile = { id: 2, name: '客户端.log', kind: 'log', size: 100, case: 'C-002' };
    const run = { evidence: [logFile], evidence_summary: { expected: 1, notes: [] }, id: 5, project_id: 1, title: '冒烟用例', status: 'failed', verdict: 'fail',
      runner: 'win', env_group: 'test', client_version: '1.0',
      created_at: '2026-11-26T02:00:00', started_at: '2026-11-26T15:59:00', finished_at: '2026-11-26T16:00:01.123456Z',
      findings: [{ title: '按钮无响应', severity: 'major', report_state: 'uncertain' },
        { title: '保存失败', external_ref: 'geelib#731', evidence_target: { sub_id: 7087, matter_id: 731 },
          evidence: [{ ...logFile, state: 'failed', message: '连接失败' }],
          report_receipt: { skill: 'jikuyun-bug-report', priority: 'P1', sub_id: 7087, type_id: 21732 } },
        { title: '刷新失败', severity: 'minor' }, { title: '输入中断', severity: 'minor' }] };
    page.on('pageerror', err => errors.push(err.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'local-ui-fixture'));
    await page.route('**/api/**', async route => {
      const req = route.request(), pathname = new URL(req.url()).pathname;
      let data = [];
      if (pathname.endsWith('/auth/me')) data = { user: { id: 1, name: '测试' }, is_platform_admin: true, memberships: [] };
      if (pathname.endsWith('/projects')) data = [{ id: 1, name: 'QA' }];
      if (pathname.endsWith('/devices')) data = [{ id: 1, name: '甲', runner_id: 'mac' }, { id: 2, name: '乙', runner_id: 'win' }];
      if (pathname.endsWith('/md-runs')) data = [run,
        { id: 6, title: '运行中的提测', status: 'running', started_at: '2026-11-27T02:32:52', created_at: '2026-11-26T18:00:00' },
        { id: 7, title: '待执行的提测', status: 'pending', created_at: '2026-11-27T10:01:02+08:00' },
        { id: 8, title: '缺少时间的提测', status: 'pending' }];
      if (pathname.endsWith('/md-runs/5')) data = run;
      if (pathname.endsWith('/md-runs/5/retests')) {
        retests.push(req.postDataJSON());
        run.findings[1].retests = [{ id: 12, status: 'pending', client_version: '2.0' }];
        data = { run_id: 12, created: true };
      }
      if (pathname.endsWith('/md-runs/12')) {
        run.findings[1].retests[0].status = 'failed';
        data = { ...run, id: 12, title: '修复验证 geelib#731', client_version: '2.0',
          retest: { source_run_id: 5, external_ref: 'geelib#731' }, findings: [{ title: '仍未修复' }] };
      }
      if (pathname.endsWith('/md-runs/5/report-executors')) {
        if (++executorQueries === 1) return route.fulfill({ status: 502, json: { msg: '无法读取执行人列表' } });
        data = { default_email: 'qa@example.test', members: [
          { name: '测试', email: 'qa@example.test' }, { name: '张华', email: 'dev@example.test' },
          { name: '张华', email: 'dev2@example.test' }] };
      }
      if (pathname.endsWith('/md-runs/5/evidence/2')) {
        assert.match(req.headers().authorization, /^Bearer /);
        return route.fulfill({ contentType: 'application/octet-stream', body: 'client log' });
      }
      if (pathname.endsWith('/md-runs/5/report-bugs')) {
        const payload = req.postDataJSON();
        reports.push(payload);
        if (!payload.receipt_action && payload.indexes) {
          run.findings[3].external_ref = 'geelib#891';
          run.findings[3].report_receipt = { assignee: payload.executor_email };
          return route.fulfill({ json: { code: 0, data: { results: [{ ok: true, index: 3 }], reported: 1, run } } });
        }
        if (!payload.receipt_action) {
          run.findings[2].external_ref = 'geelib#890';
          return route.fulfill({ status: 503, json: { code: 503, msg: '平台数据库连接中断', data: null } });
        }
        let result = { index: 0, ok: false, status: 'review', review_key: 'review-5',
          matches: [{ id: 729, status: '新建', created_at: '2026-11-25 18:56:02', description: '已记录按钮无响应' }] };
        if (payload.receipt_action === 'link') {
          run.findings[0] = { title: '按钮无响应', external_ref: 'geelib#729' };
          result = { index: 0, ok: true, skipped: true, status: 'linked', external_ref: 'geelib#729' };
        }
        if (payload.receipt_action === 'attachments') {
          run.findings[1].evidence[0].state = 'sent';
          result = { index: 1, ok: true, skipped: true, attachment_count: 1, attachments_sent: 1 };
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
    const taskRow = title => page.getByRole('row').filter({ has: page.getByRole('cell', { name: title, exact: true }) });
    for (const [title, time] of [['运行中的提测', '2026/11/27 10:32:52'], ['冒烟用例', '2026/11/27 00:00:01'],
      ['待执行的提测', '2026/11/27 10:01:02']]) {
      await taskRow(title).getByRole('cell', { name: time, exact: true }).waitFor({ timeout: 4000 });
    }
    const timeColumn = await page.getByRole('columnheader').allTextContents();
    const timeIndex = timeColumn.findIndex(text => text.includes('北京时间'));
    assert.notEqual(timeIndex, -1);
    assert.equal((await taskRow('缺少时间的提测').getByRole('cell').nth(timeIndex).innerText()).trim(), '—');
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
    await taskRow('冒烟用例').getByRole('button', { name: '查看', exact: true }).click();
    await page.getByText('极库云提 Bug 技能 · P1').waitFor();
    assert.equal(await page.getByRole('link', { name: 'geelib#731' }).getAttribute('href'),
      'https://geelib.qihoo.net/geelib/project/matterType/21732?subId=7087&matterId=731&typeId=21732');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '客户端.log', exact: true }).first().click();
    assert.equal((await download).suggestedFilename(), '客户端.log');
    await page.getByRole('button', { name: '补传附件' }).click();
    await page.getByText('已附到工单', { exact: true }).waitFor();
    await page.getByRole('button', { name: '核对回执' }).click();
    const review = page.getByRole('dialog', { name: '核对极库云回执' });
    await review.getByText('已记录按钮无响应').waitFor();
    await review.getByRole('button', { name: '重新建单' }).click();
    const retry = page.getByRole('dialog', { name: '确认重新建单' });
    await retry.getByText('无法读取执行人列表').waitFor();
    assert.equal(await retry.getByRole('button', { name: '确认上报' }).isEnabled(), false);
    await retry.getByRole('button', { name: '重新加载' }).click();
    await retry.getByText('测试（qa@example.test）').waitFor();
    await retry.getByRole('button', { name: '取消' }).click();
    await review.getByRole('button', { name: '关联所选工单' }).click();
    await page.getByText('geelib#729', { exact: true }).waitFor();
    const assign = page.getByRole('dialog', { name: '确认上报', exact: true });
    await taskRow('输入中断').getByRole('button', { name: '上报缺陷' }).click();
    await assign.getByRole('combobox').fill('张华');
    await page.getByRole('option', { name: '张华（dev2@example.test）', exact: true }).click();
    await assign.getByRole('button', { name: '确认上报' }).click();
    await page.getByText('执行人 dev2@example.test', { exact: true }).waitFor();
    await page.getByRole('button', { name: '上报未报的到极库云' }).click();
    await assign.getByRole('combobox').fill('dev@');
    await page.getByRole('option', { name: '张华（dev@example.test）', exact: true }).click();
    await assign.getByRole('button', { name: '确认上报' }).click();
    await page.getByText('geelib#890', { exact: true }).waitFor();
    await page.getByText('上报中断，已刷新工单状态。回执待确认的缺陷请核对回执。', { exact: true }).waitFor();
    assert.equal(await page.getByText(/页面异常/).count(), 0);
    assert.deepEqual(reports, [{ indexes: [1], receipt_action: 'attachments' }, { indexes: [0], receipt_action: 'check' },
      { indexes: [0], receipt_action: 'link', review_key: 'review-5', matter_id: 729 },
      { indexes: [3], executor_email: 'dev2@example.test' }, { executor_email: 'dev@example.test' }]);
    const finding = taskRow('保存失败');
    await finding.getByRole('button', { name: '下发复验' }).click();
    const retest = page.getByRole('dialog', { name: '下发缺陷复验' });
    await retest.getByText('geelib#731 · 保存失败').waitFor();
    await retest.getByText('执行机 win · 环境 test').waitFor();
    await retest.getByLabel('修复版本').fill('2.0');
    await retest.getByRole('button', { name: '确认下发' }).click();
    await finding.getByRole('button', { name: '复验 #12 · 待执行' }).waitFor();
    assert.equal(await finding.getByRole('button', { name: '下发复验' }).isEnabled(), false);
    await finding.getByRole('button', { name: '复验 #12 · 待执行' }).click();
    await page.getByText('复验工单 geelib#731').waitFor();
    assert.equal(await taskRow('仍未修复').getByRole('button', { name: '上报缺陷' }).count(), 0);
    await page.getByRole('button', { name: '原提测任务 #5' }).click();
    await finding.getByRole('button', { name: '复验 #12 · 失败' }).waitFor();
    assert.equal(await finding.getByRole('button', { name: '下发复验' }).isEnabled(), true);
    assert.deepEqual(retests, [{ external_ref: 'geelib#731', previous_run_id: 0, client_version: '2.0' }]);
    assert.deepEqual(errors, []);
    console.log('PASS dispatch, defect reporting, retest dispatch and history');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
