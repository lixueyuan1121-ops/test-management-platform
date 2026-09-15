const { test, before, after, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/eval/node_modules/playwright');

const baseUrl = process.env.UI_BASE_URL || 'http://127.0.0.1:5197';
const items = [
  { id: 1, title: '快速模式选项', key: 'homeModeQuickOption', expected: '快速模式' },
  { id: 2, title: '深度模式选项', key: 'homeModeDeepOption', expected: '深度模式' },
].map(item => ({
  ...item, project_id: 1, page: 'home', sub_product: '桌面版', steps: '打开模式选择',
  exec_kind: 'manual', review_status: 'pending', selector_fix: true, selector_fix_keys: [item.key],
  script: JSON.stringify([{ action: 'assert_text', target: { key: item.key }, args: { expected: item.expected } }]),
}));

let browser, page, errors, reads;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.setDefaultTimeout(5000);
  errors = []; reads = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    assert.equal(request.method(), 'GET', '导航测试只允许读取虚构数据');
    let data = [];
    if (path === '/api/projects') data = [{ id: 1, name: '导航测试项目' }];
    if (path === '/api/selectors/manage') data = { shared: [], by_sub: {} };
    // 列表和真实 API 一样不带 script，断言线索必须读取详情后得到。
    if (path === '/api/ai/cases') data = { items: items.map(({ script, ...row }) => row), total: items.length };
    const match = path.match(/^\/api\/ai\/testcases\/(\d+)$/);
    if (match) { reads.push(Number(match[1])); data = items.find(item => item.id === Number(match[1])); }
    await route.fulfill({ json: { code: 0, data } });
  });
  await page.goto(`${baseUrl}/tests/fixtures/selector-navigation.html`);
  await page.getByRole('button', { name: '定位缺失 key', exact: true }).first().waitFor({ timeout: 30000 });
});
afterEach(async () => { await page?.close(); });

async function query() {
  const output = page.getByTestId('selector-query');
  await output.waitFor().catch(error => {
    assert.deepEqual(errors, [], '页面不应抛出运行时异常');
    throw error;
  });
  return JSON.parse(await output.textContent());
}

test('单条定位正常跳转，携带详情中的预期文案及项目/页面/子产品', async () => {
  await page.getByRole('button', { name: '定位缺失 key', exact: true }).first().click();
  const params = await query();
  assert.deepEqual(reads, [1]);
  assert.equal(params.project_id, '1');
  assert.equal(params.page, 'home');
  assert.equal(params.sub_product, '桌面版');
  assert.equal(params.case_ids, '1');
  assert.equal(params.fix_keys, 'homeModeQuickOption');
  assert.deepEqual(JSON.parse(params.key_hints), { homeModeQuickOption: { expectedTexts: ['快速模式'] } });
  assert.match(JSON.parse(params.key_contexts).homeModeQuickOption, /快速模式/);
  assert.deepEqual(errors, []);
});

test('批量补选择器传递每个 key 的预期文案，目标匹配拒绝含相同词的整页', async () => {
  await page.locator('.el-table__header-wrapper label').filter({ has: page.getByRole('checkbox') }).click();
  await page.getByRole('button', { name: /批量补选择器/ }).click();
  await page.getByRole('button', { name: '去探测补齐', exact: true }).click();
  const params = await query();
  assert.deepEqual(reads.sort(), [1, 2]);
  assert.equal(params.case_ids, '1,2');
  assert.equal(params.bulk, '1');
  assert.equal(params.sub_product, '桌面版');
  const hints = JSON.parse(params.key_hints);
  assert.deepEqual(hints, {
    homeModeQuickOption: { expectedTexts: ['快速模式'] },
    homeModeDeepOption: { expectedTexts: ['深度模式'] },
  });
  const { matchElementsToKeys } = await import('../src/utils/bulk-fix-selectors.js');
  const elements = [
    { text: '首页 快速模式 深度模式', candidates: [{ by: 'testid', value: 'home-mode-quick-option-page' }] },
    { text: '快速模式', candidates: [{ by: 'testid', value: 'choice-1' }] },
    { text: '深度模式', candidates: [{ by: 'testid', value: 'choice-2' }] },
  ];
  const pairs = matchElementsToKeys(params.fix_keys.split(','), JSON.parse(params.key_contexts), elements, hints);
  assert.deepEqual(pairs.map(pair => pair.el?.text), ['快速模式', '深度模式']);
  assert.deepEqual(errors, []);
});
