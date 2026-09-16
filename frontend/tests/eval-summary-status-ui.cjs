// Offline UI regression: all API responses are mocked; no real model or task runs.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], submissions = [];
    let offline = false, statusReads = 0;
    const baseTask = { id: 18, name: '综合评价状态回归', project_id: 1, description: '本地验证', query_ids: [1],
      status: 'done', last_batch_id: 'new', summary_batch_id: 'new', target_engines: ['workbuddy'] };
    const states = {
      new: { summary_status: null, summary_progress: {}, summary_html: null },
      old: { summary_status: 'failed', summary_progress: { error: '旧批次 API Error' }, summary_html: null },
    };
    const task = bid => ({ ...baseTask, ...states[bid], summary_batch_id: bid });
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      const bid = url.searchParams.get('batch_id') || 'new';
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/eval-tasks')) data = [task('new')];
      if (path.endsWith('/eval-tasks/18/runs')) data = { task: task(bid), runs: [
        { run_id: 1, title: '已判定样本', status: 'judged', verdict: 'pass', score: 5, payload: {} }] };
      if (path.endsWith('/eval-tasks/18/batches')) data = { batches: [
        { batch_id: 'new', t0: '2026-09-17T11:00:00', total: 1, passed: 1, is_current: true },
        { batch_id: 'old', t0: '2026-09-16T10:00:00', total: 1, passed: 1, is_current: false }] };
      if (path.endsWith('/eval-tasks/18/summary-status')) {
        statusReads++;
        if (offline) return route.fulfill({ status: 503, json: { code: 503, msg: 'temporarily unavailable' } });
        const { summary_html, ...state } = states[bid];
        data = { task_id: 18, summary_batch_id: bid, ...state };
      }
      if (path.endsWith('/eval-tasks/18/summarize')) {
        const body = req.postDataJSON();
        submissions.push(body);
        states[body.batch_id] = { summary_status: 'queued', summary_progress: { stage: 'queued', elapsed_seconds: 0 }, summary_html: null };
        data = { job_id: submissions.length };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5197';
    const open = async () => {
      await page.getByRole('button', { name: baseTask.name, exact: true }).click();
      await page.getByRole('tab', { name: /综合评价/ }).click();
    };
    const panel = page.locator('.summary-status');
    const waitStatus = label => panel.getByText(label, { exact: true }).waitFor({ timeout: 12000 });
    await page.goto(`${base}/eval-tasks`);
    await open();
    await waitStatus('未生成');
    await page.getByRole('button', { name: '生成综合评价', exact: true }).click();
    await waitStatus('排队中');
    assert.equal(submissions.length, 1);
    assert(await page.getByRole('button', { name: '排队中', exact: true }).isDisabled());

    states.new = { summary_status: 'running', summary_progress: { stage: 'waiting_model', elapsed_seconds: 12 }, summary_html: null };
    await waitStatus('生成中');
    await panel.getByText('已等待 12 秒', { exact: true }).waitFor();
    states.new.summary_progress = { stage: 'generating', output_chars: 1234, elapsed_seconds: 75 };
    await panel.getByText(/已收到 1,234 字符/).waitFor({ timeout: 12000 });
    states.new.summary_progress = { stage: 'generating', phase: 'analyzing', chunk_total: 8,
      chunk_completed: 3, current_chunk: 4, reused_chunks: 2, output_chars: 800 };
    await panel.getByText(/已完成 3\/8 段，正在处理第 4 段/).waitFor({ timeout: 12000 });
    await panel.getByText(/已复用 2 段摘要/).waitFor();
    states.new.summary_progress = { stage: 'waiting_model', phase: 'merging', chunk_total: 8,
      chunk_completed: 8, merge_level: 1, merge_current: 1, merge_total: 2 };
    await panel.getByText(/合并第 1 层摘要（1\/2）/).waitFor({ timeout: 12000 });
    states.new.summary_progress = { stage: 'generating', phase: 'final', chunk_total: 8,
      chunk_completed: 8, output_chars: 1500 };
    await panel.getByText(/已完成 8 段分析，正在生成最终综合评价/).waitFor({ timeout: 12000 });
    states.new.summary_progress = { stage: 'retry_wait', retry_count: 1, retry_delay_seconds: 5.5,
      last_error: 'API Error: empty or malformed response (HTTP 200)', elapsed_seconds: 80 };
    await waitStatus('自动重试中');
    await panel.getByText('查看最近一次模型错误', { exact: true }).click();
    await panel.getByText(/API Error: empty or malformed/).waitFor();
    await page.screenshot({ path: '/tmp/eval-summary-retrying.png', fullPage: true });

    states.new = { summary_status: 'failed', summary_progress: { stage: 'failed', retry_count: 1,
      elapsed_seconds: 88, chunk_completed: 3, chunk_total: 8,
      error: 'API Error: empty or malformed response (HTTP 200)' }, summary_html: null };
    await waitStatus('生成失败');
    assert(await page.getByRole('button', { name: '重新生成综合评价', exact: true }).isEnabled());
    await page.reload();
    await open();
    await waitStatus('生成失败');
    await panel.getByText(/API Error: empty or malformed/).waitFor();
    await panel.getByText(/已保存 3\/8 段分析/).waitFor();
    await page.screenshot({ path: '/tmp/eval-summary-failed.png', fullPage: true });

    await page.getByRole('button', { name: '重新生成综合评价', exact: true }).click();
    await waitStatus('排队中');
    await page.getByRole('button', { name: 'Close this dialog' }).click();
    await open();
    await waitStatus('排队中');
    assert.equal(submissions.length, 2, 'reopening must not create a second generation');
    states.new = { summary_status: 'done', summary_progress: { stage: 'done', elapsed_seconds: 20 },
      summary_html: '<h2>新的综合评价正文</h2>', summary_at: '2026-09-17T11:01:00', summary_provider: 'claude', summary_share_code: 'test-share' };
    await waitStatus('已生成');
    await page.getByText('新的综合评价正文', { exact: true }).waitFor();
    await page.getByRole('button', { name: '打开在线报告', exact: true }).waitFor();

    await page.locator('.d-meta .el-select').click();
    await page.getByRole('option', { name: /09-16 10:00/ }).click();
    await waitStatus('生成失败');
    await panel.getByText('旧批次 API Error', { exact: true }).waitFor();
    await page.getByRole('button', { name: '重新生成综合评价', exact: true }).click();
    await waitStatus('排队中');
    assert.equal(submissions.at(-1).batch_id, 'old', 'historical generation must target the selected batch');
    await page.locator('.d-meta .el-select').click();
    await page.getByRole('option', { name: /09-17 11:00/ }).click();
    await waitStatus('已生成');
    assert(await page.getByRole('button', { name: '重新生成综合评价', exact: true }).isEnabled());

    offline = true;
    await panel.getByText(/状态暂时无法更新/).waitFor({ timeout: 12000 });
    await waitStatus('已生成');
    assert(await page.getByRole('button', { name: '重新生成综合评价', exact: true }).isDisabled());
    offline = false;
    await panel.getByText(/状态暂时无法更新/).waitFor({ state: 'hidden', timeout: 12000 });
    states.new = { summary_status: 'failed', summary_progress: { stage: 'failed', error: 'API Error: ' + 'gateway-error-'.repeat(50) } };
    await waitStatus('生成失败');
    assert.equal(await page.locator('.summary-html').count(), 0, 'failed new generation must not show stale report');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: '/tmp/eval-summary-mobile.png', fullPage: true });
    const panelBox = await panel.boundingBox();
    assert(panelBox.x >= 0 && panelBox.x + panelBox.width <= 390,
      JSON.stringify(await panel.evaluate(el => ({ box: el.getBoundingClientRect().toJSON(),
        drawer: el.closest('.el-drawer').getBoundingClientRect().toJSON(),
        bodyScroll: el.closest('.el-drawer__body').scrollLeft,
        style: el.closest('.el-drawer').getAttribute('style') }))));
    assert(await panel.evaluate(el => el.scrollWidth <= el.clientWidth + 1), 'long error must wrap');
    await page.getByRole('button', { name: 'Close this dialog' }).click();
    const readsAtClose = statusReads;
    await page.waitForTimeout(3500);
    assert.equal(statusReads, readsAtClose, 'closed detail must stop polling');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await open();
    await page.getByRole('button', { name: '重新生成综合评价', exact: true }).click();
    await waitStatus('排队中');
    await page.getByRole('button', { name: 'Close this dialog' }).click();
    states.new = { summary_status: 'done', summary_progress: { stage: 'done' },
      summary_at: '2026-09-17T11:02:00', summary_html: '<p>后台生成完成</p>' };
    await page.locator('.el-table__body-wrapper').getByText('已生成', { exact: true }).waitFor({ timeout: 12000 });
    assert.deepEqual(errors, []);
    console.log('PASS: queued/generating/retry/failed/done, persistent error on refresh, reopen recovery, history isolation, connection recovery, mobile wrapping, polling cleanup');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
