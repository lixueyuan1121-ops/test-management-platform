const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5195';
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4XcAAAAASUVORK5CYII=', 'base64');
const rule = { id: 'R1', title: '保护目录删除确认', module: '文件管理', platform: 'Windows', condition: '文件在保护目录', action: '点击删除', expected: '显示确认弹窗', forbidden: '直接删除', boundaries: '', evidence: '', source_type: 'explicit', source_quote: '保护目录必须确认', source_section: '权限矩阵', source_material_ids: ['IMG1'], criteria: [{ id: 'R1-C1', text: '保护目录删除前必须显示确认弹窗' }], status: 'pending', review_note: '' };
const material = { id: 'IMG1', location: '权限矩阵', mime_type: 'image/png' };
const analysis = { id: 10, project_id: 1, task_id: 2, revision: 1, job_id: 20, source_id: 30, source_text: '保护目录必须确认', source_hash: 'test-hash', source_title: '权限需求.docx', source_url: '', source_info: { input_type: 'file', warnings: [] }, materials: [material], visual_readings: [{ ...material, status: 'uncertain', text: '|目录|删除行为|\n|---|---|\n|保护目录|必须确认|', uncertainties: '脚注文字不清' }], draft: { summary: '删除前确认', scope: 'Windows', out_of_scope: '不涉及 Mac', flow: '判断目录→确认→删除', rules: [rule], questions: [{ id: 'Q1', question: '保护目录是否包含子目录？', evidence: '图中脚注不清', options: ['包含', '不包含'], rule_ids: ['R1'], blocking: true, answer: '' }] } };

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
    const writes = [], errors = [];
    let adopted = false, analysisStarted = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const body = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
      if (request.method() !== 'GET') writes.push({ path, body });
      let data = [];
      const links = [{ baseline_id: 40, rule_id: 'R1', criterion_id: 'R1-C1', text: rule.criteria[0].text, rule_title: rule.title, source_section: rule.source_section, source_quote: rule.source_quote }];
      const cases = [{ id: 61, title: '保护目录删除弹窗', steps: '点击保护目录文件的删除按钮', expected: '显示确认弹窗', precondition: '准备保护目录下的文件', review_status: adopted ? 'adopted' : 'pending', acceptance_links: links }];
      if (path === '/api/auth/me') data = { user: { id: 1, name: '测试员' }, is_platform_admin: true, memberships: [] };
      if (path === '/api/projects') data = [{ id: 1, name: '测试项目' }];
      if (path === '/api/tasks') data = [{ id: 2, title: '功能测试任务', status: 'pending' }];
      if (path === '/api/ai/status') data = { available: true, default: 'claude', providers: [{ id: 'claude', available: true }] };
      if (path === '/api/ai/extract-file') data = { source_id: 30, text: analysis.source_text, title: analysis.source_title, filename: analysis.source_title, chars: 9, materials: [material], warnings: [] };
      if (path === '/api/ai/requirements/analyses') data = analysisStarted ? [{ ...analysis, draft: undefined, status: 'done' }] : [];
      if (path === '/api/ai/requirements/analyze') { analysisStarted = true; data = { analysis_id: 10, job_id: 20 }; }
      if (path === '/api/ai-jobs/20') data = { id: 20, status: 'done', result: { analysis_id: 10 } };
      if (path === '/api/ai/requirements/analyses/10') {
        if (request.method() === 'PATCH') { analysis.draft = structuredClone(body.draft); analysis.revision++; analysis.baseline_id = null; }
        data = analysis;
      }
      if (path.endsWith('/sources/30/images/IMG1')) {
        assert.equal(request.headers().authorization, 'Bearer mock-local-only');
        return route.fulfill({ contentType: 'image/png', body: pixel });
      }
      if (path === '/api/ai/requirements/analyses/10/confirm') { analysis.baseline_id = 40; analysis.confirmation_note = body.confirmation_note; data = { baseline_id: 40, revision: analysis.revision }; }
      if (path === '/api/ai/testcases') data = { job_id: 21, ai_task_id: 50 };
      if (path === '/api/ai-jobs/21') data = { id: 21, status: 'done', result: { ai_task_id: 50, status: 'done', case_count: 1 } };
      if (path === '/api/ai/tasks/50/cases') data = cases;
      if (path === '/api/ai/requirements/coverage/50') data = { baseline_id: 40, total: 1, linked: 1, reviewed: Number(adopted), pending_rules: ['R2'], excluded_rules: [{ id: 'R3', reason: '下期实现' }], unlinked_case_ids: [], criteria: [{ criterion_id: 'R1-C1', text: rule.criteria[0].text, case_ids: [61], state: adopted ? 'reviewed' : 'pending' }] };
      if (path === '/api/ai/testcases/61') { adopted = body.review_status === 'adopted'; data = { ...cases[0], review_status: body.review_status, adopted }; }
      await route.fulfill({ json: { code: 0, data } });
    });
    await page.goto(`${base}/ai-testgen`);
    await page.locator('.form-row .el-select').filter({ hasText: '关联任务（必填）' }).getByRole('combobox').click();
    await page.getByRole('option', { name: /功能测试任务/ }).click();
    const generate = page.getByRole('button', { name: '按已确认规则生成测试点', exact: true });
    assert(await generate.isDisabled());
    await page.getByRole('button', { name: '上传文档', exact: true }).click();
    await page.locator('.upload-row input[type="file"]').setInputFiles({ name: '权限需求.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: Buffer.from('mock-document') });
    await page.getByText('保护目录必须确认', { exact: true }).waitFor();
    await page.getByRole('button', { name: '分析需求', exact: true }).click();
    await page.getByRole('tab', { name: '图片与图表（1）', exact: true }).click();
    await page.getByText('脚注文字不清', { exact: true }).last().waitFor();
    await page.getByRole('button', { name: '对照原图', exact: true }).click();
    const image = page.getByAltText('需求原图', { exact: true });
    await image.waitFor();
    assert((await image.getAttribute('src')).startsWith('blob:'));
    await page.locator('.el-dialog__headerbtn').click();
    await page.locator('.el-dialog').waitFor({ state: 'hidden' });
    await page.getByRole('tab', { name: '验收规则（1）', exact: true }).click();
    await page.getByRole('button', { name: '保护目录删除确认', exact: true }).click();
    let drawer = page.getByRole('dialog', { name: '编辑验收规则', exact: true });
    await drawer.getByText('确认本期验收', { exact: true }).click();
    await drawer.getByRole('button', { name: '完成编辑', exact: true }).click();
    await drawer.waitFor({ state: 'hidden' });
    const confirm = page.getByRole('button', { name: '确认验收规则', exact: true });
    assert(await confirm.isDisabled());
    await page.getByRole('tab', { name: '澄清问题（1 待处理）', exact: true }).click();
    await page.getByRole('textbox', { name: 'Q1 处理结论', exact: true }).fill('包含子目录');
    await page.getByRole('tab', { name: '验收规则（1）', exact: true }).click();
    await page.getByRole('button', { name: '保护目录删除确认', exact: true }).click();
    drawer = page.getByRole('dialog', { name: '编辑验收规则', exact: true });
    await drawer.locator('.el-form-item').filter({ hasText: '评审说明 / 本期排除原因' }).locator('textarea').fill('已向产品核实，保护目录包含子目录');
    await drawer.getByText('确认本期验收', { exact: true }).click();
    await drawer.getByRole('button', { name: '完成编辑', exact: true }).click();
    await drawer.waitFor({ state: 'hidden' });
    await page.getByRole('textbox', { name: '验收确认说明', exact: true }).fill('已核实图片脚注：包含子目录');
    await page.getByText('我已核对本期范围、资料完整性及选中的验收规则', { exact: true }).click();
    await confirm.click();
    await page.getByText('已确认版本 #40', { exact: true }).waitFor();
    assert(!await generate.isDisabled());
    const confirmation = writes.find(w => w.path.endsWith('/confirm')).body;
    assert.equal(confirmation.revision, 2); assert.equal(confirmation.source_hash, 'test-hash');
    await generate.click();
    await page.getByRole('button', { name: '保护目录删除弹窗', exact: true }).waitFor();
    assert.equal(writes.find(w => w.path === '/api/ai/testcases').body.baseline_id, 40);
    await page.getByText(/人工核验 0\/1/).waitFor();
    await page.getByRole('button', { name: '保护目录删除弹窗', exact: true }).click();
    drawer = page.locator('.generation-detail');
    assert.match(await drawer.innerText(), /R1-C1/);
    assert.match(await drawer.innerText(), /准备保护目录下的文件/);
    await drawer.getByText('采纳', { exact: true }).click();
    await page.getByText(/人工核验 1\/1/).waitFor();
    await drawer.locator('.el-drawer__close-btn').click();
    await drawer.waitFor({ state: 'hidden' });
    await page.getByText('生成配置', { exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.requirement-review').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/tmp/requirement-review-mobile.png' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.getByText('编辑', { exact: true }).click();
    await page.locator('textarea.req-input, .req-input textarea').fill('保护目录规则已修改');
    assert(await generate.isDisabled());
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.getByText('恢复历史需求分析', { exact: true }).click();
    await page.getByRole('option', { name: /#10/ }).click();
    await page.getByText('已确认版本 #40', { exact: true }).waitFor();
    assert(!await generate.isDisabled());
    await page.locator('.requirement-review').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/tmp/requirement-review-desktop.png' });
    assert.deepEqual(errors, []);
    console.log('Requirement review UI passed: images, clarification, confirmation, coverage, stale input, restore, mobile.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
