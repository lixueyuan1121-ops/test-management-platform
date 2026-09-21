const {chromium}=require(process.env.PLAYWRIGHT_MODULE);const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try {
 const page=await browser.newPage();page.setDefaultTimeout(90000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let id=0,allowFirst=false,failImage=true,releaseOld;const calls=new Map();let oldPendingResolve;const oldPending=new Promise(r=>{oldPendingResolve=r});
 const elements=Array.from({length:51},(_,i)=>({tag:'button',text:`Element ${i}`,element_ref:`ref-${i}`,best:{by:'testid',value:`id-${i}`},candidates:[{by:'testid',value:`id-${i}`}],...(i===50?{absRect:{x:10,y:10,w:30,h:30}}:{})}));
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{const p=new URL(route.request().url()).pathname;let data=[];
 if(p==='/api/projects')data=[{id:2,name:'Screenshot test'}];if(p==='/api/devices')data=[{id:1,runner_id:'test',name:'Test'}];if(p==='/api/selectors/manage')data={shared:[],by_sub:{}};if(p==='/api/ai/cases')data={items:[],total:0};
 if(p==='/api/probe')data={id:++id};
 if(/^\/api\/probe\/\d+$/.test(p)){
  const n=Number(p.split('/').pop()),count=(calls.get(n)||0)+1;calls.set(n,count);
  const result=n===6?{groups:[],screenshot_error:'capture timeout'}:n===1?{pageSize:{w:100,h:60},groups:[{frame:'shell',elements}]}:{groups:[]};
  let url=`/shot-${n}.svg`;
  if((n===1&&!allowFirst)||n===6)url=null;
  if(n===4){if(count===1)url=null;else {oldPendingResolve();await new Promise(r=>{releaseOld=r});}}
  data={status:'done',result,screenshot_url:url};
 }
 await route.fulfill({json:{code:0,data}});});
 await page.route(url=>url.pathname.startsWith('/shot-'),async route=>{
  if(new URL(route.request().url()).pathname==='/shot-3.svg'&&failImage){await route.fulfill({status:404,body:'missing'});return;}
  await route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="skyblue"/></svg>'});
 });
 await page.goto('http://127.0.0.1:5197/tests/fixtures/selector-targets.html');await page.waitForFunction(()=>!!window.fixtureRouter);
 await page.evaluate(()=>window.fixtureRouter.push({name:'selectors',query:{project_id:'2',fix_keys:'unused',bulk:'1'}}));
 await page.getByRole('button',{name:'退出批量',exact:true}).click();
 const discover=()=>page.getByRole('button',{name:'探测(扫当前页)',exact:true}).click();
 const loaded=()=>page.waitForFunction(()=>{const img=document.querySelector('.shot-img');return img?.complete&&img.naturalWidth>0&&img.getClientRects().length>0});
 await discover();await page.locator('.probe-group .el-table__row').first().waitFor();
 await page.getByText('元素已探测完成，正在等待截图上传…',{exact:true}).waitFor();
 await page.getByRole('button',{name:'全选未添加（最多100个）',exact:true}).click();
 allowFirst=true;await loaded();
 assert.equal(await page.locator('.el-box').count(),0);assert.equal(await page.locator('.probe-group .el-table__row').count(),50);
 await page.getByRole('button',{name:'预览 key 并批量添加（51）',exact:true}).waitFor();
 await page.locator('.el-pagination .btn-next').click();await page.waitForFunction(()=>document.querySelectorAll('.el-box').length===1);await loaded();
 await page.locator('.el-pagination .btn-prev').click();await page.waitForFunction(()=>document.querySelectorAll('.el-box').length===0);await loaded();
 console.log('PASS: delayed screenshot upload, zero-box pages, paging, and selection retained');
 await discover();await loaded();assert.equal(await page.locator('.probe-group .el-table__row').count(),0);
 assert((await page.locator('.shot-img').getAttribute('src')).includes('shot-2.svg'));
 console.log('PASS: screenshot remains visible with no groups or pageSize');
 await discover();await page.getByText('截图加载失败，请重试获取截图',{exact:true}).waitFor();
 failImage=false;await page.getByRole('button',{name:'重试获取截图',exact:true}).click();await loaded();assert.equal(id,3);
 console.log('PASS: broken image can retry without starting a new probe');
 await discover();await oldPending;await discover();await loaded();
 assert((await page.locator('.shot-img').getAttribute('src')).includes('shot-5.svg'));releaseOld();
 await new Promise(r=>setTimeout(r,150));assert((await page.locator('.shot-img').getAttribute('src')).includes('shot-5.svg'));
 await discover();await page.getByText('设备截图失败，请重新探测：capture timeout',{exact:true}).waitFor();
 await page.getByRole('button',{name:'重新探测并获取截图',exact:true}).click();await loaded();assert.equal(id,7);
 assert((await page.locator('.shot-img').getAttribute('src')).includes('shot-7.svg'));console.log('PASS: capture failure retry creates a new probe and gets a fresh image');
 assert.deepEqual(errors,[]);console.log('PASS: late screenshot response cannot overwrite a newer probe');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
