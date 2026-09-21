const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage(); page.setDefaultTimeout(90000);
    const errors=[]; page.on('pageerror', e=>errors.push(e.message));
    const rows=[]; const jobs=new Map(); let jobId=0;
    const elements=['语音','发送','失败按钮'].map((text,i)=>({tag:'button',text,element_ref:`ref-${i}`,best:{by:'css',value:'.shared-icon'},candidates:[{by:'css',value:'.shared-icon'}],absRect:{x:20+i*60,y:20,w:30,h:30}}));
    await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
      const p=new URL(route.request().url()).pathname; let data=[];
      if(p==='/api/projects')data=[{id:2,name:'Status regression'}];
      if(p==='/api/devices')data=[{id:1,runner_id:'test',name:'Test'}];
      if(p==='/api/selectors/manage')data={shared:rows,by_sub:{}};
      if(p==='/api/ai/cases')data={items:[],total:0};
      if(p==='/api/probe') {
        const params=route.request().postDataJSON().params;
        jobs.set(++jobId,params); data={id:jobId};
      }
      if(/^\/api\/probe\/\d+$/.test(p)) {
        const params=jobs.get(Number(p.split('/').pop()));
        data=params.mode==='validate_selection'
          ? {status:'done',result:{validation:params.items.map(item=>({key:item.key,ok:true,identity_verified:true,hit:{by:'xpath',value:`/ui/button[${Number(item.element_ref.slice(4))+1}]`}}))}}
          : {status:'done',result:{pageSize:{w:240,h:80},groups:[{frame:'shell',elements}]},screenshot_url:'/probe-fixture.svg'};
      }
      if(p==='/api/selectors' && route.request().method()==='POST') {
        const body=route.request().postDataJSON();
        if(body.candidates.some(c=>c.value==='/ui/button[3]')) { await route.fulfill({json:{code:1,msg:'simulated save failure'}}); return; }
        data={...body,id:rows.length+1,revision:1}; rows.push(data);
      }
      await route.fulfill({json:{code:0,data}});
    });
    await page.route('**/probe-fixture.svg',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"/>'}));
    await page.goto('http://127.0.0.1:5197/tests/fixtures/selector-targets.html');
    await page.waitForFunction(()=>!!window.fixtureRouter);
    await page.evaluate(()=>window.fixtureRouter.push({name:'selectors',query:{project_id:'2',fix_keys:'unused',bulk:'1'}}));
    await page.getByRole('button',{name:'退出批量',exact:true}).click();
    const discover=()=>page.getByRole('button',{name:'探测(扫当前页)',exact:true}).click();
    await discover(); await page.locator('.box-new').first().waitFor();
    assert.equal(await page.locator('.box-new').count(),3);
    await page.locator('.probe-group .el-table__row').filter({hasText:'语音'}).getByRole('button',{name:'加为 key',exact:true}).click();
    const dialog=page.locator('.el-dialog:visible');
    await dialog.getByPlaceholder('语义 key，如 login_button').fill('voiceButton');
    await dialog.getByRole('textbox',{name:'定位值',exact:true}).fill('/ui/button[1]');
    await dialog.getByRole('button',{name:'保存',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    await page.locator('.box-exists').waitFor();
    assert.equal(await page.locator('.box-new').count(),2);
    await page.locator('.probe-group .el-table__row').filter({hasText:'语音'}).getByRole('button',{name:'已存在',exact:true}).waitFor();
    await page.locator('.probe-toolbar').getByText('新增 2 · 可更新 0 · 已存在 1',{exact:true}).waitFor();
    // Shared siblings remain unadded; batch saves one and rejects the other.
    await page.getByRole('button',{name:'全选未添加（最多100个）',exact:true}).click();
    await page.getByRole('button',{name:'预览 key 并批量添加（2）',exact:true}).click();
    await dialog.getByRole('button',{name:'校验并保存',exact:true}).click();
    await dialog.getByText('simulated save failure',{exact:true}).waitFor();
    await dialog.getByRole('button',{name:'关闭',exact:true}).click();
    await page.locator('.probe-toolbar').getByText('新增 1 · 可更新 0 · 已存在 2',{exact:true}).waitFor();
    assert.equal(await page.locator('.box-exists').count(),2);
    assert.equal(await page.locator('.box-new').count(),1);
    await page.getByText('隐藏「已存在」',{exact:false}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.el-box').length===1);
    assert.equal(await page.locator('.probe-group .el-table__row').count(),1);
    await page.getByText('隐藏「已存在」',{exact:false}).click();
    // New snapshots must not inherit the old uid-to-key associations.
    await discover(); await page.locator('.probe-toolbar').getByText('新增 3 · 可更新 0 · 已存在 0',{exact:true}).waitFor();
    assert.equal(await page.locator('.box-new').count(),3);
    assert.deepEqual(errors,[]);
    console.log('PASS: manual XPath save updates exact box/row/count; batch partial failure remains new; hide-exists updates; re-probe resets snapshot associations');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
