const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    let failList = true, failStats = true, failQuality = true, failDetail = true, holdOld = false, held;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const url = new URL(route.request().url()), path = url.pathname;
      let data = [];
      const failure = () => route.fulfill({ status: 500, json: { msg: '模拟读取失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '项目一' }, { id: 2, name: '项目二' }];
      if (path === '/api/releases') {
        if (holdOld && url.searchParams.get('project_id') === '1') { held = route; return; }
        if (failList) return failure();
        data = { items: [{ id: 11, version: `项目${url.searchParams.get('project_id')}版本`, release_date: '2026-09-10', req_count: 1, content: '列表摘要' }], total: 1 };
      }
      if (path === '/api/releases/stats') {
        if (failStats) return failure();
        data = { total_releases: 1, total_reqs: 2, this_month: 1, latest_date: '2026-09-10', trend: [{ month: '2026-09', releases: 1, reqs: 2 }] };
      }
      if (path === '/api/releases/quality') { if (failQuality) return failure(); data = { items: [] }; }
      if (path === '/api/releases/11') {
        if (failDetail) return failure();
        data = { version: 'v1', content: '完整上线内容', memo: '详细备忘', release_date: '2026-09-10', req_count: 1 };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/releases`);
    await page.getByText('版本列表加载失败', { exact: true }).waitFor();
    failList = false;
    await page.getByRole('button', { name: '重试列表', exact: true }).click();
    await page.getByText('项目1版本', { exact: true }).waitFor();
    await page.getByRole('button', { name: '详情', exact: true }).click();
    await page.getByText('发版详情加载失败', { exact: true }).waitFor();
    assert.equal(await page.locator('.el-drawer .detail').count(), 0);
    failDetail = false;
    await page.getByRole('button', { name: '重试详情', exact: true }).click();
    await page.getByText('完整上线内容', { exact: true }).waitFor();
    await page.locator('.el-drawer__close-btn').click();
    await page.getByRole('tab', { name: '质量概览', exact: true }).click();
    await page.getByText('质量概览加载失败', { exact: true }).waitFor();
    assert.equal(await page.locator('.stat-row').isVisible(), false);
    failStats = false;
    await page.getByRole('button', { name: '重试概览', exact: true }).click();
    await page.getByText('版本质量档案加载失败', { exact: true }).waitFor();
    assert(await page.locator('.stat-row').isVisible());
    failQuality = false;
    await page.getByRole('button', { name: '重试档案', exact: true }).click();
    await page.getByText('版本质量档案加载失败', { exact: true }).waitFor({ state: 'hidden' });
    await page.locator('.chart canvas').waitFor();
    await page.getByRole('tab', { name: '版本列表', exact: true }).click();
    const select = async name => { await page.locator('.el-select').first().click(); await page.getByRole('option', { name, exact: true }).click(); };
    await select('项目二');
    await page.getByText('项目2版本', { exact: true }).waitFor();
    holdOld = true;
    await select('项目一');
    await page.locator('.el-loading-mask').waitFor();
    await select('项目二');
    await page.getByText('项目2版本', { exact: true }).waitFor();
    assert(held);
    await held.fulfill({ json: { code: 0, data: { items: [{ id: 99, version: '迟到旧版本' }], total: 1 } } });
    await page.waitForTimeout(300);
    assert.equal(await page.getByText('迟到旧版本', { exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS release independent list/stats/quality/detail failures and retries, full detail and stale project response isolation');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
