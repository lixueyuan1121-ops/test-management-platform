const {chromium}=require(process.env.PLAYWRIGHT_MODULE);const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try {
 const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let item={id:1,project_id:1,title:'模式选择用例',steps:'打开模式选择',expected:'旧预期',exec_kind:'manual',selector_fix:true,selector_fix_keys:['homeModeQuickOption'],review_status:'pending',script:'[]'};
 let mode='failed',force=false,polls=0,genCalls=0;
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{const req=route.request(),url=new URL(req.url()),path=url.pathname;let data=[];
 if(path==='/api/projects')data=[{id:1,name:'测试项目'}];
 if(path==='/api/selectors')data={shared:[],by_sub:{}};
 if(path==='/api/ai/cases')data={items:[item],total:1};
 if(path==='/api/ai/testcases/1' && req.method()==='PATCH'){item={...item,...req.postDataJSON()};data=item;}
 if(path==='/api/ai/testcases/1/gen-script'){force=url.searchParams.get('force_regenerate')==='true';genCalls++;data={job_id:10};}
 if(path==='/api/ai-jobs/10'){polls++;data=polls===1?{status:'pending'}:{status:mode,error:'模拟生成失败：目标选择器尚未注册',result:{cid:1}};}
 await route.fulfill({json:{code:0,data}});
 });
 await page.goto((process.env.UI_BASE_URL||'http://127.0.0.1:5197')+'/tests/fixtures/case-edit.html');
 await page.getByRole('button',{name:'编辑',exact:true}).click({timeout:90000});
 const dialog=page.getByRole('dialog',{name:'编辑用例'});
 await dialog.locator('textarea').nth(1).fill('新的预期结果');
 await dialog.getByRole('button',{name:'保存并重生 script',exact:true}).click();
 await dialog.getByRole('status').getByText('正文已保存，脚本生成正在排队…',{exact:true}).waitFor();
 assert(await dialog.getByRole('button',{name:'保存',exact:true}).isDisabled());
 await dialog.getByText('正文已保存，但脚本未生成成功：模拟生成失败：目标选择器尚未注册',{exact:true}).waitFor();
 assert(force);assert.equal(item.expected,'新的预期结果');assert.equal(genCalls,1);
 assert(await dialog.isVisible());
 mode='done';polls=0;
 await dialog.getByRole('button',{name:'保存并重生 script',exact:true}).click();
 await dialog.waitFor({state:'hidden'});assert.equal(genCalls,2);assert.deepEqual(errors,[]);
 console.log('PASS: forced generation, new expected saved, queue state, visible failure, retry success');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
