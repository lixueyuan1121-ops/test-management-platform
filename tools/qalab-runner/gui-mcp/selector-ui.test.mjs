import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';

const base = process.env.SELECTOR_UI_BASE;
test('选择器页面保留 role 元数据、提交版本；录制等待最终确认再开放保存', {skip:!base}, async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_TEST_EXECUTABLE});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors=[], patches=[];
  page.on('pageerror', error => errors.push(error.message));
  let stopping = false;
  const key = {id:1,key:'save',project_id:1,sub_product:'',platform:'web',frame:'shell',page:'表单',desc:'保存表单',revision:'revision-one',candidates:[{by:'role',value:'button',name:'保存',exact:true}]};
  const events=[{event_id:'1',action:'set_checked',tag:'input',type:'checkbox',checked:true,text:'同意',candidates:[{by:'testid',value:'agree'}]},
    {event_id:'2',action:'select_option',tag:'select',values:['b'],text:'B',candidates:[{by:'testid',value:'choice'}]},
    {event_id:'3',action:'assert',assert:{kind:'visible'},text:'成功',candidates:[{by:'testid',value:'status'}]}];
  await context.addInitScript(() => localStorage.setItem('tp_token','isolated-ui-test'));
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path=url.pathname, method=route.request().method();
    let data=[];
    if(path==='/api/auth/me') data={user:{id:1,name:'测试用户'},is_platform_admin:true,memberships:[]};
    else if(path==='/api/projects') data=[{id:1,name:'隔离测试项目',role:'admin'}];
    else if(path==='/api/devices') data=[{id:1,runner_id:'test-device',name:'本地测试设备',online:true}];
    else if(path==='/api/selectors/manage') data={shared:[key],by_sub:{},scope:{vm_iframe:''}};
    else if(path==='/api/selectors/1' && method==='PATCH') {
      patches.push(route.request().postDataJSON());
      await route.fulfill({status:409,json:{code:409,msg:'选择器已被其他操作更新，请刷新后重试',data:null}});return;
    }
    else if(path==='/api/tasks') data=[{id:1,title:'隔离任务'}];
    else if(path==='/api/record' && method==='POST') data={id:1};
    else if(path==='/api/record/1/stop') {stopping=true;data={status:'stopping',events};}
    else if(path==='/api/record/1') data={id:1,status:stopping?'stopped':'recording',events};
    await route.fulfill({json:{code:0,data}});
  });
  try {
    await page.goto(base+'/selectors?project_id=1');
    await page.getByRole('button',{name:'编辑',exact:true}).first().click();
    const dialog=page.getByRole('dialog');
    assert.ok((await dialog.locator('textarea').evaluateAll(elements => elements.map(el => el.value))).join('').includes('保存'));
    await dialog.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText('选择器已被其他操作更新，请刷新后重试',{exact:true}).first().waitFor();
    assert.equal(patches[0].expected_revision,'revision-one');
    assert.deepEqual(patches[0].candidates,key.candidates);
    assert.equal(await dialog.isVisible(),true);
    await page.goto(base+'/recorder');
    await page.getByRole('button',{name:'开始录制',exact:true}).click();
    await page.getByRole('button',{name:'停止录制',exact:true}).click();
    await page.getByText('正在等待执行机上传最后的步骤，完成后即可编辑保存',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'保存为 e2e 用例',exact:true}).count(),0);
    await page.getByRole('button',{name:'保存为 e2e 用例',exact:true}).waitFor();
    assert.ok(await page.getByRole('switch').count());
    await page.screenshot({path:'/tmp/selector-review-ui.png',fullPage:true});
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
