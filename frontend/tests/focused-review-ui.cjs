const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1360,height:1000}});const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('PAGEERROR',e.message)});
 const rule={id:'R1',title:'删除保护目录内文件时先询问',condition:'文件在保护目录内',action:'点击删除',expected:'弹出确认框，取消后文件保留',criteria:[{id:'R1-C1',text:'取消后文件保留'}],source_type:'explicit',source_quote:'保护目录删除必须确认',source_material_ids:[],status:'confirmed',review_note:'',platform:'Windows',module:'文件管理'};
 const other={...structuredClone(rule),id:'R2',title:'删除普通目录内文件是否也要询问',source_type:'inferred',status:'pending',criteria:[{id:'R2-C1',text:'删除前显示确认框'}]};
 const scene=r=>({id:'S'+r.id,rule_id:r.id,criterion_ids:r.criteria.map(c=>c.id),actor:'使用该功能的用户',given:r.condition,when:r.action,then:r.expected,counterexample:'',kind:'normal',reviewed:false});
 let analysis={id:10,job_id:20,status:'done',revision:1,source_hash:'fixture',source_text:'保护目录删除必须确认',source_info:{warnings:[]},visual_readings:[],draft:{summary:'删除文件前询问',scope:'Windows',out_of_scope:'',flow:'点击删除，然后选择确认或取消',rules:[rule,other],scenarios:[scene(rule),scene(other)],scenario_review_required:true,questions:[{id:'Q1',question:'删除普通目录内的文件时，也要先弹出确认框吗？',evidence:'资料只说明了保护目录',options:['也需要确认','直接删除'],rule_ids:['R2'],blocking:true,answer:''}]}};
 let confirmed=false;
 await page.route(url => url.pathname.startsWith('/api/'),async route=>{const path=new URL(route.request().url()).pathname;let data=[];
 if(path==='/api/ai/requirements/analyses')data=[];
 if(path==='/api/ai/requirements/analyses/10'){
 if(route.request().method()==='PATCH'){analysis.draft=route.request().postDataJSON().draft;analysis.revision++;analysis.draft.rules[1].status='confirmed';}
 data=analysis;
 }
 if(path.endsWith('/confirm')){confirmed=true;data={baseline_id:40,revision:analysis.revision};}
 await route.fulfill({json:{code:0,data}});});
 page.on('console',m=>{if(m.type()==='error') console.error('CONSOLE',m.text().slice(0,400))});
 await page.goto((process.env.UI_BASE_URL||'http://127.0.0.1:5197')+'/tests/fixtures/focused-review.html');
 await page.getByText('只处理需要你决定的内容',{exact:true}).waitFor({timeout:90000}).catch(async e=>{console.error(await page.locator('body').innerText());throw e});
 await page.getByRole('button',{name:'1 条规则待确认',exact:true}).click();
 assert.equal(await page.locator('.el-table__body-wrapper .el-table__row').count(),1);
 await page.getByText('什么时候：',{exact:true}).first().waitFor();
 await page.getByText('已明确（1）',{exact:true}).click();
 assert.equal(await page.locator('.el-table__body-wrapper .el-table__row').count(),1);
 await page.getByRole('tab',{name:/具体场景/}).click();
 assert.equal(await page.getByRole('checkbox',{name:/核对场景/}).count(),0);
 await page.getByRole('tab',{name:/澄清问题/}).click();
 await page.getByRole('textbox',{name:'Q1 处理结论',exact:true}).fill('普通目录也先弹出确认框');
 await page.getByRole('textbox',{name:'Q1 处理结论',exact:true}).press('End');
 await page.getByRole('textbox',{name:'R2 处理说明',exact:true}).fill('产品已确认普通目录也先询问，按此规则测试');
 await page.getByRole('button',{name:'保存评审草稿',exact:true}).click();
 await page.getByRole('button',{name:'0 条规则待确认',exact:true}).click();
 await page.getByText('没有待确认的规则',{exact:true}).waitFor();
 await page.getByText('我同意用已明确的规则生成用例，待确认的内容暂不生成',{exact:true}).click();
 await page.getByRole('button',{name:'确认本次生成范围（2 条）',exact:true}).click();
 await page.getByText('已确认版本 #40',{exact:true}).waitFor();assert(confirmed);assert.deepEqual(errors,[]);
 await page.getByText('全部（2）',{exact:true}).click();
 await page.screenshot({path:'focused-review-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'focused-review-mobile.png',fullPage:true});
 console.log('PASS: filters, plain labels, no scene ticks, stable answers, save and confirm');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
