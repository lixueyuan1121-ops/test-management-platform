const { chromium } = require(process.env.PLAYWRIGHT_PATH || '../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async () => {
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],writes=[];
  const rows=Array.from({length:100},(_,i)=>({id:i+1,key:`control_${i}`,page:'安全设置',frame:'vm',platform:'web',desc:`[连接器]-[安全设置]-[开关操作]-[安全设置开关${i}]`,revision:`r${i}`,change_status:['new','updated','retired',''][i%4],candidates:[{by:'testid',value:`control-${i}`}]}));
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('tp_token','mock-local-only'));
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let data=[];
   if(path==='/api/auth/me')data={user:{id:1,name:'测试员'},is_platform_admin:true,memberships:[]};
   if(path==='/api/projects')data=[{id:1,name:'验证项目'}];
   if(path==='/api/selectors/manage')data={shared:rows,by_sub:{},scope:{}};
   if(req.method()!=='GET'){
    const body=req.postDataJSON();writes.push({path,body});
    if(path==='/api/selectors/batch-description'){
     for(const r of rows.filter(r=>body.ids.includes(r.id))){const parts=r.desc.slice(1,-1).split(']-[');r.desc=[body.navigation,body.page,body.scene,body.element].map((value,i)=>`[${value || parts[i]}]`).join('-')}
     data={updated:body.ids.length};
    } else if(path==='/api/selectors/rename-page'){
     for(const r of rows){r.page=body.new_page;const parts=r.desc.slice(1,-1).split(']-[');parts[1]=body.new_page;r.desc=parts.map(x=>`[${x}]`).join('-')}
     data={updated:rows.length};
    } else if(req.method()==='PATCH') {Object.assign(rows[0],body);data=rows[0]}
   }
   await route.fulfill({json:{code:0,data}});
  });
  await page.goto(`${process.env.UI_BASE_URL||'http://127.0.0.1:8016'}/selectors`);
  await page.getByRole('button',{name:'编辑',exact:true}).first().waitFor();
  await page.getByRole('button',{name:'编辑',exact:true}).first().click();
  const dialog=page.getByRole('dialog',{name:'编辑 key',exact:true});
  assert.equal(await dialog.getByRole('textbox',{name:'导航Tab',exact:true}).inputValue(),'连接器');
  await dialog.getByRole('textbox',{name:'场景',exact:true}).fill('设置开关');
  await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
  assert.equal(writes.filter(w=>w.path==='/api/selectors/1').at(-1).body.desc,'[连接器]-[安全设置]-[设置开关]-[安全设置开关0]');
  const table=page.locator('.el-table').filter({has:page.getByRole('columnheader',{name:'说明',exact:true})});
  await table.locator('tbody .el-checkbox').first().click();
  await page.locator('.el-main').evaluate(el=>el.scrollTop=2400);
  await page.waitForTimeout(150);
  const toolbar=page.getByRole('toolbar',{name:'选择器批量操作'}),box=await toolbar.boundingBox(),main=await page.locator('.el-main').boundingBox();
  assert(box.y>=main.y-2 && box.y<main.y+30,JSON.stringify({box,main}));
  await toolbar.getByRole('button',{name:/批量改说明/}).click();
  const batch=page.getByRole('dialog',{name:'批量修改四段式说明',exact:true});
  assert.equal(await batch.getByRole('textbox').count(),4);
  await page.screenshot({path:'../backend/artifacts/selector-management/four-input-dialog.png'});
  assert(await batch.getByRole('button',{name:'保存',exact:true}).isDisabled());
  await batch.getByRole('textbox',{name:'导航Tab',exact:true}).fill('技能');
  await batch.getByRole('button',{name:'保存',exact:true}).click();await batch.waitFor({state:'hidden'});
  assert.equal(writes.at(-1).path,'/api/selectors/batch-description');assert.equal(rows[0].page,'安全设置');assert.equal(rows[0].desc,'[技能]-[安全设置]-[设置开关]-[安全设置开关0]');
  await table.locator('tbody .el-checkbox').first().click();
  await toolbar.getByRole('button',{name:/批量改说明/}).click();
  for(const [label,value] of [['导航Tab','技能'],['页面','专家页面'],['场景','批量场景'],['元素命名及类型','选项按钮']])await batch.getByRole('textbox',{name:label,exact:true}).fill(value);
  await batch.getByRole('button',{name:'保存',exact:true}).click();await batch.waitFor({state:'hidden'});
  assert.equal(rows[0].desc,'[技能]-[专家页面]-[批量场景]-[选项按钮]');assert.equal(rows[0].page,'安全设置');
  assert.equal(rows[1].desc,'[连接器]-[安全设置]-[开关操作]-[安全设置开关1]');
  let prompt;
  await page.getByRole('button',{name:'重命名分组 安全设置',exact:true}).click();
  prompt=page.locator('.el-message-box');await prompt.getByRole('textbox').fill('账户安全');await prompt.getByRole('button',{name:'保存',exact:true}).click();await prompt.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'重命名分组 账户安全',exact:true}).waitFor();
  assert.equal(writes.at(-1).path,'/api/selectors/rename-page');assert.equal(Object.keys(writes.at(-1).body.expected_revisions).length,100);
  assert(rows[0].desc.startsWith('[技能]-[账户安全]-[批量场景]'));
  assert.equal(await page.getByRole('columnheader',{name:'候选数',exact:true}).count(),0);
  assert.equal(await page.getByRole('columnheader',{name:'状态',exact:true}).count(),1);
  await page.locator('.el-main').evaluate(el=>el.scrollTop=2400);await page.waitForTimeout(150);
  await page.screenshot({path:'../backend/artifacts/selector-management/sticky-toolbar.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS four-part edit, sticky toolbar after scrolling 2400px, batch four inputs with blank preservation, group rename and status column');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
