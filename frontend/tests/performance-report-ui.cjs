const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [], writes = [];
    let failReport = false, failSave = false;
    let holdNext = false, heldRoute;
    const thresholds = { cpuPeak: { max: 80 }, fpsAvg: { min: 30 } };
    const reports = ['对话', '冷启动'].flatMap(scenario => ['版本A', '版本B'].map((variant, i) => ({ meta: { scenario, variant, startedAt: i, durations: { totalMs: 1000 + i * 400, readyMs: 400 + i * 100 }, summary: { cpu: { peak: 50 + i * 20, avg: 30 }, fps: { avg: 60 }, mem: { peak: 200, delta: 50 } } }, samples: ['cpuPct', 'memMB'].flatMap(metric => [0, 1000, 2000].map(t => ({ t, metric, value: 20 + i * 10 + t / 1000 }))) })));
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/perf/report-sets')) data = [{ id: 1, name: '性能验收', completed_count: 4, thresholds }];
      if (path.endsWith('/perf/report')) {
        if (holdNext) { holdNext = false; heldRoute = route; return; }
        if (failReport) return route.fulfill({ status: 500, json: { msg: '模拟报告失败' } });
        data = reports.filter(report => !url.searchParams.get('scenario') || report.meta.scenario === url.searchParams.get('scenario'));
      }
      if (path.endsWith('/thresholds')) {
        writes.push(req.postDataJSON());
        if (failSave) return route.fulfill({ status: 500, json: { msg: '模拟保存失败' } });
        Object.assign(thresholds, req.postDataJSON().thresholds);
        data = {};
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/perf-report`);
    await page.locator('canvas').nth(1).waitFor();
    await page.waitForTimeout(500);
    assert.equal(await page.locator('canvas').count(), 2);
    assert(await page.locator('canvas').first().evaluate(canvas => canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((value, i) => i % 4 === 3 && value > 0)));
    await page.screenshot({ path: '/tmp/perf-report-populated-desktop.png' });
    await page.locator('.workspace-actions .el-select').click();
    await page.getByRole('option', { name: '性能验收（4）', exact: true }).click();
    await page.locator('canvas').nth(1).waitFor();
    await page.locator('.scene-head .el-select').first().click();
    await page.getByRole('option', { name: '内存 MB', exact: true }).click();
    await page.locator('.chart').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await page.screenshot({ path: '/tmp/perf-memory-chart-desktop.png' });
    await page.getByRole('button', { name: '性能红线', exact: true }).click();
    const dialog = page.locator('.el-dialog');
    assert.equal(await dialog.getByRole('spinbutton', { name: 'CPU峰值', exact: true }).inputValue(), '80');
    await dialog.getByRole('spinbutton', { name: 'CPU峰值', exact: true }).fill('70');
    await dialog.getByRole('spinbutton', { name: '平均FPS', exact: true }).fill('45');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: '/tmp/perf-thresholds-mobile.png' });
    const box = await dialog.boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390);
    assert(box.y >= 0 && box.y + box.height <= 844, JSON.stringify(box));
    const saveBox = await dialog.getByRole('button', { name: '保存', exact: true }).boundingBox();
    assert(saveBox.y >= 0 && saveBox.y + saveBox.height <= 844, JSON.stringify(saveBox));
    failSave = true;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('模拟保存失败', { exact: true }).waitFor();
    assert(await dialog.isVisible());
    assert.equal(await dialog.getByRole('spinbutton', { name: 'CPU峰值', exact: true }).inputValue(), '70');
    failSave = false;
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1), { thresholds: { cpuPeak: { max: 70 }, fpsAvg: { min: 45 } } });
    failReport = true;
    await page.getByRole('button', { name: '刷新性能报告', exact: true }).click();
    await page.getByText('性能报告加载失败', { exact: true }).waitFor();
    assert.equal(await page.locator('canvas').count(), 0);
    failReport = false;
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await page.locator('canvas').nth(1).waitFor();
    holdNext = true;
    const heldRequest = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/perf/report'));
    await page.getByRole('button', { name: '刷新性能报告', exact: true }).click();
    await heldRequest;
    await page.locator('.workspace-actions .el-select').click();
    await page.getByRole('option', { name: '（全部采集）', exact: true }).click();
    await page.locator('canvas').nth(1).waitFor();
    const staleResponse = page.waitForResponse(response => response.url().includes('/perf/report?report_set_id=1'));
    await heldRoute.fulfill({ json: { code: 0, data: [] } });
    await staleResponse;
    await page.waitForTimeout(100);
    assert.equal(await page.locator('canvas').count(), 2);
    assert.deepEqual(errors, []);
    console.log('PASS populated performance charts, threshold min/max payload, mobile editing, failed save retention and report retry');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
