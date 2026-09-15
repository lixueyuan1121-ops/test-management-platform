const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../../tools/qalab-runner/eval/node_modules/playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);page.setDefaultNavigationTimeout(90000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let clearCount=19, confirmed=false;
 const analysis=()=>({id:10,job_id:20,status:'done',revision:1,source_hash:'fixture',source_text:'保护目录删除必须确认',source_info:{warnings:['部分附件未读取']},visual_readings:[{id:'IMG1',status:'failed',uncertainties:'附图无法读取'}],draft:{summary:'保护目录删除',scope:'Windows',out_of_scope:'',flow:'点击删除后取消',scenario_review_required:false,scenarios:[],
 rules:Array.from({length:36},(_,i)=>({id:`R${i+1}`,title:`规则${i+1}`,condition:'保护目录有文件',action:'点击删除',expected:'显示确认框',criteria:[{id:`R${i+1}-C1`,text:'弹出确认框'}],source_type:i<clearCount?'explicit':'inferred',source_quote:'保护目录删除必须确认',source_material_ids:[],review_note:'',status:i>=32?'excluded':i<clearCount?'confirmed':'pending'})),questions:[]}});
 await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
  const path=new URL(route.request().url()).pathname;let data=[];
  if(path==='/api/ai/requirements/analyses/10')data=analysis();
  if(path.endsWith('/confirm')){const body=route.request().postDataJSON();assert(body.scope_reviewed);assert(!body.confirmation_note);confirmed=true;data={baseline_id:40,revision:1};}
  await route.fulfill({json:{code:0,data}});
 });
 const base=process.env.UI_BASE_URL||'http://127.0.0.1:5197';
 await page.goto(base+'/tests/fixtures/review-regressions.html');
 const confirm=page.getByRole('button',{name:'确认本次生成范围（19 条）',exact:true});await confirm.waitFor();assert(await confirm.isDisabled());
 await page.getByRole('button',{name:'13 条规则待确认',exact:true}).click();assert.equal(await page.locator('.el-table__body-wrapper .el-table__row').count(),13);
 await page.getByText('我同意用已明确的规则生成用例，待确认的内容暂不生成',{exact:true}).click();assert(await confirm.isEnabled());
 await confirm.click();await page.getByText('已确认版本 #40',{exact:true}).waitFor();assert(confirmed);
 await page.evaluate(()=>window.updateProgress({chars:6902,units:[{id:'analysis',title:'需求理解与验收规则',status:'running',chars:6902,text:'最终返回替换正文',attempt:1}]}));
 await page.getByText(/累计接收 8224 字/).waitFor();await page.getByText('当前内容 6902 字',{exact:true}).waitFor();
 await page.evaluate(()=>window.updateProgress({received_chars:9000,units:[{id:'analysis',title:'需求理解与验收规则',status:'running',chars:776,text:'重试后的返回',attempt:2,note:'上一轮返回格式不完整，正在重试当前步骤'}]}));
 await page.getByText(/当前步骤第 2 次尝试/).waitFor();await page.getByText(/累计接收 9000 字/).waitFor();
 clearCount=0;await page.reload();const empty=page.getByRole('button',{name:'确认本次生成范围（0 条）',exact:true});await empty.waitFor();
 await page.getByText('我同意用已明确的规则生成用例，待确认的内容暂不生成',{exact:true}).click();assert(await empty.isDisabled());
 assert.deepEqual(errors,[]);console.log('PASS: 19 clear + 13 pending + 4 excluded, optional notes/warnings, no clear rules blocked, stable received count and retry label');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
