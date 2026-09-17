// All platform traffic is mocked; fixture data contains no real conversations.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-only'));
    const trace = { product: 'qwork', session_id: 'qwork-test', request_id: 'turn-2', bean_cost: 0,
      turn_status: 'completed', thinking: '核对本轮附件', answer: '最终结果',
      capture_diagnostics: { status: 'complete', message_count: 3, event_count: 8 },
      tool_calls: [{ name: 'read_file', args: { path: 'input.txt' }, result_text: '合成测试内容', reached_result: true }],
      raw_history: { messages: [{ role: 'user', text: '完整提问' }, { role: 'assistant', text: '中间回复' }, { role: 'assistant', text: '最终结果' }], events: [{ kind: 'tool_result', raw: '完整原文' }] } };
    await page.route('**/uploads/qwork-test.json', route => route.fulfill({ json: trace }));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const pathname = new URL(route.request().url()).pathname;
      let data = [];
      if (pathname.endsWith('/auth/me')) data = { user: { id: 1, name: '测试' }, is_platform_admin: true, memberships: [] };
      if (pathname.endsWith('/projects')) data = [{ id: 1, name: 'QWork测试项目' }];
      if (pathname.endsWith('/ai/eval-engines')) data = [{ engine: 'namiwork', label: '纳米Work' }, { engine: 'workbuddy', label: 'WorkBuddy' }, { engine: 'qwork', label: 'QWork' }];
      if (pathname.endsWith('/dimension-stats')) data = { overall_rate: 0, judged_total: 0, dims: [] };
      if (pathname.endsWith('/eval-queue/trend')) data = { batches: [] };
      if (pathname.endsWith('/judge-quality')) data = { overall: { reviewed: 0, judged: 0 }, by_engine: [] };
      if (pathname.endsWith('/eval-queue/history')) data = [{ run_id: 1, batch_id: 'b', target_engine: 'qwork', status: 'done',
        answer: '最终结果', trace: '/uploads/qwork-test.json', raw_message: '{"schema":"qwork-trace-index-v1"}', payload: { title: 'QWork采集验证', prompt: '完整提问' } }];
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5197';
    await page.goto(`${base}/eval-tasks`);
    await page.getByRole('button', { name: /新建任务/ }).click();
    await page.getByRole('checkbox', { name: 'QWork', exact: true }).locator('..').waitFor();
    assert(await page.getByRole('checkbox', { name: 'QWork', exact: true }).isChecked());
    await page.getByRole('checkbox', { name: 'QWork', exact: true }).locator('..').click();
    assert.equal(await page.getByRole('checkbox', { name: 'QWork', exact: true }).isChecked(), false);
    await page.getByRole('checkbox', { name: 'QWork', exact: true }).locator('..').click();
    assert(await page.getByRole('checkbox', { name: 'QWork', exact: true }).isChecked());
    await page.goto(`${base}/eval-results`);
    await page.getByRole('button', { name: '查看执行 1 详情', exact: true }).click();
    const inspector = page.locator('.run-inspector');
    await inspector.getByRole('tab', { name: '采集记录' }).click();
    await inspector.getByText('QWork 原生会话记录', { exact: true }).waitFor();
    await inspector.getByText('积分：0 · 执行状态：已完成', { exact: true }).waitFor();
    await inspector.getByRole('button', { name: '各阶段对话正文', exact: true }).click();
    await inspector.getByText('中间回复', { exact: true }).waitFor();
    await inspector.getByRole('button', { name: /read_file/ }).click();
    await inspector.getByText('合成测试内容', { exact: true }).waitFor();
    const downloading = page.waitForEvent('download');
    await inspector.getByRole('button', { name: '下载完整记录 JSON', exact: true }).click();
    const download = await downloading;
    assert.equal(download.suggestedFilename(), 'qwork-1-trace.json');
    assert.deepEqual(JSON.parse(await fs.readFile(await download.path(), 'utf8')), trace);
    await page.screenshot({ path: '/tmp/qwork-inspector-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(350);
    const box = await inspector.boundingBox();
    assert(box.x >= -1 && box.x + box.width <= 391);
    assert.deepEqual(errors, []);
    console.log('PASS: QWork selection, complete evidence, zero credits, original JSON download, mobile layout');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
