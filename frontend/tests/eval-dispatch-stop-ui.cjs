// Local mocked API: never dispatches or cancels a production task.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [], dispatches = [], stops = [];
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5198';
    let guest = false;
    const runs = [1, 2].map(id => ({ run_id: id, project_id: 1, eval_query_id: id,
      status: id === 1 ? 'done' : 'running', batch_id: 'old-batch', runner: 'test-win',
      target_engine: 'namiwork', payload: { title: `多轮${id}`, prompt: 'hello', expected: 'ok',
        conversation_group: 'group', turn_index: id - 1 } }));
    runs.push({ run_id: 3, project_id: 1, status: 'running', batch_id: 'another-batch',
      target_engine: 'namiwork', payload: { title: '独立对话', prompt: 'hello' } });
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: !guest,
        memberships: [{ project_id: 1, role: guest ? 'guest' : 'admin' }] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/devices')) data = [{ id: 1, name: 'Windows', runner_id: 'test-win' }];
      if (path.endsWith('/ai/eval-queries')) data = [{ id: 1, title: '测试用例', prompt: 'hello', expected: 'ok' }];
      if (path.endsWith('/eval-queue/enqueue')) {
        dispatches.push(req.postDataJSON());
        data = { eval_task_id: 88, task_name: dispatches[0].task_name, batch_id: 'new-batch', run_ids: [4] };
      }
      if (path.endsWith('/eval-queue/history')) data = runs;
      if (path.endsWith('/dimension-stats')) data = { dims: [], judged_total: 0, overall_rate: 0 };
      if (/\/eval-queue\/\d+\/stop$/.test(path)) {
        const id = Number(path.split('/').at(-2));
        stops.push(id);
        runs.find(r => r.run_id === id).status = 'cancelled';
        data = { cancelled_count: 1, run_ids: [id], runs: runs.filter(r => r.run_id !== 3 || id === 3) };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${base}/eval-library`);
    await page.locator('.el-table__body-wrapper .el-checkbox').first().click();
    await page.getByRole('button', { name: '配置并下发', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '下发测评用例' });
    await dialog.getByRole('textbox', { name: '测评任务名称' }).fill('');
    assert.equal(await dialog.getByRole('button', { name: '确认下发' }).isEnabled(), false);
    await dialog.getByRole('textbox', { name: '测评任务名称' }).fill('本次回归测评');
    await dialog.getByRole('button', { name: '确认下发' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(dispatches.length, 1);
    assert.equal(dispatches[0].task_name, '本次回归测评');
    assert.deepEqual(dispatches[0].eval_query_ids, [1]);
    await page.getByText(/已创建测评任务.*本次回归测评/).waitFor();

    await page.goto(`${base}/eval-results`);
    await page.waitForTimeout(700);
    if (errors.length) throw new Error(errors.join('\n'));
    const stop = page.getByRole('button', { name: '停止', exact: true });
    await stop.first().waitFor();
    await stop.first().click();
    await page.getByRole('button', { name: '继续执行', exact: true }).click();
    assert.equal(stops.length, 0);
    await stop.first().click();
    await page.locator('.el-message-box').getByRole('button', { name: '停止', exact: true }).click();
    await page.getByText('已停止 1 条未完成执行', { exact: true }).waitFor();
    assert.deepEqual(stops, [2], 'group stop targets the active turn, not the completed first turn');
    assert.equal(runs[0].status, 'done');
    assert.equal(runs[2].status, 'running');
    await page.getByText('已停止', { exact: true }).first().waitFor();
    await page.locator('.el-message-box').waitFor({ state: 'hidden' });
    await page.screenshot({ path: '/tmp/eval-dispatch-stop-results.png' });
    guest = true;
    await page.reload();
    await page.getByText('独立对话', { exact: true }).waitFor();
    assert.equal(await stop.count(), 0, 'guest must not see stop actions');
    assert.deepEqual(errors, []);
    console.log('PASS: task creation payload/name validation, stop confirmation/cancel, multi-turn target, stopped state, guest permissions');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
