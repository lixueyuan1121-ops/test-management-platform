const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'fixture-only'));
    const row = { run_id: 18, case_id: 1, project_id: 1, title: '视觉PPT功能回归', batch_id: 'fixture', kind: 'gui', runner: 'fixture', status: 'blocked', verdict: 'blocked', has_report: true, reason: "step6: 目标 xpath=//*[contains(normalize-space(.), '视觉PPT制作')] 在允许的 frame 中匹配 18 个元素；请限定 frame/within/has_text" };
    await page.route(u => u.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: false, memberships: [{ project_id: 1, role: 'member' }] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/exec-queue/history') data = { items: [row], total: 1, runners: ['fixture'] };
      if (path === '/api/exec-queue/18') data = { ...row, execution_evidence: { mode: 'script', script_sha256: 'a'.repeat(64), runtime_sha256: 'b'.repeat(64) }, matches_current_version: false,
        replay_readiness: { state: 'pending', label: '当前版本待回归验证', consecutive_passes: 0, required_passes: 2 },
        report: [{ action: 'click', ok: false, diagnostic: { code: 'AMBIGUOUS_TARGET', match_count: 18, frames: [{ scope: 'shell', count: 18 }], target: { selector: 'xpath=//*' }, suggestion: '限定所属容器' } }] };
      if (path === '/api/exec-queue/enqueue-cases') { writes.push(req.postDataJSON()); data = { run_ids: [20, 21], batch_id: 'verification-fixture' }; }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto((process.env.UI_BASE_URL || 'http://127.0.0.1:5193') + '/exec-results?project_id=1');
    await page.getByText('定位不唯一', { exact: true }).waitFor();
    assert.equal(await page.getByText('补齐选择器', { exact: true }).count(), 0);
    await page.getByText('查看匹配冲突（18）', { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '执行报告' });
    await dialog.getByText('执行方式：脚本执行', { exact: true }).waitFor();
    await dialog.getByText('shell：18 个匹配', { exact: true }).waitFor();
    await dialog.getByText('本次执行与当前回归版本不同，当前脚本或选择器需要重新验证', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '验证回归（2 次）' }).click();
    assert.equal(writes.length, 0, 'confirmation precedes side effects');
    await page.getByRole('button', { name: '下发验证', exact: true }).click();
    await page.waitForURL('**batch_id=verification-fixture');
    assert.deepEqual(writes, [{ project_id: 1, runner: 'auto', test_case_ids: [1], verification_runs: 2 }]);
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(errors, []);
    if (process.env.UI_SCREENSHOT) await page.screenshot({ path: process.env.UI_SCREENSHOT, fullPage: true });
    console.log('PASS: ambiguous historical selector, exact count/frame, execution mode/version, stale evidence warning and two-run dispatch');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
