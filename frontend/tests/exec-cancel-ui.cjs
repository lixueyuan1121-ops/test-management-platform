const { chromium } = require('../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_TEST_EXECUTABLE});
try{
const page=await browser.newPage({viewport:{width:1440,height:950}});
let rows=[
{run_id:101,case_id:1,project_id:1,title:'运行中用例',batch_id:'old-active',runner:'test',kind:'gui',status:'running',updated_at:'2026-09-21T12:00:00'},
{run_id:102,case_id:2,project_id:1,title:'排队用例',batch_id:'old-active',runner:'test',kind:'gui',status:'pending',updated_at:'2026-09-21T12:00:00'}];
rows.push({run_id:103,case_id:3,project_id:1,title:'历史报告用例',batch_id:'new-done',runner:'test',kind:'gui',status:'blocked',fail_kind:'selector',verdict:'blocked',has_report:true,updated_at:'2026-09-21T13:00:00'});
const errors=[],writes=[];let fail=true,reader=false,historyReads=0,detailReads=0,caseReads=0;
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>localStorage.setItem('tp_token','local-fixture-only'));
await page.route(u=>u.pathname.startsWith('/api/'),async route=>{
const req=route.request(),path=new URL(req.url()).pathname;let data=[];
if(path==='/api/auth/me') data={user:{id:1,name:'测试员'},is_platform_admin:false,memberships:[{project_id:1,role:reader?'guest':'member'}]};
if(path==='/api/projects') data=[{id:1,name:'测试项目'}];
if(path==='/api/exec-queue/history') { historyReads++; assert.equal(new URL(req.url()).searchParams.get('summary'),'true'); data=rows; }
if(path.startsWith('/api/ai/testcases/')) caseReads++;
if(path==='/api/exec-queue/103') { detailReads++; data={...rows[2],report:[{action:'assert_text',desc:'按需加载的报告',ok:true}]}; }
if(path.endsWith('/cancel')){
writes.push(path);const row=rows.find(x=>path.includes('/'+x.run_id+'/'));
if(fail){fail=false;return route.fulfill({status:500,json:{msg:'模拟终止失败'}});}
if(row.status==='pending')Object.assign(row,{status:'blocked',fail_kind:'cancelled',cancelled:true,verdict:'blocked'});
else Object.assign(row,{cancel_requested:true,fail_kind:'cancel_requested'});
data=row;
}
await route.fulfill({json:{code:0,data}});
});
await page.goto((process.env.UI_BASE_URL||'http://127.0.0.1:5199')+'/exec-results?project_id=1');
const running=page.locator('.el-table__row').filter({hasText:'运行中用例'});
const pending=page.locator('.el-table__row').filter({hasText:'排队用例'});
await running.getByRole('button',{name:'手动终止',exact:true}).waitFor();
assert.equal(await running.getByText('重试',{exact:true}).count(),0);
assert.equal(historyReads,1);assert.equal(detailReads,0);assert.equal(caseReads,0);
await page.waitForTimeout(6200);
assert.equal(historyReads,1,'no automatic list polling');assert.equal(caseReads,0,'no blocked-case prefetch');
await pending.getByRole('button',{name:'手动终止',exact:true}).click();
await page.getByText('模拟终止失败',{exact:true}).waitFor();
assert(await pending.getByRole('button',{name:'手动终止',exact:true}).isEnabled());
await pending.getByRole('button',{name:'手动终止',exact:true}).click();
await pending.getByText('已终止',{exact:true}).waitFor();
await running.getByRole('button',{name:'手动终止',exact:true}).click();
await running.getByText('终止中',{exact:true}).waitFor();
assert(await running.getByRole('button',{name:'终止中…',exact:true}).isDisabled());
Object.assign(rows[0],{status:'blocked',verdict:'blocked',fail_kind:'cancelled',cancelled:true,cancel_requested:false});
await page.getByRole('button',{name:'刷新执行结果',exact:true}).click();
await page.getByText('批次 old-active',{exact:true}).click();
await running.getByText('已终止',{exact:true}).waitFor();
assert.equal(await running.getByText('补齐选择器',{exact:true}).count(),0);
assert.equal(await running.getByText('重试',{exact:true}).count(),1);
await page.getByText('批次 new-done',{exact:true}).click();
const reported=page.locator('.el-table__row').filter({hasText:'历史报告用例'});
if(!await reported.getByText('报告',{exact:true}).isVisible()) await page.getByText('批次 new-done',{exact:true}).click();
await reported.getByText('报告',{exact:true}).click();
await page.getByText('按需加载的报告',{exact:true}).waitFor();
assert.equal(detailReads,1);
await page.getByRole('dialog',{name:'执行报告'}).getByRole('button',{name:'Close this dialog'}).click();
await page.screenshot({path:process.env.UI_SCREENSHOT||'backend/artifacts/exec-cancel-20260921/cancel-ui.png',fullPage:true});
reader=true; rows[0]={...rows[0],status:'running',fail_kind:null,verdict:null,cancelled:false};
await page.reload();await running.getByText('执行中',{exact:true}).waitFor();
assert(await running.getByRole('button',{name:'手动终止',exact:true}).isVisible());
assert(await running.getByRole('button',{name:'手动终止',exact:true}).isDisabled());
assert.deepEqual(errors,[]);assert.equal(writes.length,3);
console.log('PASS cancel UI: failure retry, pending cancel, manual refresh only, lazy reports, no prefetch, old active batch visible, member/guest permissions');
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});