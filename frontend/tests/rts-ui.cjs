const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRead = false, holdOld = false, heldRoute, recommendation = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (req.method() !== 'GET') writes.push({ path, body: req.postDataJSON() });
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [{ project_id: 1, role: 'admin' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/releases') data = { items: [{ id: 11, version: 'v1', release_date: '2026-09-10' }, { id: 12, version: 'v2', release_date: '2026-09-11' }] };
      if (path === '/api/rts/candidates') {
        if (holdOld && url.searchParams.get('release_id') === '11') { heldRoute = route; return; }
        if (failRead) return route.fulfill({ status: 500, json: { msg: '模拟读取失败' } });
        const id = Number(url.searchParams.get('release_id'));
        data = { items: [{ case_id: id * 10, title: `版本${id}高分用例`, priority: 'P1', risk_score: 80, signals: { in_release: 50 } }, { case_id: id * 10 + 1, title: `版本${id}低分用例`, priority: 'P2', risk_score: 30, signals: {} }], in_release_count: 1 };
      }
      if (path === '/api/rts/recommendation') data = { exists: recommendation, overall_risk: 'high', recommended_count: 1, candidate_count: 2, summary: '重点检查核心流程', rationale: '核心用例近期失败', focus_points: ['验证修复结果'] };
      if (path === '/api/rts/analyze') { recommendation = true; data = { job_id: 5 }; }
      if (path === '/api/ai-jobs/5') data = { status: 'done', result: {} };
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/rts`);
    await page.getByText('请选择版本', { exact: true }).waitFor();
    async function selectRelease(version) {
      await page.locator('.el-select').nth(1).click();
      await page.getByRole('option', { name: new RegExp(`^${version}（`) }).click();
    }
    await selectRelease('v1');
    await page.getByText('版本11高分用例', { exact: true }).waitFor();
    await page.getByText('候选 2 条（属本版本 1）· 已选 1', { exact: true }).waitFor();
    await page.getByRole('button', { name: '下发所选回归', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(writes.length, 0);
    await page.getByRole('button', { name: '下发所选回归', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '确认下发', exact: true }).click();
    await page.getByText('已下发 1 条，结果计入该版本质量卡', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/exec-queue/enqueue-cases', body: { project_id: 1, runner: 'auto', test_case_ids: [110], release_id: 11 } });
    await page.getByRole('button', { name: 'AI 生成推荐', exact: true }).click();
    await page.getByText('重点检查核心流程', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1), { path: '/api/rts/analyze', body: { project_id: 1, release_id: 11 } });
    failRead = true;
    await selectRelease('v2');
    await page.getByText('候选用例或推荐加载失败', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '下发所选回归', exact: true }).isDisabled());
    assert.equal(await page.getByText('版本11高分用例', { exact: true }).count(), 0);
    failRead = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await page.getByText('版本12高分用例', { exact: true }).waitFor();
    holdOld = true;
    await selectRelease('v1');
    await page.waitForFunction(() => document.querySelector('.el-loading-mask'));
    await selectRelease('v2');
    await page.getByText('版本12高分用例', { exact: true }).waitFor();
    assert(heldRoute);
    await heldRoute.fulfill({ json: { code: 0, data: { items: [{ case_id: 999, title: '迟到的旧用例', risk_score: 90 }], in_release_count: 1 } } });
    await page.waitForTimeout(250);
    assert.equal(await page.getByText('迟到的旧用例', { exact: true }).count(), 0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
    await page.getByRole('button', { name: '下发所选回归', exact: true }).click();
    await page.waitForTimeout(350);
    const box = await page.locator('.el-message-box').boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
    await page.screenshot({ path: '/tmp/rts-confirm-mobile.png', fullPage: true });
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.deepEqual(errors, []);
    console.log('PASS RTS default selection, analyze/dispatch payload, cancel, failure retry, stale response isolation and mobile confirmation');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
