const { chromium } = require(process.env.PLAYWRIGHT_PATH || '../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}), errors=[];
  const rows=Array.from({length:6},(_,i)=>({id:i+1,key:`control_${i}`,page:['安全设置','部门','技能'][Math.floor(i/2)],frame:i===5?'shell':'vm',platform:'web',desc:`[技能]-[页面${i}]-[场景]-[按钮${i}]`,change_status:['new','updated','retired'][i%3],candidates:[{by:'testid',value:`test-${i}`},{by:'css',value:`.old-${i}`,disabled:true,status:'retired'}],revision:`rev-${i}`}));
  const other={...rows[0],id:100,key:'other_project'};
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('tp_token','mock-local-only'));
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
   const u=new URL(route.request().url());let data=[];
   if(u.pathname==='/api/auth/me')data={user:{id:1,name:'测试员'},is_platform_admin:true,memberships:[]};
   if(u.pathname==='/api/projects')data=[{id:1,name:'项目一'},{id:2,name:'项目二'}];
   if(u.pathname==='/api/selectors/manage')data={shared:u.searchParams.get('project_id')==='2'?[other]:rows,by_sub:{},scope:{vm_iframe:'#app-frame'}};
   await route.fulfill({json:{code:0,data}});
  });
  await page.goto(`${process.env.UI_BASE_URL||'http://127.0.0.1:8016'}/selectors?project_id=1`);
  const bar=page.getByRole('toolbar',{name:'选择器批量操作'}),all=bar.getByRole('checkbox',{name:'当前页全选',exact:true});
  await bar.getByText('已选 0 / 6',{exact:true}).waitFor();
  assert.equal(await page.locator('.module-card').count(),0);
  const allLabel=bar.locator('label.el-checkbox').filter({hasText:'当前页全选'});
  const tables=page.locator('.el-table').filter({has:page.getByRole('columnheader',{name:'状态',exact:true,includeHidden:true})});
  await tables.first().locator('thead .el-checkbox').click();
  await bar.getByText('已选 2 / 6',{exact:true}).waitFor();
  assert(await all.evaluate(el=>el.indeterminate || el.getAttribute('aria-checked')==='mixed'));
  await page.locator('.el-collapse-item__header').filter({hasText:'技能'}).click();
  await allLabel.click();await bar.getByText('已选 6 / 6',{exact:true}).waitFor();
  assert.equal(await tables.locator('tbody input[type=checkbox]:checked').count(),6);
  await allLabel.click();await bar.getByText('已选 0 / 6',{exact:true}).waitFor();
  assert.equal(await tables.locator('tbody input[type=checkbox]:checked').count(),0);
  await tables.first().locator('tbody .el-checkbox').first().click();
  await bar.getByText('已选 1 / 6',{exact:true}).waitFor();
  const downloaded=page.waitForEvent('download');await bar.getByRole('button',{name:'全部导出',exact:true}).click();
  const download=await downloaded;assert(download.suggestedFilename().endsWith('.json'));
  const payload=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(payload.project_id,1);assert.equal(payload.sub_product,'');assert.equal(payload.vmIframe,'#app-frame');assert.equal(Object.keys(payload.registry).length,6);
  for(const row of rows)assert.deepEqual(payload.registry[row.key],{frame:row.frame,page:row.page,desc:row.desc,platform:row.platform,change_status:row.change_status,candidates:row.candidates});
  await download.saveAs('../backend/artifacts/selector-management/export-ui-fixture.json');
  await allLabel.click();await bar.getByText('已选 6 / 6',{exact:true}).waitFor();
  await page.locator('.el-select').filter({hasText:'项目一'}).first().click();await page.getByRole('option',{name:'项目二',exact:true}).click();
  await bar.getByText('已选 0 / 1',{exact:true}).waitFor();assert(!(await all.isChecked()));
  const nextDownload=page.waitForEvent('download');await bar.getByRole('button',{name:'全部导出',exact:true}).click();const second=await nextDownload;
  const secondPayload=JSON.parse(await fs.readFile(await second.path(),'utf8'));assert.equal(secondPayload.project_id,2);assert.deepEqual(Object.keys(secondPayload.registry),['other_project']);
  assert.deepEqual(errors,[]);
  console.log('PASS cross-group select all/clear/partial selection, collapsed groups, complete JSON download independent of selection, project isolation');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
