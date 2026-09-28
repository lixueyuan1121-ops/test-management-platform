const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/eval/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[],releaseRequests=[];let saved=0,failSave=false;
  let task={id:1,project_id:1,title:'多人协作回归任务',description:'多人协作回归任务',assigned_to:2,assigned_to_ids:[2],assigned_to_name:'成员甲',assigned_date:'2026-09-28',priority:'p2',status:'pending'};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('tp_token','isolated-ui-test'));
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;let data=[];
   if(path==='/api/auth/me')data={user:{id:1,name:'管理员'},is_platform_admin:true,memberships:[]};
   if(path==='/api/projects')data=[{id:1,name:'回归项目',platform_type:'pc'},{id:2,name:'其他项目',platform_type:'pc'}];
   if(path==='/api/projects/1/members'||path==='/api/projects/2/members')data=[{user_id:2,name:'成员甲',username:'a'},{user_id:3,name:'成员乙',username:'b'}];
   if(path==='/api/releases'){
    const offset=Number(url.searchParams.get('offset')),limit=Number(url.searchParams.get('limit'));releaseRequests.push({offset,limit,project:url.searchParams.get('project_id'),sub:url.searchParams.get('sub_product')});
    await new Promise(r=>setTimeout(r,100));data={total:35,items:Array.from({length:Math.max(0,Math.min(limit,35-offset))},(_,i)=>({id:offset+i+1,version:`version-${offset+i+1}`,release_date:'2026-09-28',req_count:1,content:'测试版本'}))};
   }
   if(path==='/api/releases/stats')data={total_releases:35,total_reqs:35,this_month:35,trend:[]};
   if(path==='/api/releases/quality')data={items:[]};
   if(path==='/api/tasks/checklist-summary')data={};
   if(path==='/api/tasks'&&req.method()==='GET')data=[task];
   if(path==='/api/tasks/1'&&req.method()==='PATCH'){
    saved++;if(failSave)return route.fulfill({status:500,json:{msg:'模拟保存失败'}});
    const body=req.postDataJSON();assert(Array.isArray(body.assigned_to_ids));task={...task,...body,assigned_to:body.assigned_to_ids[0],assigned_to_name:body.assigned_to_ids.map(x=>x===2?'成员甲':'成员乙').join('、')};data=task;
   }
   await route.fulfill({json:{code:0,data}});
  });
  const base=process.env.UI_BASE_URL||'http://127.0.0.1:5196';
  await page.goto(base+'/releases');await page.getByText('version-1',{exact:true}).waitFor();
  await page.locator('.el-pager li').filter({hasText:/^2$/}).click();await page.getByText('version-21',{exact:true}).waitFor();
  assert.equal(releaseRequests.at(-1).offset,20);assert.equal(await page.locator('.el-pager li.is-active').innerText(),'2');
  assert.equal(await page.locator('.el-table__body tbody tr').count(),15);
  await page.locator('.btn-prev').click();await page.getByText('version-1',{exact:true}).waitFor();assert.equal(releaseRequests.at(-1).offset,0);
  await page.locator('.btn-next').click();await page.getByText('version-21',{exact:true}).waitFor();
  await page.getByText('纳米Work桌面版',{exact:true}).click();await page.getByText('version-1',{exact:true}).waitFor();assert.equal(releaseRequests.at(-1).offset,0);
  await page.locator('.el-pagination .el-select').click();await page.getByRole('option',{name:/50/}).click();
  await page.getByText('version-35',{exact:true}).waitFor();assert.equal(releaseRequests.at(-1).limit,50);assert.equal(await page.locator('.el-table__body tbody tr').count(),35);
  await page.goto(base+'/tasks');await page.getByText('多人协作回归任务',{exact:true}).waitFor();
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  const dialog=page.getByRole('dialog'),assignees=dialog.locator('.el-form-item').filter({hasText:'指派给'}).locator('.el-select');
  await assignees.click();await page.getByRole('option',{name:'成员乙 (b)',exact:true}).click();await dialog.locator('textarea').click();
  await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.deepEqual(task.assigned_to_ids,[2,3]);
  await page.reload();await page.getByText('成员甲、成员乙',{exact:true}).waitFor();await page.getByRole('button',{name:'编辑',exact:true}).click();
  assert.equal(await assignees.locator('.el-tag').count(),2);
  await assignees.click();await page.getByRole('option',{name:'成员甲 (a)',exact:true}).click();await dialog.locator('textarea').click();
  await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.deepEqual(task.assigned_to_ids,[3]);
  await page.getByRole('button',{name:'编辑',exact:true}).click();await assignees.click();await page.getByRole('option',{name:'成员乙 (b)',exact:true}).click();await dialog.locator('textarea').click();
  const before=saved;await dialog.getByRole('button',{name:'保存',exact:true}).click();await page.getByText('任务名称/指派/日期必填',{exact:true}).waitFor();assert.equal(saved,before);assert(await dialog.isVisible());
  await assignees.click();await page.getByRole('option',{name:'成员甲 (a)',exact:true}).click();await dialog.locator('textarea').click();failSave=true;
  await dialog.getByRole('button',{name:'保存',exact:true}).click();await page.getByText('模拟保存失败',{exact:true}).waitFor();assert(await dialog.isVisible());
  assert.deepEqual(errors,[]);console.log('PASS: page 2/previous/next/filter reset/size; multi-select persistence/reopen/remove/empty/save-failure');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
