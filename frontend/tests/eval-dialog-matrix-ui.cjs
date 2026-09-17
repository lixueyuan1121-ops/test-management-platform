// Offline browser acceptance. Every API is mocked; no model calls or real dispatch.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [], submissions = [];
    const matrix = { chatMode: ['边想边做', '先规划，再执行'], model: ['GLM-5.3', '豆包'], thinkingDepth: ['标准', '高'] };
    const tasks = [
      { id: 18, name: '纳米组合执行', target_engines: ['namiwork'], dialog_options: { chatMode: '边想边做', model: 'GLM-5.3', thinkingDepth: '标准', trial_count: 2 } },
      { id: 19, name: 'WorkBuddy原流程', target_engines: ['workbuddy'], dialog_options: { model: 'WB单模型' } },
      { id: 20, name: '两个产品独立配置', target_engines: ['namiwork', 'workbuddy'], dialog_options: { matrix, model: 'WB专用模型' } },
    ].map(t => ({ ...t, project_id: 1, query_ids: [1, 2, 3], status: 'done', last_batch_id: 'b' }));
    const runs = ['c1', 'c2'].flatMap((cfg, c) => [0, 1].map(turn => ({
      run_id: c * 2 + turn + 1, batch_id: 'b', target_engine: 'namiwork', eval_query_id: turn + 1,
      status: 'judged', verdict: 'pass', payload: { title: '多轮用例', prompt: '测试', conversation_group: `g-${cfg}`, turn_index: turn,
        configuration_id: cfg, configuration_index: c + 1, configuration_label: `模式:边想边做 · 模型:${cfg} · 深度:高` },
    })));
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/devices')) data = [{ runner_id: 'r1', name: '测试机' }];
      if (path.endsWith('/eval-tasks')) data = tasks;
      if (/\/eval-tasks\/\d+\/run$/.test(path)) {
        const body = req.postDataJSON(), task = tasks.find(t => path.includes(`/${t.id}/`));
        submissions.push({ taskId: task.id, body });
        task.dialog_options = { ...body.dialog_options, trial_count: body.trial_count,
          ...(body.dialog_options_matrix ? { matrix: body.dialog_options_matrix } : {}),
          ...(body.dialog_options_b !== null ? { compareB: body.dialog_options_b } : {}) };
        data = { run_ids: [1, 2, 3], batch_id: 'new', runners: ['r1'] };
      }
      if (path.endsWith('/eval-tasks/18/runs')) data = { task: tasks[0], runs, experiment: {
        metrics: { coverage_rate: 100, total: 4, completed: 4 }, manifest: { trial_count: 1 },
        trial_metrics: { tasks: [], by_engine_variant: ['c1', 'c2'].map(c => ({ engine: 'namiwork', configuration_id: c,
          configuration_label: `模式:边想边做 · 模型:${c} · 深度:高`, task_count: 1, success_rate: 100, coverage_rate: 100, mean_score: 5 })) },
      } };
      if (path.endsWith('/eval-tasks/18/batches')) data = { batches: [] };
      if (path.endsWith('/eval-tasks/18/summary-status')) data = { task_id: 18, summary_status: null };
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5197';
    await page.goto(`${base}/eval-tasks`);
    const dialog = page.getByRole('dialog', { name: '执行测评任务' });
    const open = async name => {
      await page.locator('.el-table__body-wrapper .el-table__row').filter({ hasText: name }).getByRole('button', { name: '执行', exact: true }).click();
      await dialog.waitFor();
    };
    const choose = async (label, value) => {
      await dialog.locator('.el-select').filter({ has: page.getByRole('combobox', { name: label }) }).locator('.el-select__wrapper').click();
      await page.getByRole('option', { name: value, exact: true }).click();
      await page.keyboard.press('Escape');
      await page.getByRole('option', { name: value, exact: true }).waitFor({ state: 'hidden' });
    };
    await open('纳米组合执行');
    assert.equal(await dialog.getByRole('textbox', { name: '纳米Work模型' }).inputValue(), 'GLM-5.3');
    await choose('纳米Work对话模式', '先规划，再执行');
    await choose('纳米Work思考深度', '高');
    await dialog.getByRole('textbox', { name: '纳米Work模型' }).fill('GLM-5.3\nglm-5.3，豆包; 测试模型');
    await dialog.getByText(/已识别 3 个模型/).waitFor();
    await dialog.getByText(/将下发 72 条执行/).waitFor();
    await dialog.locator('.el-switch').first().click(); // auto scheduling
    await page.waitForTimeout(300); // allow switch/popover transitions before screenshot
    await page.screenshot({ path: '/tmp/eval-dialog-matrix.png', fullPage: true });
    await dialog.getByRole('button', { name: '下发执行' }).click();
    await dialog.waitFor({ state: 'hidden' });
    const body = submissions.at(-1).body;
    assert.deepEqual(body.dialog_options_matrix, { ...matrix, model: ['GLM-5.3', '豆包', '测试模型'] });
    assert.equal(body.trial_count, 2);
    assert.equal(body.dialog_options, null);
    assert.equal(body.dialog_options_b, null);

    await open('纳米组合执行');
    await dialog.getByText(/将下发 72 条执行/).waitFor();
    assert.equal(await dialog.getByRole('textbox', { name: '纳米Work模型' }).inputValue(), 'GLM-5.3\n豆包\n测试模型');
    await dialog.getByRole('textbox', { name: '纳米Work模型' }).fill(Array.from({ length: 76 }, (_, i) => `M${i}`).join('\n'));
    await dialog.getByText(/单批最多 300 种组合/).waitFor();
    assert(await dialog.getByRole('button', { name: '下发执行' }).isDisabled());
    await dialog.getByRole('textbox', { name: '纳米Work模型' }).fill('x'.repeat(65));
    await dialog.getByText(/每个模型名最多64个字符/).waitFor();
    await dialog.getByRole('textbox', { name: '纳米Work模型' }).fill('GLM-5.3');
    await dialog.locator('.el-switch').filter({ has: page.getByRole('switch', { name: 'A/B 对比' }) }).click();
    assert.equal(await dialog.getByRole('textbox', { name: '纳米Work模型' }).count(), 0);
    await dialog.getByText(/将下发 12 条执行/).waitFor();
    await dialog.getByRole('button', { name: '下发执行' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(submissions.at(-1).body.dialog_options_matrix, null);
    assert.deepEqual(submissions.at(-1).body.dialog_options_b, {});
    assert.equal(submissions.at(-1).body.dialog_options.chatMode, '边想边做');

    await open('WorkBuddy原流程');
    assert.equal(await dialog.getByRole('textbox', { name: '纳米Work模型' }).count(), 0);
    await dialog.getByText(/将下发 3 条执行/).waitFor();
    await dialog.getByRole('button', { name: '下发执行' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(submissions.at(-1).body.dialog_options, { model: 'WB单模型' });
    assert.equal(submissions.at(-1).body.dialog_options_matrix, null);

    await open('两个产品独立配置');
    await dialog.getByText(/将下发 27 条执行/).waitFor();
    assert.equal(await dialog.getByRole('textbox', { name: 'WorkBuddy模型' }).inputValue(), 'WB专用模型');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400); // responsive layout and dialog opening transition
    const box = await dialog.boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 391, 'mobile dialog must fit viewport');
    await page.screenshot({ path: '/tmp/eval-dialog-matrix-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await dialog.getByRole('button', { name: '下发执行' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(submissions.at(-1).body.dialog_options_matrix, matrix);
    assert.deepEqual(submissions.at(-1).body.dialog_options, { model: 'WB专用模型' });

    await page.getByRole('button', { name: '纳米组合执行', exact: true }).click();
    await page.getByText('配置组合', { exact: true }).waitFor();
    assert.equal(await page.locator('.d-table .el-table__body-wrapper').getByText('多轮 ×2', { exact: true }).count(), 2);
    await page.waitForTimeout(400);
    await page.screenshot({ path: '/tmp/eval-dialog-matrix-results.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS: multiple mode/depth, manual models, Cartesian count, restore, validation, A/B, WorkBuddy isolation, mobile, grouped results');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
