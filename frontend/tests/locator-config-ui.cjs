const {chromium}=require(process.env.PLAYWRIGHT_MODULE);const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(90000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const rows=[],jobs=new Map(),saves=[];let id=0,modern=true;
 const elements=['语音','发送'].map((name,i)=>({tag:'div',text:'',accessibleName:name,element_ref:`ref-${i}`,best:{by:'css',value:'.icon'},candidates:[{by:'css',value:'.icon'},{by:'testid',value:'actions'}],absRect:{x:20+i*60,y:20,w:30,h:30}}));
 // Force an XPath shared by two textless controls; names only describe the choices.
 elements.forEach(e=>e.candidates=e.candidates.filter(c=>c.by!=='testid'));
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{const p=new URL(route.request().url()).pathname;let data=[];
 if(p==='/api/projects')data=[{id:2,name:'Locator test'}];if(p==='/api/devices')data=[{id:1,runner_id:'test',name:'Test'}];if(p==='/api/selectors/manage')data={shared:rows,by_sub:{}};if(p==='/api/ai/cases')data={items:[],total:0};
 if(p==='/api/probe'){jobs.set(++id,route.request().postDataJSON().params);data={id};}
 if(/^\/api\/probe\/\d+$/.test(p)){const params=jobs.get(Number(p.split('/').pop()));
 if(params.mode==='validate_selection')data={status:'done',result:{...(modern?{locator_features:['has_text','nth','primary','require_identity']}:{}),validation:params.items.map(item=>item.inspect_only?{key:item.key,ok:true,count:2,matches:elements.map((el,i)=>({...el,visible:true,frame:'shell',candidate:{...item.candidates[0],nth:i}}))}:{key:item.key,ok:true,identity_verified:true,count:1,hit:item.candidates[0]})}};
 else data={status:'done',result:{pageSize:{w:180,h:80},groups:[{frame:'shell',elements}]},screenshot_url:'/probe-fixture.svg'};}
 if(p==='/api/selectors'&&route.request().method()==='POST'){data={...route.request().postDataJSON(),id:rows.length+1,revision:'1'};rows.push(data);saves.push(data);}
 if(/^\/api\/selectors\/\d+$/.test(p)&&route.request().method()==='PATCH'){data={...rows[Number(p.split('/').pop())-1],...route.request().postDataJSON()};saves.push(data);}
 await route.fulfill({json:{code:0,data}});});
 await page.route('**/probe-fixture.svg',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="80"><text x="20" y="40">MIC</text><text x="80" y="40">SEND</text></svg>'}));
 await page.goto('http://127.0.0.1:5197/tests/fixtures/selector-targets.html');await page.waitForFunction(()=>!!window.fixtureRouter);
 await page.evaluate(()=>window.fixtureRouter.push({name:'selectors',query:{project_id:'2',fix_keys:'unused',bulk:'1'}}));
 await page.getByRole('button',{name:'退出批量',exact:true}).click();await page.getByRole('button',{name:'探测(扫当前页)',exact:true}).click();
 await page.locator('.probe-group .el-table__row').filter({hasText:'语音'}).getByRole('button',{name:'加为 key',exact:true}).click();
 const dialog=page.locator('.el-dialog:visible');await dialog.locator('.locator-match-option').nth(1).waitFor();
 assert.equal(await dialog.locator('.locator-match-option').count(),2);
 await dialog.getByRole('radio',{name:/#2/}).locator('xpath=ancestor::label').click();
 await dialog.getByPlaceholder('语义 key，如 login_button').fill('sendButton');
 if (process.env.LOCATOR_SCREENSHOT) await page.screenshot({path:process.env.LOCATOR_SCREENSHOT});
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
 assert.equal(saves[0].candidates.length,1);assert.equal(saves[0].candidates[0].by,'xpath');assert.equal(saves[0].candidates[0].nth,1);
 await page.locator('.probe-group .el-table__row').filter({hasText:'发送'}).getByRole('button',{name:'已存在',exact:true}).waitFor();
 await page.locator('.probe-group .el-table__row').filter({hasText:'语音'}).getByRole('button',{name:'加为 key',exact:true}).click();
 await dialog.locator('.locator-match-option').nth(1).waitFor();await dialog.getByRole('radio',{name:'CSS',exact:true}).locator('xpath=ancestor::label').click();
 await dialog.getByRole('radio',{name:'组合定位（加文本）',exact:true}).locator('xpath=ancestor::label').click();await dialog.getByRole('textbox',{name:'组合文本',exact:true}).fill('语音');
 await dialog.getByPlaceholder('语义 key，如 login_button').fill('voiceButton');await dialog.getByRole('button',{name:'保存',exact:true}).click();await dialog.waitFor({state:'hidden'});
 assert.deepEqual(saves[1].candidates,[{by:'css',value:'.icon',has_text:'语音',primary:true}]);
 modern=false;
 await page.locator('.el-box').first().click();await dialog.locator('.locator-match-option').nth(1).waitFor();
 await dialog.getByRole('radio',{name:'CSS',exact:true}).locator('xpath=ancestor::label').click();await dialog.getByRole('radio',{name:'组合定位（加文本）',exact:true}).locator('xpath=ancestor::label').click();await dialog.getByRole('textbox',{name:'组合文本',exact:true}).fill('语音');
 await dialog.getByRole('button',{name:'保存',exact:true}).click();await page.getByText('设备 Runner 版本不支持当前定位方式，请更新 Runner 后重试',{exact:true}).waitFor();assert.equal(saves.length,2);
 assert.deepEqual(errors,[]);console.log('PASS: live duplicate options, choosing second DOM, persisted XPath occurrence, CSS + text, selected status, old Runner guard');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
