const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5195';
const rule = {id:'R1',title:'保护目录删除确认',module:'文件',platform:'Windows',condition:'保护目录内有文件',action:'点击删除',expected:'弹出确认，取消后文件保留',source_type:'explicit',source_quote:'保护目录删除必须确认',source_section:'权限矩阵',source_material_ids:[],criteria:[{id:'R1-C1',text:'删除前确认'}],status:'confirmed',review_note:'',forbidden:'',boundaries:'',evidence:''};
const analysis = {id:10,project_id:1,task_id:2,revision:2,job_id:20,source_id:null,source_text:'保护目录删除必须确认',source_hash:'hash',source_title:'权限二期',source_url:'',source_info:{input_type:'text',warnings:[]},materials:[],visual_readings:[],baseline_id:40,draft:{summary:'保护用户文件，删除前确认',scope:'Windows',out_of_scope:'回收站下期',flow:'识别目录→确认→删除',rules:[rule],questions:[]}};
const candidate = {id:61,title:'保护目录删除与取消',steps:'点击删除，取消弹窗，检查文件',expected:'显示确认弹窗，文件保留',precondition:'准备保护目录文件',kind:'gui',platform:'web',hash:'snapshot',criterion_ids:['R1-C1'],reason:'避免误删除用户文件',reused:true,provenance:[{criterion_id:'R1-C1',baseline_id:39,source_criterion_id:'R1-C1'}]};
let mission = {id:1,project_id:1,task_id:2,goal:'验证文件权限二期，重点检查保护目录删除与取消',provider:'claude',analysis_id:10,baseline_id:40,phase:'awaiting_approval',paused:false,revision:4,policy:{max_cases:20},plan:{baseline_id:40,summary:'优先复用已核对用例，覆盖删除确认及取消保留',cases:[candidate],risks:['其他平台待验证'],missing_criteria:[]},events:[{id:1,message:'方案已准备好，请核对并授权执行',created_at:'2026-09-13T10:00:00'}],report:{total:1,verified:0,criteria:[],runs:[],pending_rules:[],summary:'仍有未验证内容',verdict:'needs_attention'},final_report:{}};
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1100},reducedMotion:'reduce'}), writes=[], errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>localStorage.setItem('tp_token','local-mock'));
    await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
      const req=route.request(),path=new URL(req.url()).pathname;
      const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON():null;
      if(req.method()!=='GET')writes.push({path,body});
      let data=[];
      if(path==='/api/auth/me')data={user:{id:1,name:'测试负责人'},is_platform_admin:true,memberships:[]};
      if(path==='/api/projects')data=[{id:1,name:'质量平台'},{id:2,name:'另一个项目'}];
      if(path==='/api/tasks')data=[{id:2,title:'权限二期测试',status:'pending'}];
      if(path==='/api/devices')data=[{id:5,runner_id:'test-mac',name:'授权测试机',platform:'web'}];
      if(path==='/api/ai/status')data={available:true,providers:[{id:'claude',available:true}]};
      if(path==='/api/ai/requirements/analyses')data=[analysis];
      if(path==='/api/ai/requirements/analyses/10')data=analysis;
      if(path==='/api/test-missions'){
        if(req.method()==='POST'){assert.equal(body.goal,'验证新的文件权限需求');assert.equal(body.requirement.task_id,2);data={...mission,id:2,goal:body.goal};}
        else data=[mission];
      }
      if(path==='/api/test-missions/1'||path==='/api/test-missions/2')data=mission;
      if(path==='/api/test-missions/1/decisions'){
        assert.equal(body.action,'approve');assert.equal(body.reviewed,true);assert.deepEqual(body.case_ids,[61]);assert.equal(body.runner,'test-mac');
        mission={...mission,revision:5,phase:'executing',policy:{...mission.policy,...body},events:[...mission.events,{id:2,message:'已授权执行',actor_id:1}]};data=mission;
      }
      if(path==='/api/test-missions/metrics')data={total:1,completed:1,ready_for_review:0,verified_criteria:0,total_criteria:1,avg_interventions:2,avg_duration_minutes:8,recorded_generation_cost_usd:null,metric_note:'按当前版本与证据统计',cost_note:'费用未完整采集，不估算总成本'};
      await route.fulfill({contentType:'application/json',body:JSON.stringify({code:0,msg:'ok',data})});
    });
    await page.goto(`${base}/commander?mission=1&project_id=1`);
    await page.getByRole('heading',{name:'测试方案',exact:true}).waitFor();
    const approve=page.getByRole('button',{name:'确认方案并开始执行',exact:true});assert.equal(await approve.isDisabled(),true);
    await page.getByText('核对前置、步骤、预期与来源',{exact:true}).click();
    await page.getByText('点击删除，取消弹窗，检查文件',{exact:true}).waitFor();
    await page.getByRole('combobox',{name:'执行设备',exact:true}).press('ArrowDown');
    await page.getByRole('option',{name:'授权测试机 · web',exact:true}).click();
    await page.getByText('我已核对所选用例的前置、步骤、预期及验收关联，授权在该设备和预算内执行',{exact:true}).click();
    await page.screenshot({path:'/tmp/test-mission-plan-desktop.png',fullPage:true});
    await approve.click();await page.getByText('正在等待执行机回传结果',{exact:true}).waitFor();
    await page.reload();await page.getByText('正在等待执行机回传结果',{exact:true}).waitFor();
    assert.equal(writes.filter(w=>w.path.endsWith('/decisions')).length,1);
    mission={...mission,revision:6,phase:'completed',report:{...mission.report,criteria:[{id:'R1-C1',text:'删除前确认',state:'flaky',run_ids:[72],source_quote:'保护目录删除必须确认',source_section:'权限矩阵'}],runs:[{id:71,case_id:61,status:'blocked',attempt:1,reason:'连接超时',triage:{kind:'environment',reason:'临时连接中断',suggestion:'复测'}},{id:72,case_id:61,status:'passed',attempt:2,retry_of:71,reason:'执行通过'}],summary:'仍有不稳定场景，需要核对原始失败'},events:[...mission.events,{id:3,message:'执行已收口，保留原始失败证据'}]};
    await page.getByRole('button',{name:'刷新',exact:true}).click();
    await page.getByRole('heading',{name:'质量结论',exact:true}).waitFor();
    await page.getByText('重试后通过',{exact:true}).waitFor();
    await page.getByText('执行记录、失败归因与重试链',{exact:true}).click();
    await page.getByText(/复测自 #71/).waitFor();
    await page.waitForTimeout(300); // Element Plus collapse transition
    await page.screenshot({path:'/tmp/test-mission-report-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('.aside').getBoundingClientRect().width < 66);
    await page.getByRole('heading',{name:'质量结论',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:'/tmp/test-mission-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true,'mobile page overflow');
    await page.setViewportSize({width:1440,height:1100});
    await page.getByRole('button',{name:'新建测试目标',exact:true}).click();
    await page.getByRole('textbox',{name:'测试目标描述',exact:true}).fill('验证新的文件权限需求');
    await page.getByRole('combobox',{name:'关联测试任务',exact:true}).press('ArrowDown');await page.getByRole('option',{name:'权限二期测试',exact:true}).click();
    await page.getByRole('textbox',{name:'需求正文',exact:true}).fill('保护目录删除必须确认');
    await page.getByRole('button',{name:'创建目标并开始分析',exact:true}).click();
    await page.getByRole('dialog',{name:'新建测试目标',exact:true}).waitFor({state:'hidden'});
    assert.equal(writes.filter(w=>w.path==='/api/test-missions').length,1);
    await page.goto(`${base}/ai-wall`);
    await page.getByRole('heading',{name:'AI 目标闭环成效',exact:true}).waitFor();
    await page.getByText('平均人工介入',{exact:true}).waitFor();
    await page.getByText('费用未完整采集，不估算总成本',{exact:false}).waitFor();
    await page.screenshot({path:'/tmp/test-mission-metrics.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('PASS: durable restore, explicit authorization, retry evidence, mobile layout, new goal input');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
