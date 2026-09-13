const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5195';
const c={id:61,title:'取消保护目录删除',hash:'original',kind:'gui',platform:'web',steps:'点击删除后取消',expected:'文件保留',precondition:'保护目录内有文件',criterion_ids:['R1-C1'],provenance:[],reason:'验证用户取消后的结果'};
const finding={kind:'missing_branch',reason:'只做了操作，缺少检查文件仍存在的步骤',criterion_ids:['R1-C1'],suggested_steps:'点击删除后取消，检查原文件仍存在',suggested_expected:'',suggested_precondition:''};
let mission={id:1,project_id:1,task_id:2,goal:'核验删除取消后的文件保留',phase:'awaiting_approval',revision:3,paused:false,policy:{max_cases:20},plan:{baseline_id:40,summary:'取消删除',cases:[c],quality:{version:'mission-quality-v1',status:'blocked',cases:[{case_id:61,case_hash:'original',checked_criterion_ids:['R1-C1'],findings:[finding]}]}},events:[],report:{total:1,verified:0,runs:[],criteria:[],pending_rules:[]},final_report:{}};
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'}),writes=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>localStorage.setItem('tp_token','mock'));
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let data=[];
   if(path==='/api/auth/me')data={user:{id:1,name:'测试负责人'},is_platform_admin:true,memberships:[]};
   if(path==='/api/projects')data=[{id:1,name:'质量平台'}];
   if(path==='/api/test-missions')data=[mission];
   if(path==='/api/test-missions/1')data=mission;
   if(path==='/api/test-missions/1/decisions'){
    const body=req.postDataJSON();writes.push(body);assert.equal(body.action,'apply_repair');assert.equal(body.repair_case_id,61);assert.equal(body.repair_finding_index,0);
    mission={...mission,revision:4,phase:'planning',plan:{},events:[{id:1,message:'保存为待采纳的修订副本，保留原用例'}]};data=mission;
   }
   if(path==='/api/test-missions/1/runs/72')data={id:72,status:'passed',report:[{action:'assert_text',ok:true,check:{actual:'文件已删除',expected:'文件保留',mode:'equals'}}]};
   await route.fulfill({json:{code:0,data}});
  });
  await page.goto(`${base}/commander?mission=1&project_id=1`);
  await page.getByRole('heading',{name:'独立用例审查',exact:true}).waitFor();
  assert(await page.getByRole('button',{name:'确认方案并开始执行',exact:true}).isDisabled());
  assert(await page.getByRole('checkbox',{name:'纳入本次执行',exact:true}).isDisabled());
  await page.getByText('对照修订建议',{exact:true}).click();
  await page.getByText('点击删除后取消，检查原文件仍存在',{exact:true}).waitFor();
  await page.waitForTimeout(300);
  await page.locator('.quality-review').screenshot({path:'/tmp/mission-p0-review.png'});
  await page.getByRole('button',{name:'应用此修订草稿',exact:true}).click();
  await page.getByText('AI 正在规划方案并独立检查用例质量',{exact:true}).waitFor();assert.equal(writes.length,1);
  mission={...mission,phase:'completed',revision:5,report:{total:1,verified:0,pending_rules:[],verdict:'needs_attention',summary:'证据与产品预期冲突',runs:[{id:72,status:'passed',attempt:1,has_report:true}],criteria:[{id:'R1-C1',text:'取消后文件保留',state:'contradicted',run_ids:[72],assessments:[{run_id:72,verdict:'contradicted',reason:'实际状态为文件已删除',refs:[{step_no:1,kind:'check',quote:'文件已删除'}]}]}]}};
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await page.getByText('实际状态为文件已删除',{exact:true}).first().waitFor();
  await page.getByRole('button',{name:'步骤 1 · 实际断言',exact:true}).first().click();
  await page.getByRole('dialog',{name:'执行证据',exact:true}).waitFor();
  await page.getByRole('dialog').getByText(/"actual": "文件已删除"/).waitFor();
  await page.locator('.el-dialog__headerbtn').click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('heading',{name:'质量结论',exact:true}).scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.locator('.report-card').screenshot({path:'/tmp/mission-p0-evidence-mobile.png'});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('PASS: blocked review, visible repair diff, explicit repair, actual conflicting evidence, responsive layout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
