const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5191';
    for (const scenario of [
      { name: 'member', roles: ['member', 'guest'], allowed: true },
      { name: 'project-admin', roles: ['admin', 'guest'], allowed: true },
      { name: 'guest', roles: ['guest', 'guest'], allowed: false },
      { name: 'no-membership', roles: [], allowed: false },
      { name: 'platform-admin', roles: [], admin: true, allowed: true },
    ]) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      const errors = [], managementCalls = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        localStorage.setItem('tp_token', 'mock-permission-test');
        localStorage.setItem('tp_last_project', '2');
        localStorage.setItem('tp_sidebar_collapsed', '0');
      });
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const url = new URL(route.request().url()), path = url.pathname;
        let data = [];
        if (path === '/api/auth/me') data = { user: { id: 1, name: '权限测试' }, is_platform_admin: !!scenario.admin,
          memberships: scenario.roles.map((role, i) => ({ project_id: i + 1, role })) };
        if (path === '/api/projects') data = [{ id: 1, name: '可操作项目' }, { id: 2, name: '游客项目' }];
        if (path === '/api/stats/overview') data = {};
        if (path === '/api/selectors/manage') { data = { shared: [], by_sub: {}, scope: {} }; managementCalls.push(url.searchParams.get('project_id')); }
        await route.fulfill({ json: { code: 0, data } });
      });
      for (const [path, label] of [['selectors', '选择器管理'], ['recorder', '录制脚本']]) {
        await page.goto(`${base}/${path}${path === 'selectors' ? '?project_id=2' : ''}`);
        if (!scenario.allowed) {
          await page.waitForURL('**/dashboard');
          for (const name of ['选择器管理', '录制脚本']) {
            await page.getByPlaceholder('查找功能').fill(name);
            await page.getByText('无匹配功能', { exact: true }).waitFor();
            assert.equal(await page.getByRole('menuitem', { name, exact: true }).count(), 0);
          }
          assert.deepEqual(managementCalls, []);
        } else {
          await page.getByRole('menuitem', { name: label, exact: true }).waitFor();
          const projectSelect = page.locator('.el-select').first();
          await projectSelect.click();
          await page.getByRole('option', { name: '可操作项目', exact: true }).waitFor();
          assert.equal(await page.getByRole('option', { name: '游客项目', exact: true }).count(), scenario.admin ? 1 : 0);
          await page.keyboard.press('Escape');
          if (!scenario.admin && path === 'selectors') assert(managementCalls.length && managementCalls.every(id => id === '1'));
          if (!scenario.admin) {
            await page.getByPlaceholder('查找功能').fill('用户管理');
            await page.getByText('无匹配功能', { exact: true }).waitFor();
          }
        }
      }
      assert.deepEqual(errors, [], scenario.name);
      await context.close();
      console.log(`PASS ${scenario.name}: menus, direct routes, project permissions`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
