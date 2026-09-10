const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    let failure = true;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }, { id: 2, name: '另一项目' }];
      if (path.startsWith('/api/stats/')) {
        if (failure) return route.fulfill({ status: 500, json: { msg: '模拟统计失败' } });
        if (path.endsWith('/daily')) data = { should_submit: 2, submitted: 1, not_submitted: [{ user_id: 2, name: '待交成员' }], avg_progress: 50, online_cnt: 1, open_issues: 2, new_issues: 1, workload_total: 4, reports: [{ name: '已交成员', progress_pct: 50, workload_hours: 4, summary: '已完成验证' }] };
        else data = { total_tasks: 4, total_online: 2, members: [{ name: '测试成员', task_cnt: 4, online_cnt: 2 }], daily: [{ date: '2026-09-10', task_cnt: 4, online_cnt: 2 }] };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/stats`);
    await page.getByText('日报统计加载失败', { exact: true }).waitFor();
    assert.equal(await page.getByText('全部已交', { exact: true }).count(), 0);
    assert.equal(await page.locator('.stat .num').count(), 0);
    failure = false;
    await page.getByRole('button', { name: '重试统计', exact: true }).click();
    await page.getByText('待交成员', { exact: true }).waitFor();
    await page.getByText('已完成验证', { exact: true }).waitFor();
    assert.deepEqual(await page.locator('.stat .num').allTextContents(), ['2', '1', '1', '50%', '1', '2']);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/daily-stats-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    failure = true;
    await page.goto(`${base}/workload`);
    await page.getByText('工作量统计加载失败', { exact: true }).waitFor();
    assert.equal(await page.locator('canvas').count(), 0);
    failure = false;
    await page.getByRole('button', { name: '重试统计', exact: true }).click();
    await page.locator('canvas').first().waitFor();
    await page.waitForTimeout(500);
    assert.equal(await page.locator('canvas').count(), 2);
    for (const canvas of await page.locator('canvas').all()) assert(await canvas.evaluate(el => el.getContext('2d').getImageData(0, 0, el.width, el.height).data.some((v, i) => i % 4 === 3 && v > 0)));
    failure = true;
    await page.locator('.el-select').first().click();
    await page.getByRole('option', { name: '另一项目', exact: true }).click();
    await page.getByText('工作量统计加载失败', { exact: true }).waitFor();
    assert.equal(await page.locator('canvas').first().isVisible(), false);
    failure = false;
    await page.getByRole('button', { name: '重试统计', exact: true }).click();
    await page.locator('canvas').first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    for (const canvas of await page.locator('canvas').all()) { const box = await canvas.boundingBox(); assert(box.x >= 0 && box.x + box.width <= 390); }
    await page.screenshot({ path: '/tmp/workload-stats-retry-mobile.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS daily/workload failure versus zero, retry, populated statistics, nonblank charts, stale chart hiding and mobile bounds');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
