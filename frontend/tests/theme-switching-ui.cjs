// Both themes share the real routes/components; business APIs are local fixtures only.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
const output = path.resolve('artifacts/ui-themes-2026-10-05');
fs.mkdirSync(output, { recursive: true });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function choose(page, label) {
  await page.getByRole('button', { name: '切换界面主题', exact: true }).click();
  await page.getByRole('menuitem', { name: label, exact: false }).click();
  await page.waitForFunction(value => document.documentElement.dataset.uiTheme === value, label === '深色科技' ? 'tech' : 'fresh');
}
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const page = await context.newPage(), errors = [], requests = [];
    page.on('pageerror', e => errors.push(e.message));
    await context.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), p = new URL(req.url()).pathname;
      requests.push({ method: req.method(), path: p, body: req.postData() });
      let data = [];
      if (p === '/api/auth/login') data = { access_token: 'mock-only-token', refresh_token: 'mock-only-refresh' };
      if (p === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (p === '/api/projects') data = [{ id: 1, name: '纳米Work PC端', code: 'mock' }];
      if (p === '/api/stats/overview') data = {};
      if (p === '/api/eval-tasks') data = [{ id: 1, name: '多轮上下文专项', description: '上下文保持与连续指令理解', status: 'running', query_ids: [1,2], last_batch_id: 'mock-batch', done_count: 1, run_count: 2 }];
      if (p === '/api/ai/eval-queries') data = [{ id: 1, title: '读取文档', prompt: '请读取附件并归纳', expected: '给出可核对的结论', turn_index: 0 }];
      if (p === '/api/ai/eval-engines') data = [{ engine: 'namiwork', label: '纳米Work' }];
      if (p === '/api/stats/workload') data = { total_tasks: 3, total_online: 1, members: [{ name: '测试员', task_cnt: 3, online_cnt: 1 }], daily: [{ date: '2026-10-04', task_cnt: 3, online_cnt: 1 }, { date: '2026-10-05', task_cnt: 4, online_cnt: 2 }] };
      if (p === '/api/verified-imports/jobs') data = { items: [], total: 0 };
      if (p === '/api/selectors/audit') data = { config: {}, runs: [], pages: [] };
      if (p === '/api/releases') data = { items: [], total: 0 };
      if (p === '/api/releases/stats') data = { total_releases: 2, total_reqs: 4, this_month: 2, trend: [{ month: '2026-09', releases: 1, reqs: 2 }, { month: '2026-10', releases: 2, reqs: 4 }] };
      if (p === '/api/releases/quality') data = { items: [] };
      if (p === '/api/devices/overview') data = { total_devices: 0, online_devices: 0, running_devices: 0, devices: [] };
      if (p === '/api/stats/ai-funnel') data = { funnel: [], bugs_found: 0 };
      if (p === '/api/feedback/defense-calendar') data = { days: [], streak: 0, total_guard_days: 0 };
      if (p === '/api/selectors/manage') data = { shared: [], by_sub: {} };
      if (p === '/api/api-env') data = { base_url: '', auth_type: 'fixed', auth: {}, contract: '' };
      if (p === '/api/test-missions/metrics') data = { total: 0, criteria: [], runs: [] };
      if (p === '/api/stats/daily') data = { should_submit: 0, submitted: 0, not_submitted: [], reports: [] };
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(base + '/login');
    await page.getByPlaceholder('请输入用户名').fill('mock-user');
    await page.getByPlaceholder('请输入密码').fill('mock-password');
    const before = requests.length;
    await choose(page, '深色科技');
    assert.equal(await page.getByPlaceholder('请输入用户名').inputValue(), 'mock-user');
    assert.equal(await page.getByPlaceholder('请输入密码').inputValue(), 'mock-password');
    assert.equal(requests.length, before, 'theme switch must not call a business API');
    assert.equal(await page.evaluate(() => localStorage.getItem('tp_ui_theme')), 'tech');
    await page.screenshot({ path: path.join(output, 'login-tech.png') });
    await page.reload();
    await page.getByRole('button', { name: '登录', exact: true }).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-ui-theme'), 'tech');
    await choose(page, '清爽易用');
    await page.screenshot({ path: path.join(output, 'login-fresh.png') });
    // A real storage event from another same-origin tab updates both preference and control.
    const second = await context.newPage();
    await second.goto(base + '/login');
    await choose(second, '深色科技');
    await page.waitForFunction(() => document.documentElement.dataset.uiTheme === 'tech');
    await second.close();
    await page.getByPlaceholder('请输入用户名').fill('mock-user');
    await page.getByPlaceholder('请输入密码').fill('mock-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL('**/dashboard');
    assert.equal(await page.locator('html').getAttribute('data-ui-theme'), 'tech');
    assert.deepEqual(JSON.parse(requests.find(r => r.path === '/api/auth/login').body), { username: 'mock-user', password: 'mock-password' });
    await page.goto(base + '/eval-tasks');
    await page.getByRole('button', { name: '多轮上下文专项', exact: true }).waitFor();
    await page.getByRole('textbox', { name: '搜索任务', exact: true }).fill('多轮');
    await delay(300);
    const reads = requests.length;
    await choose(page, '清爽易用');
    assert.equal(await page.getByRole('textbox', { name: '搜索任务', exact: true }).inputValue(), '多轮');
    assert.equal(requests.length, reads, 'theme switch must not refetch/reset the task list');
    await page.getByRole('button', { name: '新建任务', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '新建测评任务', exact: true });
    await editor.getByPlaceholder('如：多轮上下文专项 / v2.3 回归测评').fill('未保存输入');
    // Storage event also covers an open teleported dialog without closing or remounting it.
    await page.evaluate(() => window.dispatchEvent(new StorageEvent('storage', { key: 'tp_ui_theme', newValue: 'tech' })));
    assert.equal(await editor.getByPlaceholder('如：多轮上下文专项 / v2.3 回归测评').inputValue(), '未保存输入');
    assert.equal(await editor.locator('.el-dialog').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(24, 35, 51)');
    await page.screenshot({ path: path.join(output, 'task-editor-tech.png') });
    await editor.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(requests.filter(r => r.method !== 'GET' && r.path !== '/api/auth/login').length, 0);
    await page.goto(base + '/workload');
    await page.locator('canvas').first().waitFor();
    await choose(page, '深色科技');
    await delay(700);
    const firstImage = await page.locator('canvas').first().evaluate(el => el.toDataURL());
    const chartReads = requests.length;
    await choose(page, '清爽易用');
    await delay(700);
    const secondImage = await page.locator('canvas').first().evaluate(el => el.toDataURL());
    assert(firstImage !== secondImage, 'canvas chart colors must redraw in place');
    assert.equal(requests.length, chartReads, 'chart theme redraw must not reload data');
    // All real routes mount in both themes; nonempty action coverage lives in the existing suites.
    const routerSource = fs.readFileSync(path.join(__dirname, '../src/router/index.js'), 'utf8');
    const routes = [...routerSource.matchAll(/path: '([^']+)'[^\n]+component:/g)].map(m => '/' + m[1].replace(/^\//, '').replace(':id', '1')).filter(r => r !== '/login' && r !== '/');
    const mounts = [];
    for (const mode of ['fresh', 'tech']) {
      await choose(page, mode === 'fresh' ? '清爽易用' : '深色科技');
      for (const route of routes) {
        await page.goto(base + route);
        await page.getByRole('button', { name: '切换界面主题', exact: true }).waitFor();
        await delay(120);
        assert.equal(await page.locator('html').getAttribute('data-ui-theme'), mode, route);
        assert.equal(await page.locator('.main').evaluate(el => getComputedStyle(el).backgroundColor), mode === 'tech' ? 'rgb(16, 23, 34)' : 'rgb(246, 248, 251)', route);
        mounts.push({ route, theme: mode });
      }
      for (const route of ['/dashboard','/device-board','/eval-tasks','/projects','/login']) {
        await page.goto(base + route);
        await page.getByRole('button', { name: '切换界面主题', exact: true }).waitFor();
        await delay(300);
        await page.screenshot({ path: path.join(output, route.slice(1) + '-' + mode + '-desktop.png') });
        await page.setViewportSize({ width: 390, height: 844 });
        await delay(300);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, route + ' mobile overflow');
        await page.screenshot({ path: path.join(output, route.slice(1) + '-' + mode + '-mobile.png') });
        await page.setViewportSize({ width: 1440, height: 1000 });
      }
    }
    fs.writeFileSync(path.join(output, 'route-mounts.json'), JSON.stringify({ mounts, errors }, null, 2));
    assert.deepEqual(errors, []);
    console.log(`PASS theme switching, persistence, login payload, cross-tab sync, unsaved input, dialog, chart redraw and ${mounts.length} route/theme mounts.`);
    await context.close();
    const blocked = await browser.newPage();
    await blocked.addInitScript(() => {
      const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
      Storage.prototype.getItem = function(key) { if (key === 'tp_ui_theme') throw new Error('storage unavailable'); return get.call(this,key); };
      Storage.prototype.setItem = function(key,value) { if (key === 'tp_ui_theme') throw new Error('storage unavailable'); return set.call(this,key,value); };
    });
    await blocked.goto(base + '/login');
    await choose(blocked, '深色科技');
    assert.equal(await blocked.locator('html').getAttribute('data-ui-theme'), 'tech');
    console.log('PASS storage unavailable fallback.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
