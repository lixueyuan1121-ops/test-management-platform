const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [], requests = [];
    let mode = 'data';
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const url = new URL(route.request().url());
      requests.push(url);
      let data = [];
      if (url.pathname.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (url.pathname.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (url.pathname.endsWith('/stats/ai')) {
        if (mode === 'error') return route.fulfill({ status: 500, json: { detail: 'Mock failure' } });
        data = mode === 'empty' ? {} : { total_generated: 80, total_adopted: 40, total_reviewed: 50, adopt_rate: 0.8, project_cnt: 2, run_cnt: 4, total_cost_usd: 1.2,
          trend: [{ date: '2026-09-09', generated: 30, adopted: 10 }, { date: '2026-09-10', generated: 50, adopted: 30 }],
          by_provider: [{ provider: 'deepseek', generated: 80, reviewed: 50, adopt_rate: 0.8, run_cnt: 4, cost_usd: 1.2, avg_duration_s: 20 }] };
      }
      if (url.pathname.endsWith('/stats/ai-funnel')) {
        if (mode === 'partial') return route.fulfill({ status: 500, json: { detail: 'Mock funnel failure' } });
        data = { funnel: [{ stage: 'generated', label: '已生成', count: 80 }, { stage: 'adopted', label: '已采纳', count: 40 }], bugs_found: 2, selector_pending: 1, saved_hours: 3, from: '2026-08-12', to: '2026-09-10' };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/ai-wall`);
    await page.locator('.eng-table').waitFor();
    await page.locator('.el-loading-mask').waitFor({ state: 'hidden' });
    assert.match(await page.locator('.hero .val').innerText(), /20/);
    assert(await page.locator('.trend svg path').count() > 0);
    await page.screenshot({ path: '/tmp/ai-wall-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.getByRole('button', { name: '筛选', exact: true }).click();
    const sevenDayResponse = page.waitForResponse(response => response.url().includes('/stats/ai-funnel?days=7'));
    await page.getByText('近 7 天', { exact: true }).click();
    await sevenDayResponse;
    await page.locator('.eng-table').waitFor();
    await page.locator('.el-loading-mask').waitFor({ state: 'hidden' });
    assert(requests.some(url => url.pathname.endsWith('/ai-funnel') && url.searchParams.get('days') === '7'));
    assert.equal(await page.getByRole('radio', { name: '近 7 天', exact: true }).isChecked(), true);
    await page.waitForTimeout(350);
    await page.screenshot({ path: '/tmp/ai-wall-mobile.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('.funnel-panel').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/tmp/ai-wall-funnel-mobile.png' });
    const funnelLayout = await page.locator('.fp-step').first().evaluate(element => {
      const bar = element.getBoundingClientRect(), parent = element.parentElement.getBoundingClientRect();
      return bar.right <= parent.right + 1;
    });
    assert(funnelLayout);
    const widths = await page.locator('.fp-step').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().width));
    assert(widths[1] < widths[0]);
    mode = 'partial';
    await page.getByRole('button', { name: '刷新战绩', exact: true }).click();
    await page.getByText('价值漏斗加载失败，其他统计仍可查看', { exact: true }).waitFor();
    assert.equal(await page.locator('.funnel-panel').count(), 0);
    assert.equal(await page.locator('.eng-table').count(), 1);
    mode = 'error';
    await page.getByRole('button', { name: '刷新战绩', exact: true }).click();
    await page.getByText('战绩数据加载失败', { exact: true }).waitFor();
    assert.equal(await page.getByText('所选区间暂无 AI 生成数据', { exact: true }).count(), 0);
    mode = 'empty';
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.getByText('所选区间暂无 AI 生成数据', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS AI wall data, mobile filters, date payload, partial failure, error/retry and empty states');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
