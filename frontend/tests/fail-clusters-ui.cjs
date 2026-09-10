const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRead = false, failIssue = true, holdOld = false, heldRoute, failAnalysis = false;
    const longTitle = '需求标题包含完整业务场景和很长的补充说明'.repeat(8);
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (req.method() !== 'GET') writes.push({ path, body: req.postDataJSON() });
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [{ project_id: 1, role: 'admin' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/releases') data = { items: [{ id: 11, version: 'v1', release_date: '2026-09-10' }, { id: 12, version: 'v2', release_date: '2026-09-11' }] };
      if (path === '/api/fail-clusters/scope') {
        if (holdOld && url.searchParams.get('release_id') === '11') { heldRoute = route; return; }
        if (failRead) return route.fulfill({ status: 500, json: { msg: '模拟读取失败' } });
        data = { requirements: [{ id: 21, title: longTitle, fail_count: 2 }, { id: 22, title: '没有失败的需求', fail_count: 0 }] };
      }
      if (path === '/api/fail-clusters') data = { items: [{ id: 31, root_cause_title: `版本${url.searchParams.get('release_id')}根因`, summary: '检查路径编码与重试记录'.repeat(10), triage_kind: 'bug', member_count: 2, requirement_ids: [21], severity: 'major', issue_id: null }], fail_count: 2 };
      if (path === '/api/fail-clusters/analyze') data = { job_id: 5 };
      if (path === '/api/ai-jobs/5') data = failAnalysis ? { status: 'failed', error: '模拟分析失败' } : { status: 'done', result: {} };
      if (path === '/api/fail-clusters/31/create-issue') {
        if (failIssue) return route.fulfill({ status: 500, json: { msg: '模拟建草稿失败' } });
        data = { issue_id: 41, already: false };
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/fail-clusters`);
    await page.getByText('请选择版本', { exact: true }).waitFor();
    async function selectRelease(version) {
      await page.locator('.el-select').nth(1).click();
      await page.getByRole('option', { name: new RegExp(`^${version}（`) }).click();
    }
    await selectRelease('v1');
    await page.getByText('版本11根因', { exact: true }).waitFor();
    const analyze = page.getByRole('button', { name: 'AI 聚类去噪', exact: true });
    await page.locator('.scope .el-checkbox').first().click();
    assert(await analyze.isDisabled());
    await page.locator('.scope .el-checkbox').last().click();
    assert(await analyze.isDisabled());
    assert.equal(writes.length, 0);
    await page.locator('.scope .el-checkbox').first().click();
    failAnalysis = true;
    await analyze.click();
    await page.getByText('聚类失败：模拟分析失败', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/fail-clusters/analyze', body: { project_id: 1, release_id: 11, requirement_ids: [21] } });
    failAnalysis = false;
    await analyze.click();
    await page.getByText('聚类完成', { exact: true }).waitFor();
    await page.getByRole('button', { name: '一键建缺陷草稿', exact: true }).click();
    await page.getByText('模拟建草稿失败', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '一键建缺陷草稿', exact: true }).isEnabled());
    failIssue = false;
    await page.getByRole('button', { name: '一键建缺陷草稿', exact: true }).click();
    await page.getByText('已建缺陷 #41', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/fail-clusters/31/create-issue', body: {} });
    failRead = true;
    await selectRelease('v2');
    await page.getByText('需求或聚类结果加载失败', { exact: true }).waitFor();
    assert(await analyze.isDisabled());
    assert.equal(await page.getByText('版本11根因', { exact: true }).count(), 0);
    failRead = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.getByText('版本12根因', { exact: true }).waitFor();
    holdOld = true;
    await selectRelease('v1');
    await page.locator('.el-skeleton').waitFor();
    await selectRelease('v2');
    await page.getByText('版本12根因', { exact: true }).waitFor();
    assert(heldRoute);
    await heldRoute.fulfill({ json: { code: 0, data: { requirements: [{ id: 99, title: '迟到的旧需求', fail_count: 4 }] } } });
    await page.waitForTimeout(250);
    assert.equal(await page.getByText('迟到的旧需求', { exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.waitForTimeout(350);
    assert(await page.locator('.fc-page').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
    await page.locator('.main').evaluate(el => { el.scrollTop = el.scrollHeight; });
    const box = await analyze.boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
    await page.screenshot({ path: '/tmp/fail-clusters-mobile.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('PASS clustering selected scope, analysis/draft failure retry, write payloads, stale version isolation and sticky mobile actions');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
