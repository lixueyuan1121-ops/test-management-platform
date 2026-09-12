// Local-only acceptance for repeated execution, evidence checks and versioned judgments.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => localStorage.setItem('tp_token', 'mock-local-only'));
    const query = { id: 1, title: '公式验证', prompt: '生成Excel', expected: '包含公式', verification_rules: [] };
    const task = { id: 18, name: '稳定性评测', query_ids: [1], status: 'done', target_engines: ['namiwork'], last_batch_id: 'batch', dialog_options: {trial_count: 3} };
    const run = { run_id: 101, eval_query_id: 1, batch_id: 'batch', target_engine: 'namiwork', status: 'judged', verdict: 'fail', judgment_id: 8,
      payload: {title: query.title, prompt: query.prompt, trial_index: 1, trial_count: 3}, verdict_dims: {artifact_verification:{status:'fail',checks:[{status:'fail',name:'结果.xlsx',reason:'单元格B1没有公式',sha256:'abc'}]}} };
    const metrics = { pass_rate: 100, coverage_rate: 10, confirmed_success_rate: 10, completed: 10, total: 10, config_errors: 0, execution_errors: 0, judge_errors: 9 };
    const experiment = {manifest: {trial_count: 3, dataset_hash:'dataset-v1'}, metrics, trial_metrics:{
      by_engine_variant:[{engine:'namiwork',variant:'A',task_count:1,success_rate:33.3,coverage_rate:100,mean_score:3}],
      tasks:[{engine:'namiwork',case:'q1',variant:'A',success_rate:33.3,score_min:1,score_max:5,attempts:[{trial_index:1,verdict:'pass'},{trial_index:2,verdict:'fail'},{trial_index:3,verdict:'fail'}]}]}};
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req=route.request(), path=new URL(req.url()).pathname;
      let data=[];
      if (req.method() !== 'GET') writes.push({path,body:req.postDataJSON()});
      if (path.endsWith('/auth/me')) data={user:{id:1,name:'Test'},is_platform_admin:true,memberships:[]};
      if (path.endsWith('/projects')) data=[{id:1,name:'测试项目'}];
      if (path.endsWith('/eval-tasks')) data=[task];
      if (path.endsWith('/ai/eval-queries')) data=[query];
      if (path.endsWith('/devices')) data=[{name:'测试机',runner_id:'r1'}];
      if (path.endsWith('/eval-tasks/18/runs')) data={task,runs:[run],experiment};
      if (path.endsWith('/eval-tasks/18/run')) data={run_ids:[1,2,3],batch_id:'b2'};
      if (path.endsWith('/ai/eval-queries/1')) data={...query,...req.postDataJSON()};
      if (path.endsWith('/eval-queue/history')) data=[run];
      if (path.endsWith('/dimension-stats')) data={dims:[],judged_total:1,overall_rate:0};
      if (path.endsWith('/eval-queue/trend')) data={batches:[]};
      if (path.endsWith('/judge-quality')) data={overall:{reviewed:0},by_engine:[]};
      if (path.endsWith('/eval-judge/101/judgments')) data=[{id:8,attempt:1,provider:'claude',rules_version:'context-v2',input_hash:'hash',input:{prompt:'原始提问'},ballots:[{raw_output:'第一票'}],result:{verdict:'fail',verdict_reason:'产物缺公式'},reviews:[{mark:'confirmed',note:'人工确认',created_at:'2026-09-12T10:00:00'}]}];
      await route.fulfill({json:{code:0,data}});
    });
    const base=process.env.UI_BASE_URL || 'http://127.0.0.1:5194';
    await page.goto(`${base}/eval-tasks`);
    await page.getByRole('button',{name:'稳定性评测',exact:true}).click();
    await page.locator('.experiment-overview').waitFor();
    assert.match(await page.locator('.experiment-overview').innerText(), /判定覆盖率\s*10%/);
    await page.getByText('逐题重复执行与得分波动',{exact:true}).click();
    await page.getByText('1: 通过；2: 未通过；3: 未通过',{exact:true}).waitFor();
    await page.screenshot({path:'/tmp/eval-reliability-overview.png',fullPage:true});
    await page.getByRole('button',{name:'Close this dialog'}).click();
    await page.getByRole('button',{name:'执行',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'执行测评任务'});
    assert.equal(await dialog.locator('.el-input-number input').inputValue(),'3');
    await dialog.locator('.el-switch').first().click();
    await dialog.getByRole('button',{name:'下发执行',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    assert.equal(writes.find(w=>w.path.endsWith('/18/run')).body.trial_count,3);
    await page.goto(`${base}/eval-library`);
    await page.getByRole('button',{name:'公式验证',exact:true}).click();
    await page.getByRole('button',{name:'添加产物检查'}).click();
    await page.getByRole('button',{name:'保存产物检查'}).click();
    await page.getByText('产物检查已保存，后续执行生效',{exact:true}).waitFor();
    assert.deepEqual(writes.find(w=>w.path.endsWith('/eval-queries/1')).body.verification_rules,[{kind:'file_readable',file_pattern:'*.xlsx'}]);
    await page.goto(`${base}/eval-results`);
    await page.locator('.case-link').first().click();
    await page.getByRole('heading',{name:'实际产物核验'}).waitFor();
    await page.getByRole('tab',{name:'判定历史',exact:true}).click();
    await page.getByRole('button',{name:/判定 #8/}).click();
    await page.getByText('人工确认',{exact:true}).waitFor();
    await page.getByText('当次判定输入',{exact:true}).click();
    await page.getByText('独立票据（1 票）',{exact:true}).click();
    await page.waitForTimeout(350);
    await page.screenshot({path:'/tmp/eval-reliability-history.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS: trial count dispatch, coverage, task-balanced stability, artifact rules, versioned judgments, raw ballots');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
