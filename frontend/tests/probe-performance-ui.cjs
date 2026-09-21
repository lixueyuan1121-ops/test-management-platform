const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(90000);
    const errors=[]; page.on('pageerror', error => errors.push(error.message));
    const elements=Array.from({length:10000},(_,i)=>({tag:'button',text:`element-${i}`,element_ref:`ref-${i}`,best:{by:'testid',value:`id-${i}`},candidates:[{by:'testid',value:`id-${i}`},{by:'css',value:'.shared'}],absRect:{x:i%20*20,y:Math.floor(i/20)%10*20,w:15,h:15}}));
    await page.route(url=>url.pathname.startsWith('/api/'), async route=>{
      const p=new URL(route.request().url()).pathname;let data=[];
      if(p==='/api/projects')data=[{id:2,name:'Performance'}];
      if(p==='/api/devices')data=[{id:1,runner_id:'test',name:'Test'}];
      if(p==='/api/selectors/manage')data={shared:[],by_sub:{}};
      if(p==='/api/ai/cases')data={items:[],total:0};
      if(p==='/api/probe')data={id:1};
      if(p==='/api/probe/1')data={status:'done',result:{pageSize:{w:400,h:200},groups:[{frame:'shell',elements:elements.slice(0,75)},{frame:'vm',elements:elements.slice(75)}]},screenshot_url:'/probe-fixture.svg'};
      await route.fulfill({json:{code:0,data}});
    });
    await page.route('**/probe-fixture.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"/>'}));
    await page.goto('http://127.0.0.1:5197/tests/fixtures/selector-targets.html');
    await page.waitForFunction(()=>!!window.fixtureRouter);
    await page.evaluate(()=>window.fixtureRouter.push({name:'selectors',query:{project_id:'2',fix_keys:'copyToast',bulk:'1'}}));
    const start=Date.now();
    await page.getByRole('button',{name:'探测(扫当前页)',exact:true}).click();
    await page.locator('.probe-group .el-table__row').first().waitFor();
    assert.equal(await page.locator('.probe-group .el-table__row').count(),50);
    assert.equal(await page.locator('.el-box').count(),50);
    await page.locator('.probe-group').getByText('element-0',{exact:true}).waitFor();
    await page.locator('.el-pagination .btn-next').click();
    await page.locator('.probe-group').getByText('element-50',{exact:true}).waitFor();
    assert.equal(await page.locator('.probe-group').count(),2);
    assert.equal(await page.locator('.probe-group .el-table__row').count(),50);
    assert.equal(await page.locator('.el-box').count(),50);
    await page.getByRole('button',{name:'全选未添加（最多100个）',exact:true}).click();
    await page.getByRole('button',{name:'预览 key 并批量添加（100）',exact:true}).waitFor();
    await page.getByText('隐藏「已存在」',{exact:false}).click();
    await page.locator('.probe-group').getByText('element-0',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log(`PASS: 10000 elements, bounded rows/boxes, cross-frame paging, 100-item batch selection, filter reset; ${Date.now()-start}ms including probe polling`);
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
