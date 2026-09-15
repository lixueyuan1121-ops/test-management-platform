const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try {
 const page=await browser.newPage({viewport:{width:1500,height:1000}});page.setDefaultTimeout(90000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const el=(tag,name,rect,by,value)=>({tag,text:'',accessibleName:name,absRect:rect,element_ref:name,best:{by,value,...(by==='role'?{name,exact:true}:{})},candidates:[{by,value,...(by==='role'?{name,exact:true}:{})}]});
 const elements=[el('button','编辑',{x:100,y:70,w:28,h:28},'role','button'),el('button','复制',{x:140,y:70,w:28,h:28},'role','button'),el('div','外层容器',{x:10,y:10,w:300,h:120},'css','.shell')];
 elements[1].accessibleName='';elements[1].tooltipText='复制';
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{const p=new URL(route.request().url()).pathname;let data=[];
 if(p==='/api/projects')data=[{id:2,name:'验证项目'}];if(p==='/api/devices')data=[{id:1,runner_id:'bendi-win',name:'本地版'}];if(p==='/api/selectors/manage')data={shared:[],by_sub:{}};if(p==='/api/ai/cases')data={items:[],total:0};
 if(p==='/api/probe')data={id:1};if(p==='/api/probe/1')data={status:'done',result:{pageSize:{w:400,h:200},groups:[{frame:'shell',frameMatch:'shell',elements}]},screenshot_url:'/probe-fixture.svg'};
 await route.fulfill({json:{code:0,data}});});
 await page.route('**/probe-fixture.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><rect width="400" height="200" fill="white"/><rect x="10" y="10" width="300" height="120" fill="#eee"/><text x="90" y="40">你好</text><rect x="100" y="70" width="28" height="28" fill="#ccc"/><rect x="140" y="70" width="28" height="28" fill="#ccc"/></svg>'}));
 await page.goto('http://127.0.0.1:5197/tests/fixtures/selector-targets.html');await page.waitForFunction(()=>!!window.fixtureRouter);
 await page.evaluate(()=>window.fixtureRouter.push({name:'selectors',query:{project_id:'2',fix_keys:'copyToast',bulk:'1'}}));
 await page.getByRole('button',{name:'探测(扫当前页)',exact:true}).click();await page.locator('.el-box').first().waitFor();
 await page.locator('.probe-group').getByText('编辑',{exact:true}).waitFor();
 await page.locator('.probe-group').getByText('复制',{exact:true}).waitFor();
 const overlay=page.locator('.shot-overlay');await overlay.scrollIntoViewIfNeeded();const rect=await overlay.boundingBox();
 const move=(x,y)=>page.mouse.move(rect.x+x/400*rect.width,rect.y+y/200*rect.height);
 const click=(x,y)=>page.mouse.click(rect.x+x/400*rect.width,rect.y+y/200*rect.height);
 for (const [name,x] of [['编辑',114],['复制',154]]) {
  await move(20,20);await move(x,84);await click(x,84);
  const dialog=page.locator('.el-dialog:visible');await dialog.waitFor();
  await dialog.getByText(name,{exact:true}).waitFor();assert((await dialog.innerText()).includes('button'));
  assert(!(await dialog.innerText()).includes('外层容器'));
  await dialog.getByRole('button',{name:'取消',exact:true}).click();await dialog.waitFor({state:'hidden'});
 }
 assert.deepEqual(errors,[]);console.log('PASS: 父容器先高亮仍可分别点击编辑和复制，弹窗显示正确按钮名称');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
