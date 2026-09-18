// All APIs are mocked. No Multica tasks, model requests or actual evaluations are created.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [], reads = [];
    let guest = false, failSubmit = false;
    const source = { run_id: 10, batch_id: 'original', target_engine: 'namiwork', status: 'judged', verdict: 'fail', score: 2,
      multica_ref: 'old-id', multica_pushed_at: null, verdict_reason: '原结果缺少汇总', answer: '原始回答',
      payload: { title: '库存汇总', prompt: '汇总原始库存文件', expected: '输出汇总', dialog_options: { model: 'GLM-5.3', chatMode: '边想边做', thinkingDepth: '高' } } };
    const second = { ...source, run_id: 20, target_engine: 'workbuddy', payload: { ...source.payload, title: '其他产品反馈', dialog_options: { model: 'Claude' } }, multica_pushed_at: '2026-09-18T15:30:00' };
    const retests = [];
    const task = { id: 99, name: 'Multica 复测测试', project_id: 1, query_ids: [1], target_engines: ['qwork'], status: 'done',
      last_batch_id: 'newer', summary_batch_id: 'retest-batch', description: '测试任务' };
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error' && m.text().includes('[全局错误]')) errors.push(m.text()); });
    page.setDefaultTimeout(10000);
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      let data = [];
      if (request.method() !== 'GET') writes.push({ path, body: request.postDataJSON() });
      else reads.push(url);
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: !guest, memberships: [{ project_id: 1, role: 'guest' }] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测评项目' }, { id: 2, name: '另一个项目' }];
      if (path.endsWith('/multica-results')) {
        const rows = url.searchParams.get('project_id') === '2' ? [] : [source, second].filter(r => !url.searchParams.get('search') || r.payload.title.includes(url.searchParams.get('search')));
        data = { items: rows.map(r => ({ ...r, latest_retest: retests.at(-1), retest_count: retests.length })), total: rows.length };
      }
      if (/\/multica-results\/\d+\/retests$/.test(path)) data = { source, retests };
      if (path.endsWith('/multica-retest')) {
        if (failSubmit) return route.fulfill({ status: 409, json: { msg: '所选反馈已有复测正在进行，请先查看复测记录' } });
        const body = request.postDataJSON();
        const row = { ...source, run_id: 100 + retests.length, eval_task_id: 99, batch_id: 'retest-batch', target_engine: body.target_engines?.[0] || source.target_engine,
          payload: { ...source.payload, dialog_options: body.model != null ? { model: body.model } : source.payload.dialog_options },
          created_at: '2026-09-18T16:00:00', status: 'judged', verdict: 'pass', score: 5, verdict_reason: '复测汇总完整' };
        retests.push(row);
        data = { task_id: 99, batch_id: 'retest-batch', run_ids: [row.run_id], source_count: 1, context_count: 0 };
      }
      if (path.endsWith('/eval-tasks')) data = [task];
      if (path.endsWith('/eval-tasks/99/runs')) data = { task, runs: retests };
      if (path.endsWith('/eval-tasks/99/batches')) data = { batches: [{ batch_id: 'retest-batch', total: 1, passed: 1, t0: '2026-09-18T16:00:00', is_current: false }] };
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5188';
    const openPage = async () => { await page.goto(`${base}/eval-multica?project_id=1`); await page.getByText('库存汇总', { exact: true }).waitFor(); };
    const selectFirst = async () => { await page.locator('.multica-page .el-table__body-wrapper .el-checkbox').first().click(); };
    const openModal = async () => { await selectFirst(); await page.getByRole('button', { name: '复测所选（1）', exact: true }).click(); };
    const dialog = page.getByRole('dialog', { name: '发起 Multica 复测' });
    await openPage();
    await page.getByText('历史推送', { exact: true }).waitFor();
    await page.getByText('2026-09-18 15:30:00', { exact: true }).waitFor();
    await page.waitForTimeout(350);
    await page.getByText('不通过', { exact: true }).first().waitFor();
    await page.screenshot({ path: '/tmp/multica-list.png', fullPage: true });
    await openModal();
    assert.equal(await dialog.getByRole('textbox', { name: '复测模型', exact: true }).inputValue(), 'GLM-5.3');
    assert(await dialog.getByRole('textbox', { name: '复测模型', exact: true }).isDisabled());
    assert(await dialog.getByText('纳米Work', { exact: true }).isVisible());
    await dialog.getByRole('button', { name: '创建并执行复测', exact: true }).click();
    await page.getByText('复测汇总完整', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1).body, { project_id: 1, run_ids: [10], target_engines: null, model: null });
    await page.getByText('原结果缺少汇总', { exact: true }).waitFor();
    await page.getByRole('button', { name: '刷新复测记录', exact: true }).click();
    await page.getByText('复测汇总完整', { exact: true }).waitFor();
    await page.screenshot({ path: '/tmp/multica-history.png', fullPage: true });
    await page.getByRole('button', { name: '查看任务 / 判定', exact: true }).click();
    await page.waitForURL('**/eval-tasks?**');
    await page.getByRole('button', { name: task.name, exact: true }).waitFor();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('dialog').getByText('复测汇总完整', { exact: true }).waitFor();
    assert(reads.some(u => u.pathname.endsWith('/eval-tasks/99/runs') && u.searchParams.get('batch_id') === 'retest-batch'));
    assert.equal(new URL(page.url()).searchParams.get('task_id'), '99');
    await openPage();
    await openModal();
    await dialog.getByText('修改产品（未勾选时沿用各条原产品）', { exact: true }).click();
    await dialog.locator('.el-select__wrapper').click();
    await page.getByRole('option', { name: '纳米Work', exact: true }).click();
    assert(await dialog.getByRole('button', { name: '创建并执行复测', exact: true }).isDisabled());
    await page.getByRole('option', { name: 'QWork', exact: true }).click();
    await dialog.getByText('发起 Multica 复测', { exact: true }).click();
    await dialog.getByText('修改模型（未勾选时沿用各条原模型）', { exact: true }).click();
    await dialog.getByRole('textbox', { name: '复测模型', exact: true }).fill('DeepSeek-V4');
    await page.screenshot({ path: '/tmp/multica-options.png', fullPage: true });
    // Server conflict must preserve the edited settings for correction, not silently enqueue another task.
    failSubmit = true;
    await dialog.getByRole('button', { name: '创建并执行复测', exact: true }).click();
    await page.getByText('所选反馈已有复测正在进行，请先查看复测记录', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('textbox', { name: '复测模型', exact: true }).inputValue(), 'DeepSeek-V4');
    failSubmit = false;
    await dialog.getByRole('button', { name: '创建并执行复测', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { project_id: 1, run_ids: [10], target_engines: ['qwork'], model: 'DeepSeek-V4' });
    await openPage();
    await page.getByRole('textbox', { name: '搜索已推送结果' }).fill('库存');
    await page.getByRole('button', { name: '搜索', exact: true }).click();
    await page.getByText('其他产品反馈', { exact: true }).waitFor({ state: 'hidden' });
    assert(reads.some(u => u.searchParams.get('search') === '库存' && u.searchParams.get('page') === '1'));
    await selectFirst();
    await page.locator('.page-head .el-select__wrapper').click();
    await page.getByRole('option', { name: '另一个项目', exact: true }).click();
    await page.getByText('暂无已推送 Multica 的测评结果', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '复测所选（0）', exact: true }).isDisabled());
    await page.setViewportSize({ width: 390, height: 844 });
    await openPage();
    await page.waitForTimeout(400);
    assert(await page.locator('.multica-page').evaluate(n => n.scrollWidth <= n.clientWidth + 1));
    await page.screenshot({ path: '/tmp/multica-mobile.png', fullPage: true });
    guest = true;
    await openPage();
    assert(await page.locator('.multica-page .el-table__body-wrapper input[type=checkbox]').first().isDisabled());
    assert(await page.getByRole('button', { name: '复测所选（0）', exact: true }).isDisabled());
    assert.deepEqual(errors, []);
    console.log('PASS Multica collection, original defaults, editable products/model, conflict recovery, history comparison, exact task/batch link, search, project isolation, guest permissions and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
