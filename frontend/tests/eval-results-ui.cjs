// Run against a local Vite server; all API requests are intercepted.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    const writes = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    const rows = [
      { run_id: 101, eval_query_id: 1, status: 'judged', verdict: 'pass', trace: '/uploads/test-trace.json', verdict_reason: '已完成文件分析', reported_duration: 65, verdict_dims: { tools_ok: { pass: null, note: '缺少完整过程证据', evidence_quote: '返回了文件摘要' } }, payload: { title: '文件分析', prompt: '分析上传的文件', expected: '输出文件摘要', conversation_group: 'g1', turn_index: 0 } },
      { run_id: 102, eval_query_id: 2, status: 'judged', verdict: 'fail', is_abnormal: true, payload: { title: '补充分析', prompt: '补充对比结果', conversation_group: 'g1', turn_index: 1 } },
      { run_id: 103, eval_query_id: 3, status: 'done', payload: { title: '生成报告', prompt: '生成测试报告' } },
      { run_id: 104, eval_query_id: 4, status: 'failed', trace: 'https://untrusted.invalid/uploads/data.json', reason: '测试错误', payload: { title: '失败用例' } },
      { run_id: 105, eval_query_id: 5, status: 'judged', verdict: 'pass', trace: '/uploads/missing-trace.json', pushed_multica: true, payload: { title: '已推送用例' } },
    ].map(row => ({ batch_id: 'batch-1', target_engine: 'workbuddy', answer: '测试回答', ...row }));
    let traceRequests = 0;
    await page.route('**/uploads/missing-trace.json', route => route.fulfill({ status: 404, body: 'not found' }));
    let unsafeRequests = 0;
    await page.route('https://untrusted.invalid/**', route => { unsafeRequests++; return route.abort(); });
    await page.route('**/uploads/test-trace.json', async route => {
      traceRequests++;
      await route.fulfill({ json: { thinking: '核对列名和统计范围', tool_calls: [{ name: 'read_file', args: { path: 'example.csv' }, result_text: '读取成功', reached_result: true }] } });
    });
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let data = [];
      if (request.method() !== 'GET') writes.push({ path, body: request.postDataJSON() });
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: 'Agent 对话测评' }];
      if (path.endsWith('/eval-queue/history')) data = rows;
      if (path.endsWith('/dimension-stats')) data = { overall_rate: 80, judged_total: 10, dims: ['thinking', 'tool_use', 'artifact'].map(dimension => ({ dimension, pass_rate: 80, total: 10 })) };
      if (path.endsWith('/eval-queue/trend')) data = { batches: [1, 2].map(i => ({ batch_id: `batch-${i}`, date: `2026-09-0${i}T10:00:00`, judged: 10, total: 10, pass_rate: 70 + i * 10, avg_score: 3 + i / 2 })) };
      if (path.endsWith('/judge-quality')) data = { overall: { reviewed: 10, confirmed: 9, accuracy: 90, fp_rate: 10, fn_rate: 0, judged: 20, review_rate: 50 }, by_engine: [] };
      if (path.endsWith('/review')) data = { review_mark: request.postDataJSON().mark, review_note: request.postDataJSON().note, is_abnormal: false };
      if (path.endsWith('/multica')) {
        const ids = request.postDataJSON().run_ids;
        rows.filter(row => ids.includes(row.run_id)).forEach(row => { row.pushed_multica = true; });
        data = { pushed: ids.length, candidates: ids.length, results: [] };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5187'}/eval-results`);
    await page.getByText('生成报告', { exact: true }).waitFor();
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.aside').evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(32, 35, 41)');
    const judgeButton = page.getByRole('button', { name: '批量判定（4）', exact: true });
    assert.equal(await judgeButton.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(37, 99, 235)');
    await judgeButton.hover();
    await page.waitForTimeout(350);
    assert.equal(await judgeButton.evaluate(node => getComputedStyle(node).backgroundColor), 'rgb(29, 78, 216)');
    await page.locator('.page-heading h1').click();
    assert.equal(await page.getByRole('tab', { name: '结果明细' }).evaluate(node => getComputedStyle(node).color), 'rgb(37, 99, 235)');
    const retryButton = page.getByRole('button', { name: '重跑失败（1）', exact: true });
    assert.equal(await retryButton.evaluate(node => getComputedStyle(node).color), 'rgb(146, 85, 6)');
    assert.equal(await page.locator('.analysis-view').isVisible(), false);
    assert.deepEqual(await page.locator('.result-summary strong').allTextContents(), ['5', '3', '1', '1']);
    await page.locator('.el-table__expand-icon').first().click();
    await page.getByRole('button', { name: '查看执行 101 详情', exact: true }).click();
    await page.locator('.run-inspector').waitFor({ state: 'visible' });
    const inspector = page.locator('.run-inspector');
    await inspector.getByText('已完成文件分析', { exact: true }).waitFor();
    await inspector.getByText('无法定论', { exact: true }).waitFor();
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/eval-verdict-desktop.png', fullPage: true });
    await inspector.getByRole('checkbox', { name: '加入推送选择' }).locator('..').click();
    assert(await page.getByRole('checkbox', { name: '选择第1轮', exact: true }).isChecked());
    await inspector.getByRole('checkbox', { name: '加入推送选择' }).locator('..').click();
    await inspector.getByRole('button', { name: '认可判定', exact: true }).click();
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('已标注：已认可判定', { exact: true }).waitFor();
    assert.deepEqual(writes.pop(), { path: '/api/eval-judge/101/review', body: { mark: 'confirmed', note: '' } });
    assert.equal(traceRequests, 0);
    await inspector.getByRole('tab', { name: '提问与回答' }).click();
    await inspector.getByText('输出文件摘要', { exact: true }).waitFor();
    await inspector.getByRole('tab', { name: '采集记录' }).click();
    await inspector.getByText('核对列名和统计范围', { exact: true }).waitFor();
    await inspector.getByRole('button', { name: /read_file/ }).click();
    await inspector.getByText('读取成功', { exact: true }).waitFor();
    assert.equal(traceRequests, 1);
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/eval-inspector-desktop.png', fullPage: true });
    await inspector.getByRole('button', { name: '关闭详情', exact: true }).click();
    await inspector.waitFor({ state: 'hidden' });
    for (const id of [104, 105]) {
      await page.getByRole('button', { name: `查看执行 ${id} 详情`, exact: true }).click();
      await inspector.getByRole('tab', { name: '采集记录' }).click();
      await inspector.getByRole('alert').waitFor();
      await inspector.getByRole('button', { name: '关闭详情' }).click();
      await inspector.waitFor({ state: 'hidden' });
    }
    assert.equal(unsafeRequests, 0);
    await page.getByRole('checkbox', { name: '选择第2轮', exact: true }).locator('..').click();
    assert.equal(await page.getByRole('checkbox', { name: '选择第1轮', exact: true }).isChecked(), false);
    await page.getByRole('tab', { name: '分析概览' }).click();
    await page.locator('.tr-chart canvas').waitFor({ state: 'visible' });
    await page.waitForTimeout(600);
    assert(await page.locator('.analysis-view canvas').evaluateAll(nodes => nodes.length === 2 && nodes.every(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      return canvas.width > 100 && pixels.some((value, index) => index % 4 === 3 && value > 0);
    })));
    await page.screenshot({ path: '/tmp/eval-analysis-desktop.png', fullPage: true });
    await page.getByRole('tab', { name: '结果明细' }).click();
    assert(await page.getByRole('checkbox', { name: '选择第2轮', exact: true }).isChecked());
    await page.getByRole('button', { name: '推送到 Multica（1）' }).click();
    await page.getByText('推送成功 1/1 条', { exact: true }).waitFor();
    assert.deepEqual(writes, [{ path: '/api/eval-export/multica', body: { project_id: 1, run_ids: [102] } }]);
    await page.getByRole('checkbox', { name: '全选当前结果', exact: true }).locator('..').click();
    await page.getByRole('button', { name: '推送到 Multica（3）' }).waitFor();
    await page.getByRole('button', { name: '清空选择', exact: true }).click();
    assert(await page.getByRole('button', { name: '推送到 Multica（0）' }).isDisabled());
    await page.getByRole('button', { name: '导出到飞书', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await page.locator('.filters .el-select').nth(1).click();
    await page.getByRole('option', { name: '未判定', exact: true }).click();
    await page.getByText('多轮 ×2', { exact: true }).waitFor({ state: 'hidden' });
    assert.equal(await page.getByText('多轮 ×2', { exact: true }).count(), 0);
    assert(await page.getByText('生成报告', { exact: true }).isVisible());
    await page.locator('.filters .el-select').nth(1).click();
    await page.getByRole('option', { name: '不通过', exact: true }).click();
    await page.getByText('多轮 ×2', { exact: true }).waitFor();
    assert.equal(await page.getByText('生成报告', { exact: true }).count(), 0);
    await page.locator('.filters .el-select').nth(1).hover();
    await page.locator('.filters .el-select').nth(1).locator('.el-select__clear').click();
    await page.getByText('生成报告', { exact: true }).waitFor();
    await page.waitForTimeout(300);
    await page.screenshot({ path: '/tmp/eval-results-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.collapse-btn').click();
    await page.waitForFunction(() => document.querySelector('.aside').getBoundingClientRect().width <= 65);
    assert(await page.locator('.header, .page-heading, .result-summary').evaluateAll(nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1)));
    await page.screenshot({ path: '/tmp/eval-results-mobile.png', fullPage: true });
    await page.getByRole('button', { name: '查看执行 103 详情', exact: true }).click();
    await inspector.waitFor({ state: 'visible' });
    await inspector.getByRole('tab', { name: '采集记录' }).click();
    await inspector.getByText('暂无 trace 记录', { exact: true }).waitFor();
    await page.waitForTimeout(350);
    assert(await inspector.evaluate(node => { const r = node.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1 && node.scrollWidth <= node.clientWidth + 1; }));
    await page.screenshot({ path: '/tmp/eval-inspector-mobile.png', fullPage: true });
    await page.keyboard.press('Escape');
    await inspector.waitFor({ state: 'hidden' });
    await page.getByRole('textbox', { name: '搜索用例或提问' }).fill('生成报告');
    assert.equal(await page.getByText('多轮 ×2', { exact: true }).count(), 0);
    assert(await page.getByRole('button', { name: '生成报告', exact: true }).isVisible());
    await page.getByRole('textbox', { name: '搜索用例或提问' }).fill('');
    await page.getByRole('button', { name: '导出到飞书', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await page.waitForTimeout(300);
    assert(await page.getByRole('dialog').evaluate(node => {
      const rect = node.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth && node.scrollWidth <= node.clientWidth + 1;
    }));
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await page.getByRole('tab', { name: '分析概览' }).click();
    await page.waitForTimeout(600);
    assert(await page.locator('.tr-chart canvas').evaluate(canvas => canvas.getBoundingClientRect().width < 390));
    assert.deepEqual(errors, []);
    console.log('PASS results/analysis, charts, inspector evidence/review/selection, trace errors and URL safety, search, multi-turn push payload, export and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
