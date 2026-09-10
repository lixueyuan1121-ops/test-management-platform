const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    let failRead = true, failParse = true, failOpen = true, failSave = true, holdOld = false, held;
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname;
      let data = [];
      const fail = () => route.fulfill({ status: 500, json: { msg: '模拟环境操作失败' } });
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '项目一' }, { id: 2, name: '项目二' }];
      if (path === '/api/api-env' && req.method() === 'GET') {
        if (holdOld && url.searchParams.get('project_id') === '1') { held = route; return; }
        if (failRead) return fail();
        data = { base_url: '', auth_type: 'fixed', auth: {}, contract: `GET /project${url.searchParams.get('project_id')}` };
      }
      if (req.method() !== 'GET') {
        writes.push({ path, body: req.postDataJSON() });
        if (path === '/api/api-env' && failSave) return fail();
        if (path === '/api/api-env/parse-curl') {
          if (failParse) return fail();
          data = { parsed: { method: 'GET', path: '/users', base_url: 'https://example.test', stripped_auth: ['Authorization'] }, contract_line: 'GET /users', script_seed: { steps: [] } };
        }
        if (path === '/api/api-env/import-openapi') { if (failOpen) return fail(); data = { base_url: 'https://schema.test', contract: 'GET /pets', count: 1 }; }
      }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${process.env.UI_BASE_URL || 'http://127.0.0.1:5189'}/api-env`);
    await page.getByText('API 环境加载失败', { exact: true }).waitFor();
    assert(await page.getByRole('button', { name: '保存', exact: true }).isDisabled());
    failRead = false;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    const contract = page.locator('.env-form textarea').last();
    await contract.waitFor();
    await page.getByRole('button', { name: '粘贴 curl 解析', exact: true }).click();
    let dialog = page.locator('.el-dialog').filter({ hasText: '粘贴 curl 解析' });
    const curl = 'curl https://example.test/users';
    await dialog.locator('textarea').first().fill(curl);
    const failed = page.waitForResponse(r => r.url().endsWith('/parse-curl') && r.status() === 500);
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await failed;
    assert.equal(await dialog.locator('textarea').first().inputValue(), curl);
    failParse = false;
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await dialog.getByText('GET /users', { exact: true }).last().waitFor();
    assert.deepEqual(writes.at(-1).body, { curl });
    await dialog.locator('textarea').first().fill(`${curl} -v`);
    assert(await dialog.getByRole('button', { name: '并入契约', exact: true }).isDisabled());
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await dialog.getByRole('button', { name: '并入契约', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await contract.inputValue(), 'GET /project1\nGET /users');
    assert.equal(await page.getByLabel('base_url', { exact: true }).inputValue(), 'https://example.test');
    await page.getByRole('button', { name: '导入 OpenAPI/Swagger', exact: true }).click();
    dialog = page.locator('.el-dialog').filter({ hasText: '导入 OpenAPI/Swagger' });
    const spec = '{"openapi":"3.0.0","paths":{}}';
    await dialog.locator('textarea').first().fill(spec);
    const importFailed = page.waitForResponse(r => r.url().endsWith('/import-openapi') && r.status() === 500);
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await importFailed;
    assert.equal(await dialog.locator('textarea').first().inputValue(), spec);
    failOpen = false;
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await dialog.getByRole('button', { name: '替换契约', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.deepEqual(writes.at(-1).body, { project_id: 1, spec });
    assert.equal(await contract.inputValue(), 'GET /pets');
    await page.getByRole('button', { name: '导入 OpenAPI/Swagger', exact: true }).click();
    await dialog.locator('textarea').first().fill(spec);
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await dialog.locator('textarea').nth(1).waitFor();
    await dialog.locator('textarea').first().fill(`${spec} `);
    assert(await dialog.getByRole('button', { name: '追加到契约', exact: true }).isDisabled());
    await dialog.getByRole('button', { name: '解析', exact: true }).click();
    await dialog.locator('textarea').nth(1).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    const box = await dialog.boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
    await page.screenshot({ path: '/tmp/api-openapi-mobile.png', fullPage: true });
    await dialog.getByRole('button', { name: '追加到契约', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await contract.inputValue(), 'GET /pets\nGET /pets');
    await page.setViewportSize({ width: 1440, height: 1000 });
    const saveFailed = page.waitForResponse(r => r.url().endsWith('/api/api-env') && r.request().method() === 'PUT' && r.status() === 500);
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await saveFailed;
    assert.equal(await contract.inputValue(), 'GET /pets\nGET /pets');
    failSave = false;
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('已保存', { exact: true }).waitFor();
    assert.deepEqual(writes.at(-1).body, { project_id: 1, base_url: 'https://example.test', auth_type: 'fixed', auth: {}, contract: 'GET /pets\nGET /pets' });
    await page.getByRole('button', { name: '重置', exact: true }).click();
    await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await contract.inputValue(), 'GET /pets\nGET /pets');
    const select = async name => { await page.locator('.el-select').first().click(); await page.getByRole('option', { name, exact: true }).click(); };
    await select('项目二');
    await page.waitForFunction(() => document.querySelector('.env-form textarea:last-of-type') && [...document.querySelectorAll('.env-form textarea')].at(-1).value === 'GET /project2');
    holdOld = true;
    await select('项目一');
    await page.locator('.el-loading-mask').waitFor();
    await select('项目二');
    await page.waitForFunction(() => [...document.querySelectorAll('.env-form textarea')].at(-1)?.value === 'GET /project2');
    assert(held);
    await held.fulfill({ json: { code: 0, data: { contract: '旧项目秘密配置', auth: {} } } });
    await page.waitForTimeout(250);
    assert.equal(await contract.inputValue(), 'GET /project2');
    assert.deepEqual(errors, []);
    console.log('PASS API env read/save retry, curl input retention and invalidation, OpenAPI replacement, exact payloads, reset cancellation and project isolation');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
