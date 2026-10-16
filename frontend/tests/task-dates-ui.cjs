const { chromium } = require('../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const requests = [], errors = [];
  let delaySingle = false;
  try {
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'isolated-ui-test'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const url = new URL(route.request().url());
      let data = [];
      if (url.pathname === '/api/auth/me') data = { user: { id: 1, name: '管理员' }, is_platform_admin: true, memberships: [] };
      if (url.pathname === '/api/projects') data = [{ id: 1, name: '日期筛选回归项目' }];
      if (url.pathname === '/api/projects/1/members') data = [{ user_id: 1, name: '管理员', username: 'admin' }];
      if (url.pathname === '/api/tasks' || url.pathname === '/api/tasks/checklist-summary') {
        const dates = [url.searchParams.get('start_date'), url.searchParams.get('end_date')].filter(Boolean);
        assert(!url.searchParams.has('dates') && !url.searchParams.has('dates[]'), 'range must use start/end parameters');
        requests.push({ path: url.pathname, dates });
        if (delaySingle && dates.length === 2 && dates[0] === dates[1]) await new Promise(resolve => setTimeout(resolve, 800));
        data = url.pathname.endsWith('checklist-summary') ? { 1: { total: 1, passed: 1 } } : [{
          id: 1, title: dates.length ? `日期：${dates.join(',')}` : '全部日期任务',
          description: dates.length ? `日期：${dates.join(',')}` : '全部日期任务',
          assigned_date: dates[0] || '2026-01-01', assigned_to_ids: [1], assigned_to_name: '管理员', priority: 'p2', status: 'pending',
        }];
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto((process.env.UI_BASE_URL || 'http://127.0.0.1:5196') + '/tasks');
    const table = page.locator('.el-table').first();
    await table.getByText(/日期：/).waitFor();
    const input = page.getByPlaceholder('开始日期');
    const editor = page.locator('.el-date-editor').first();
    const copy = page.getByRole('button', { name: '复制昨日', exact: true });
    assert(await copy.isEnabled());
    await editor.hover();
    await editor.locator('.el-range__close-icon').click();
    await table.getByText('全部日期任务', { exact: true }).waitFor();
    assert(await copy.isDisabled());
    assert.equal(requests.filter(r => r.path.endsWith('checklist-summary')).at(-1).dates.length, 0);

    await page.getByRole('button', { name: '新建任务', exact: true }).click();
    const dialog = page.getByRole('dialog');
    const day = new Date();
    const today = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
    assert.equal(await dialog.getByRole('combobox', { name: /分配日期/ }).inputValue(), today);
    await dialog.getByRole('button', { name: '取消', exact: true }).click();

    await input.click();
    const calendar = page.locator('.el-picker-panel:visible');
    const left = calendar.locator('.el-date-range-picker__content.is-left');
    const right = calendar.locator('.el-date-range-picker__content.is-right');
    const choose = (panel, day) => panel.locator('td.available').filter({ hasText: new RegExp(`^${day}$`) }).click();
    const before = requests.length;
    await choose(left, 1);
    assert.equal(requests.length, before, 'do not query an incomplete range');
    await choose(left, 3);
    await table.getByText(/日期：.*-01,.*-03/).waitFor();
    const selected = requests.filter(r => r.path === '/api/tasks').at(-1).dates;
    assert.equal(selected.length, 2);
    assert(selected[0].endsWith('-01') && selected[1].endsWith('-03'));
    assert.deepEqual(requests.filter(r => r.path.endsWith('checklist-summary')).at(-1).dates, selected);
    assert(await copy.isDisabled());

    // Cross-month selection must retain both actual endpoints.
    await input.click();
    await choose(left, 28);
    await choose(right, 3);
    await table.getByText(/日期：.*-28,.*-03/).waitFor();
    const crossMonth = requests.filter(r => r.path === '/api/tasks').at(-1).dates;
    assert.notEqual(crossMonth[0].slice(0, 7), crossMonth[1].slice(0, 7));

    // A delayed single-day response cannot replace the cleared all-date view.
    delaySingle = true;
    await input.click();
    await choose(left, 1);
    await choose(left, 1);
    await page.waitForTimeout(100);
    assert(await copy.isEnabled());
    await editor.hover();
    await editor.locator('.el-range__close-icon').click();
    await table.getByText('全部日期任务', { exact: true }).waitFor();
    await page.waitForTimeout(1000);
    assert(await table.getByText('全部日期任务', { exact: true }).isVisible());
    assert(await copy.isDisabled());
    delaySingle = false;
    await input.click();
    await choose(left, 1);
    await choose(left, 3);
    await table.getByText(/日期：/).waitFor();
    await input.press('Delete');
    await table.getByText('全部日期任务', { exact: true }).waitFor();
    assert(await copy.isDisabled());
    assert.deepEqual(errors, []);
    console.log('PASS: default date; clear button and keyboard deletion; new-task date fallback; inclusive date range and cross-month selection; summary parameters; copy guard; stale-response guard');
  } catch (error) {
    console.error('UI requests:', JSON.stringify(requests), 'page errors:', errors, 'body:', await page.locator('body').innerText());
    await page.screenshot({ path: '../backend/artifacts/task-date-range-20261016/ui-failure.png', timeout: 10000 });
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
