// Run after building to backend/artifacts/async-import/frontend-dist.
import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const require = createRequire(path.join(root, 'tools/qalab-runner/gui-mcp/package.json'))
const { chromium } = require('playwright-core')
const dist = path.join(root, 'backend/artifacts/async-import/frontend-dist')
const server = http.createServer((req, res) => {
  let file = path.join(dist, new URL(req.url, 'http://localhost').pathname)
  if (!file.startsWith(dist)) { res.writeHead(403).end(); return }
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html')
  res.setHeader('Content-Type', { '.js':'application/javascript', '.css':'text/css', '.html':'text/html' }[path.extname(file)] || 'application/octet-stream')
  fs.createReadStream(file).pipe(res)
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
let browser, activePage
try {
  browser = await chromium.launch({channel: process.env.QALAB_TEST_BROWSER || 'msedge', headless: true})
  const context = await browser.newContext({viewport:{width:1440,height:1100}})
  await context.addInitScript(() => localStorage.setItem('tp_token','isolated-ui-fixture'))
  const queries=[], errors=[], actions=[]
  let managed=true, resolved=false, retried=false
  const job=id=>({job_id:id,project_id:1,user_id:2,requirement:`首页返回验证 ${id}`,status:'needs_confirmation',counts:{pending:0,needs_confirmation:1,done:1,failed:1},case_count:3,created_cases:1,reused_cases:0,created_at:'2026-09-22T10:00:00'})
  const items=()=>[
    {id:1,title:'任务 Logo 返回首页',status:resolved?'pending':'needs_confirmation',attempts:1},
    {id:2,title:'专家 Logo 返回首页',status:'done',receipt:{case_id:2740,run_id:30,batch_id:'fixture',disposition:'created'}},
    {id:3,title:'项目 Logo 返回首页',status:retried?'pending':'failed',error:'后台处理失败，请管理员检查服务日志后重试',attempts:3},
  ]
  await context.route('**/api/**', async route=>{
    const request=route.request(), url=new URL(request.url()), p=url.pathname, q=url.searchParams
    queries.push(p+url.search); let data=[]
    if(p==='/api/auth/me') data={user:{id:1,username:'fixture',name:'测试'},is_platform_admin:managed,memberships:[]}
    else if(p==='/api/projects') data=[{id:1,name:'纳米Work PC端',code:'fixture'}]
    else if(p==='/api/verified-imports/jobs') {
      const start=(Number(q.get('page'))-1)*20
      data={items:Array.from({length:Math.min(20,21-start)},(_,i)=>job(start+i+1)),total:21,page:Number(q.get('page')),page_size:20}
    } else if(/^\/api\/verified-imports\/jobs\/\d+$/.test(p)) data={...job(Number(p.split('/').at(-1))),can_manage:managed,items:items()}
    else if(p.endsWith('/resolve')) {
      const choice=request.postDataJSON();assert.equal(choice.case_id,2739);assert.equal(choice.action,'reuse');assert.ok(choice.reason.length>=5)
      actions.push('resolve'); resolved=true; data={accepted:true}
    } else if(p.endsWith('/retry')) {actions.push('retry');retried=true;data={accepted:true}}
    else if(p.includes('/items/')) data={...items().find(i=>i.id===Number(p.split('/').at(-1))),source:{title:'任务 Logo 返回首页',steps:'打开任务侧栏，点击 Logo',expected:'产品首页可见',script:[],report:[]},plan:{confirmation_token:'a'.repeat(64),candidates:[{case_id:2739,title:'点击任务图标回首页',steps:'打开任务并点击顶部 Logo',expected:'产品首页可见',reason:'场景相似，需人工确认'}]}}
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,data})})
  })
  const page=await context.newPage();activePage=page;page.on('pageerror',e=>errors.push(String(e)))
  const base=`http://127.0.0.1:${server.address().port}`
  await page.goto(base+'/verified-imports?project_id=1')
  await page.waitForFunction(()=>document.querySelectorAll('.el-table__body-wrapper tbody tr').length===20)
  await page.locator('.el-pagination .btn-next').click()
  await page.waitForFunction(()=>document.querySelectorAll('.el-table__body-wrapper tbody tr').length===1)
  assert.ok(queries.some(q=>q.includes('page=2')))
  await page.getByText('全部状态',{exact:true}).click()
  await page.locator('.el-select-dropdown:visible').getByText('待确认',{exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('.el-pager .is-active')?.textContent.trim()==='1')
  await page.getByRole('button',{name:'查看详情',exact:true}).first().click()
  await page.getByRole('button',{name:'查看 / 对比',exact:true}).first().click()
  await page.getByRole('button',{name:'复用此用例',exact:true}).click()
  const confirm=page.getByRole('button',{name:'确认并交给后台',exact:true})
  assert.equal(await confirm.isDisabled(),true)
  await page.getByPlaceholder('请说明复用或独立建例的依据（至少 5 个字符）').fill('相同入口、操作和验收结果，确认复用')
  await page.screenshot({path:path.join(root,'backend/artifacts/async-import/confirmation.png'),fullPage:true})
  await confirm.click()
  await confirm.waitFor({state:'hidden'})
  await page.getByRole('button',{name:'重试',exact:true}).click()
  await page.waitForTimeout(400)
  assert.deepEqual(actions,['resolve','retry'])
  const before=queries.length;await page.waitForTimeout(6200);assert.equal(queries.length,before,'must not auto-refresh')
  await page.screenshot({path:path.join(root,'backend/artifacts/async-import/import-tasks.png'),fullPage:true})
  managed=false;resolved=false;retried=false
  await page.goto(base+'/verified-imports?project_id=1&job_id=1')
  await page.getByRole('button',{name:'查看 / 对比',exact:true}).first().waitFor()
  assert.equal(await page.getByRole('button',{name:'重试',exact:true}).count(),0)
  await page.getByRole('button',{name:'查看 / 对比',exact:true}).first().click()
  await page.getByRole('heading',{name:'本次提交：任务 Logo 返回首页'}).waitFor()
  assert.equal(await page.getByRole('button',{name:'确认并交给后台',exact:true}).count(),0)
  assert.deepEqual(errors,[])
  console.log('PASS: 20/page, filter reset, deep link, candidate confirmation, retry, member read-only, no polling or browser errors')
} catch(error) {
  await activePage?.screenshot({path:path.join(root,'backend/artifacts/async-import/ui-failure.png'),fullPage:true})
  throw error
} finally {await browser?.close();await new Promise(resolve=>server.close(resolve))}
