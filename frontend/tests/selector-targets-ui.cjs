const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1600,height:1100}});const errors=[];page.setDefaultTimeout(90000);page.on('pageerror',e=>{errors.push(e.message);console.error(e.message)});page.on('console',m=>{if(m.type()==='error')console.error(m.text())});
 let detailReads=0;
 const items=[{id:230,title:'复制操作保持可用',selector_fix:true,selector_fix_keys:['copyToast'],exec_kind:'manual',page:'会话',steps:'点击复制后显示成功提示'},
 {id:231,title:'朗读操作保持可用',selector_fix:true,selector_fix_keys:['chatMsgReadAloud'],exec_kind:'manual',page:'会话',steps:'点击朗读'}];
 const scripts={230:[{action:'click',target:{key:'copyButton'},desc:'点击历史回答下方的复制按钮'}, {action:'wait',target:{key:'copyToast'},desc:'等待复制成功提示（待补：复制成功 toast 元素）'}],231:[{action:'click',target:{key:'chatMsgReadAloud'},desc:'点击已完成回答底部动作条中的朗读按钮'}]};
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
 const path=new URL(route.request().url()).pathname;let data=[];
 if(path==='/api/projects')data=[{id:2,name:'验证项目'}];
 if(path==='/api/selectors/manage')data={shared:[],by_sub:{}};
 if(path==='/api/ai/cases')data={items,total:2};
 if(path.startsWith('/api/ai/testcases/')){const id=Number(path.split('/').pop());detailReads++;data={...items.find(x=>x.id===id),script:scripts[id]};}
 await route.fulfill({json:{code:0,data}});
 });
 await page.goto((process.env.UI_BASE_URL||'http://127.0.0.1:5197')+'/tests/fixtures/selector-targets.html');
 await page.getByRole('button',{name:'copyToast',exact:true}).waitFor();
 assert.equal(await page.getByRole('columnheader',{name:'待补充的元素',exact:true}).count(),0);
 assert.equal(detailReads,0);
 const tip=page.locator('.missing-selector-tooltip:visible');
 await page.getByRole('button',{name:'copyToast',exact:true}).hover();
 await tip.getByText('复制成功 toast 元素',{exact:true}).waitFor();
 assert.equal(detailReads,1);
 assert(await tip.getByText('脚本第 2 步',{exact:true}).isVisible());
 assert.equal(await page.locator('.el-message').count(),0);
 await page.screenshot({path:process.env.UI_SCREENSHOT_DIR+'/case-library-hover.png',fullPage:true,animations:'disabled'});
 await page.mouse.move(1500,1000);await tip.waitFor({state:'hidden'});
 await page.getByRole('button',{name:'copyToast',exact:true}).hover();
 await tip.getByText('复制成功 toast 元素',{exact:true}).waitFor();assert.equal(detailReads,1);
 await page.mouse.move(1500,1000);await tip.waitFor({state:'hidden'});
 await page.getByRole('button',{name:'chatMsgReadAloud',exact:true}).hover();
 await tip.getByText('点击已完成回答底部动作条中的朗读按钮',{exact:true}).waitFor();
 await page.mouse.move(1500,1000);await tip.waitFor({state:'hidden'});
 await page.getByRole('button',{name:'去补充元素',exact:true}).first().click();
 await page.getByRole('radio',{name:'copyToast',exact:true}).waitFor();
 assert.equal(await page.locator('.missing-target-list').count(),0);
 assert.equal(await page.locator('.probe-key-tooltip:visible').count(),0);
 await page.locator('.el-radio-button__inner').filter({hasText:'copyToast'}).hover();
 await page.locator('.probe-key-tooltip:visible').getByText('复制成功 toast 元素',{exact:true}).waitFor();
 await page.locator('.probe-key-tooltip:visible').getByText('先操作：点击历史回答下方的复制按钮',{exact:true}).waitFor();
 await page.mouse.move(1500,1000);await page.locator('.probe-key-tooltip:visible').waitFor({state:'hidden'});
 // Remount the page with the same context as the user's bulk entry.
 const query=await page.evaluate(()=>({...window.fixtureRouter.currentRoute.value.query,bulk:'1'}));
 await page.evaluate(()=>window.fixtureRouter.push('/'));
 await page.getByRole('button',{name:'copyToast',exact:true}).waitFor();
 await page.evaluate(q=>window.fixtureRouter.push({name:'selectors',query:q}),query);
 const chip=page.locator('.fix-keys-chips .el-tag').filter({hasText:'copyToast'});
 await chip.waitFor();assert.equal(await page.locator('.missing-target-list').count(),0);
 await chip.hover();await page.locator('.probe-key-tooltip:visible').getByText('复制成功 toast 元素',{exact:true}).waitFor();
 await page.screenshot({path:process.env.UI_SCREENSHOT_DIR+'/selector-destination.png',fullPage:true,animations:'disabled'});
 await page.mouse.move(1500,1000);await page.locator('.probe-key-tooltip:visible').waitFor({state:'hidden'});
 assert.deepEqual(errors,[]);console.log('PASS: 用例库及单条/批量探测key悬浮说明正常，移开隐藏，无常驻说明卡片');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
