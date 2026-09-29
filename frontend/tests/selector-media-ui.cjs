const {chromium}=require('../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1600,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const rows=Array.from({length:6},(_,i)=>({id:i+1,key:`control_${i}`,page:i<3?'首页':'技能',frame:'shell',desc:`[首页]-[页面]-[场景]-[按钮${i}]`,change_status:['new','updated','retired'][i%3],candidates:[{by:'css',value:`.item-${i}`}],screenshot_url:'',screenshot_revision:`image-${i}`}));
 const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ZAAAAABJRU5ErkJggg==','base64');
 let uploads=0,deletes=0;
 await page.addInitScript(()=>localStorage.setItem('tp_token','local-test-only'));
 await page.route('**/uploads/**',r=>r.fulfill({body:pixel,contentType:'image/png'}));
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
  const req=route.request(),u=new URL(req.url());let data=[];
  if(u.pathname==='/api/auth/me')data={user:{id:1,name:'测试'},is_platform_admin:true,memberships:[]};
  if(u.pathname==='/api/projects')data=[{id:1,name:'测试项目'}];
  if(u.pathname==='/api/selectors/manage')data={shared:rows,by_sub:{},scope:{}};
  if(u.pathname.endsWith('/screenshot')){
   const row=rows.find(r=>u.pathname===`/api/selectors/${r.id}/screenshot`);assert(row);assert.equal(u.searchParams.get('expected_revision'),row.screenshot_revision);
   if(req.method()==='POST'){assert(req.headers()['content-type'].includes('multipart/form-data'));uploads++;row.screenshot_url='/uploads/test.png';row.screenshot_source='upload';}
   else {assert.equal(req.method(),'DELETE');deletes++;row.screenshot_url='';row.screenshot_source='deleted';}
   row.screenshot_revision+='x';data=row;
  }
  await route.fulfill({json:{code:0,data}});
 });
 await page.goto(`${process.env.UI_BASE_URL||'http://localhost:8000'}/selectors?project_id=1`);
 const bar=page.getByRole('toolbar',{name:'选择器批量操作'});
 const count=text=>bar.getByText(text,{exact:true}).waitFor();
 await count('已选 0 · 显示 6 / 6');
 const search=page.getByRole('textbox',{name:'搜索选择器'});
 await search.fill('CONTROL_0');await count('已选 0 · 显示 1 / 6');
 await bar.locator('label.el-checkbox').filter({hasText:'当前页全选'}).click();await count('已选 1 · 显示 1 / 6');
 await search.fill('');await count('已选 0 · 显示 6 / 6');
 await page.locator('.el-select').filter({has:page.getByRole('combobox',{name:'筛选DOM状态'})}).click();await page.getByRole('option',{name:'新增',exact:true}).click();await count('已选 0 · 显示 2 / 6');
 await search.fill('.item-3');await count('已选 0 · 显示 1 / 6');
 await search.fill('missing');await count('已选 0 · 显示 0 / 6');await page.getByText('没有匹配的选择器',{exact:true}).waitFor();
 await search.fill('control_0');await count('已选 0 · 显示 1 / 6');
 assert.equal(await page.getByRole('columnheader',{name:'截图',exact:true,includeHidden:true}).count(),0);
 assert.deepEqual(errors,[]);
 console.log('PASS search, status intersection, filtered select-all, empty results, screenshot column removed');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
