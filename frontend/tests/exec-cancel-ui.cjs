const { chromium } = require('../../tools/qalab-runner/gui-mcp/node_modules/playwright-core');
const assert = require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_TEST_EXECUTABLE});
try{
const page=await browser.newPage({viewport:{width:1440,height:950}});
let rows=[
{run_id:101,case_id:1,project_id:1,title:'运行中用例',batch_id:'cancel-test',runner:'test',kind:'gui',status:'running',updated_at:'2026-09-21T12:00:00'},
{run_id:102,case_id:2,project_id:1,title:'排队用例',batch_id:'cancel-test',runner:'test',kind:'gui',status:'pending',updated_at:'2026-09-21T12:00:00'}];
const errors=[],writes=[];let fail=true,reader=false;
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>localStorage.setItem('tp_token','local-fixture-only'));
await page.route(u=>u.pathname.startsWith('/api/'),async route=>{
const req=route.request(),path=new URL(req.url()).pathname;let data=[];
if(path==='/api/auth/me') data={user:{id:1,name:'测试员'},is_platform_admin:false,memberships:[{project_id:1,role:reader?'viewer':'member'}]};
if(path==='/api/projects') data=[{id:1,name:'测试项目'}];
if(path==='/api/exec-queue/history') data=rows;
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
await pending.getByRole('button',{name:'手动终止',exact:true}).click();
await page.getByText('模拟终止失败',{exact:true}).waitFor();
assert(await pending.getByRole('button',{name:'手动终止',exact:true}).isEnabled());
await pending.getByRole('button',{name:'手动终止',exact:true}).click();
await pending.getByText('已终止',{exact:true}).waitFor();
await running.getByRole('button',{name:'手动终止',exact:true}).click();
await running.getByText('终止中',{exact:true}).waitFor();
assert(await running.getByRole('button',{name:'终止中…',exact:true}).isDisabled());
Object.assign(rows[0],{status:'blocked',verdict:'blocked',fail_kind:'cancelled',cancelled:true,cancel_requested:false});
await running.getByText('已终止',{exact:true}).waitFor({timeout:12000});
assert.equal(await page.getByText('补齐选择器',{exact:true}).count(),0);
assert.equal(await running.getByText('重试',{exact:true}).count(),1);
await page.screenshot({path:process.env.UI_SCREENSHOT||'backend/artifacts/exec-cancel-20260921/cancel-ui.png',fullPage:true});
reader=true; rows[0]={...rows[0],status:'running',fail_kind:null,verdict:null,cancelled:false};
await page.reload();await running.getByText('执行中',{exact:true}).waitFor();
assert.equal(await page.getByRole('button',{name:'手动终止',exact:true}).count(),0);
assert.deepEqual(errors,[]);assert.equal(writes.length,3);
console.log('PASS cancel UI: failure retry, pending cancel, running request/ack poll, no selector fix, member/viewer permissions');
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});