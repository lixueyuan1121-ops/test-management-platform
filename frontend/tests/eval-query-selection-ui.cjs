// Local fixtures only: 18 selected old/unlabeled cases behind 600 newer records.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [], saves = [], cursors = [];
    let failPage = false;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    const queries = Array.from({ length: 618 }, (_, i) => ({ id: 618 - i, title: `用例${618 - i}`, prompt: '测试提问', dimension: null }));
    const task = { id: 10, project_id: 1, name: '常用对话', query_ids: Array.from({ length: 18 }, (_, i) => i + 1),
      status: 'draft', target_engines: ['namiwork'] };
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测试项目' }];
      if (path.endsWith('/eval-tasks')) data = [task];
      if (path.endsWith('/eval-tasks/10') && req.method() === 'PATCH') { saves.push(req.postDataJSON()); data = task; }
      if (path.endsWith('/ai/eval-queries')) {
        const beforeId = Number(url.searchParams.get('before_id'));
        cursors.push(beforeId || null);
        if (failPage && beforeId) return route.abort('failed');
        data = queries.filter(q => !beforeId || q.id < beforeId).slice(0, Number(url.searchParams.get('limit')) || 200);
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5197'}/eval-tasks`);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '编辑测评任务' });
    await editor.getByText('共 618 条', { exact: true }).waitFor();
    await editor.getByText('已选 18', { exact: true }).click();
    assert.equal(await editor.locator('.el-table__body-wrapper tbody tr').count(), 18);
    assert(await editor.getByRole('checkbox', { name: '选择 用例1', exact: true }).isChecked());
    assert.equal(await editor.locator('.el-table__body-wrapper').getByText('未标注', { exact: true }).count(), 18);
    await editor.getByRole('textbox', { name: '搜索用例标题' }).fill('用例18');
    await editor.getByText('共 1 条', { exact: true }).waitFor();
    await editor.getByRole('textbox', { name: '搜索用例标题' }).clear();
    await editor.getByText('共 18 条', { exact: true }).waitFor();
    assert(await editor.getByRole('radio', { name: '已选 18', exact: true }).isChecked());
    await page.waitForTimeout(250); // 等待表格与标签过渡绘制完成，再检查截图。
    await page.screenshot({ path: '/tmp/eval-query-selected-18.png' });
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await editor.waitFor({ state: 'hidden' });
    assert.deepEqual(saves[0].query_ids, task.query_ids);
    assert.deepEqual(cursors, [null, 419, 219, 19]);

    failPage = true;
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    await editor.getByText(/用例加载失败/).waitFor();
    assert(await editor.getByRole('button', { name: '保存', exact: true }).isEnabled() === false);
    assert.equal(await editor.getByText(/共 \d+ 条/).count(), 0);
    assert.equal(await editor.getByText('已选 18', { exact: true }).count(), 1);
    failPage = false;
    await editor.getByRole('button', { name: '重新加载用例' }).click();
    await editor.getByText('共 618 条', { exact: true }).waitFor();
    await editor.getByText('已选 18', { exact: true }).click();
    await editor.getByText('共 18 条', { exact: true }).waitFor();
    await editor.getByRole('button', { name: '取消', exact: true }).click();

    queries.splice(queries.findIndex(q => q.id === 1), 1);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    await editor.getByText('有 1 条已选用例不在当前项目用例库中，原有勾选已保留', { exact: true }).waitFor();
    await editor.getByText('已选 18', { exact: true }).click();
    await editor.getByText('共 17 条', { exact: true }).waitFor();
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await editor.waitFor({ state: 'hidden' });
    assert.deepEqual(saves[1].query_ids, task.query_ids, '缺失项不能被静默移除');
    assert.deepEqual(errors, []);
    console.log('PASS: old 18 selected cases visible, unlabeled included, save preserved, paging failure/retry, missing ID warning');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
