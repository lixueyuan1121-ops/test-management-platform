// All API requests are mocked; no real tasks are dispatched.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const writes = [], errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    const queries = [
      { id: 1, title: '单轮推理', prompt: '推理', dimension: 'thinking' },
      { id: 3, title: '多轮续问', prompt: '续问', dimension: 'multi_turn', conversation_group: '旅行计划', turn_index: 1 },
      { id: 2, title: '多轮首问', prompt: '首问', dimension: 'multi_turn', conversation_group: '旅行计划', turn_index: 0 },
      { id: 4, title: '未标注题', prompt: '未标注' },
    ];
    const task = { id: 18, name: '回归专项', query_ids: [1, 2, 3], target_engines: ['namiwork'], status: 'done' };
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      let data = [];
      if (req.method() !== 'GET') writes.push({ path, body: req.postDataJSON() });
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'Test' }, is_platform_admin: true, memberships: [] };
      if (path.endsWith('/projects')) data = [{ id: 1, name: '测评项目' }];
      if (path.endsWith('/eval-tasks')) data = [task];
      if (path.endsWith('/ai/eval-queries')) data = queries;
      if (path.endsWith('/ai/eval-dimensions')) data = { dimensions: [{ key: 'thinking', label: '思考推理' }, { key: 'multi_turn', label: '多轮追问' }] };
      if (path.endsWith('/ai/eval-engines')) data = ['namiwork', 'workbuddy', 'qwork'].map((engine, i) => ({ engine, label: ['纳米Work', 'WorkBuddy', 'QWork'][i] }));
      if (path === '/api/devices') data = [{ name: '测试机', runner_id: 'runner-1' }];
      if (path.endsWith('/enqueue')) data = { run_ids: [1], batch_id: 'mock-batch' };
      await route.fulfill({ json: { code: 0, data } });
    });
    const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5189';
    await page.goto(`${base}/eval-tasks`);
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '编辑测评任务' });
    assert.equal(await editor.getByRole('columnheader', { name: '来源', exact: true }).count(), 0);
    await editor.getByRole('combobox', { name: '按维度筛选', exact: true }).click();
    await page.getByRole('option', { name: '多轮追问', exact: true }).click();
    const rows = editor.locator('.el-table__body-wrapper tbody tr');
    assert.equal(await rows.count(), 2);
    assert.match(await rows.nth(0).innerText(), /多轮首问[\s\S]*旅行计划[\s\S]*第 1 轮/);
    assert.match(await rows.nth(1).innerText(), /多轮续问[\s\S]*第 2 轮/);
    await editor.locator('.el-checkbox').filter({ has: page.getByRole('checkbox', { name: '全选筛选结果', exact: true }) }).click();
    await editor.getByRole('combobox', { name: '按维度筛选', exact: true }).click();
    await page.getByRole('option', { name: '未标注', exact: true }).click();
    assert.equal(await rows.count(), 1);
    await editor.locator('.el-checkbox').filter({ has: page.getByRole('checkbox', { name: '选择 未标注题', exact: true }) }).click();
    await page.screenshot({ path: '/tmp/eval-dimension-picker.png' });
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await editor.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.find(w => w.path === '/api/eval-tasks/18').body.query_ids, [1, 4]);
    await page.goto(`${base}/eval-library`);
    await page.locator('.el-table__body-wrapper .el-checkbox').first().click();
    for (const [label, engine] of [['WorkBuddy', 'workbuddy'], ['QWork', 'qwork'], ['纳米Work', 'namiwork']]) {
      await page.getByRole('button', { name: '配置并下发', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '下发测评用例' });
      await dialog.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '被测产品', exact: true }) }).click();
      await page.getByRole('option', { name: label, exact: true }).click();
      assert.equal(await dialog.getByRole('combobox', { name: '对话模式', exact: true }).count(), engine === 'namiwork' ? 1 : 0);
      assert.equal(await dialog.getByRole('combobox', { name: '目标设备', exact: true }).count(), engine === 'namiwork' ? 1 : 0);
      await dialog.getByRole('textbox', { name: '模型', exact: true }).fill(`${engine}-model`);
      await page.screenshot({ path: `/tmp/eval-dispatch-${engine}.png` });
      await dialog.getByRole('button', { name: '确认下发', exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(writes.at(-1).body.target_engine, engine);
      assert.equal(writes.at(-1).body.target_device, null);
      assert.deepEqual(writes.at(-1).body.dialog_options, { model: `${engine}-model` });
    }
    assert.deepEqual(errors, []);
    console.log('PASS dimension/unlabeled filtering, conversation order, selection persistence, three product dispatch payloads');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
