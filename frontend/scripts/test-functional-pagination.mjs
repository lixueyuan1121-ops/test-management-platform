// Run after building to backend/artifacts/verified-dedup-pagination/frontend-dist.
import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const require = createRequire(path.join(root, 'tools/qalab-runner/gui-mcp/package.json'))
const { chromium } = require('playwright-core')
const dist = path.join(root, 'backend/artifacts/verified-dedup-pagination/frontend-dist')
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
  browser = await chromium.launch({channel: process.env.QALAB_TEST_BROWSER || 'chrome', headless: true})
  const context = await browser.newContext({viewport:{width:1440,height:1100}})
  await context.addInitScript(() => localStorage.setItem('tp_token','isolated-ui-fixture'))
  const queries = [], errors = []
  let remaining = 45
  await context.route('**/api/**', async route => {
    const url = new URL(route.request().url()), q = url.searchParams
    let data = []
    if (url.pathname === '/api/auth/me') data={user:{id:1,username:'fixture',name:'测试'},is_platform_admin:true,memberships:[]}
    else if (url.pathname === '/api/projects') data=[{id:1,name:'测试项目',code:'fixture'}]
    else if (url.pathname === '/api/ai/cases') {
      queries.push({path:url.pathname,q:Object.fromEntries(q)})
      assert.equal(q.get('limit'),'20')
      const offset=Number(q.get('offset'))
      data={total:remaining,items:Array.from({length:Math.max(0,Math.min(20,remaining-offset))},(_,i)=>({id:offset+i+1,title:`场景 ${offset+i+1}`,steps:'操作',expected:'预期',exec_kind:'gui',review_status:'adopted',task_id:1,project_id:1}))}
    } else if (url.pathname.startsWith('/api/ai/testcases/') && route.request().method()==='PATCH') {
      remaining--; data={}
    } else if (url.pathname === '/api/exec-queue/history') {
      queries.push({path:url.pathname,q:Object.fromEntries(q)})
      assert.equal(q.get('page_size'),'20')
      const offset=(Number(q.get('page'))-1)*20
      data={total:45,runners:['windows','mac'],items:Array.from({length:Math.min(20,45-offset)},(_,i)=>({run_id:offset+i+1,case_id:1,project_id:1,batch_id:'fixture-batch',runner:'windows',kind:'gui',title:`运行 ${offset+i+1}`,status:i===0&&offset===0?'running':'passed',verdict:i===0&&offset===0?null:'pass',can_cancel:true,created_at:'2026-09-21T12:00:00',duration_ms:100,has_report:true}))}
    }
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({code:0,msg:'ok',data})})
  })
  const page = await context.newPage(); activePage = page
  page.on('pageerror', error => errors.push(String(error)))
  const base=`http://127.0.0.1:${server.address().port}`
  const rowCount=async expected=>{await page.waitForFunction(n=>document.querySelectorAll('.el-table__body-wrapper tbody tr').length===n,expected)}
  for (const view of ['case-library','adopted-cases','exec-results']) {
    console.log('TEST',view); remaining=45
    await page.goto(base+'/'+view)
    await rowCount(20)
    await page.locator('.el-pagination .btn-next').click()
    await page.waitForFunction(()=>document.querySelector('.el-pager .is-active')?.textContent.trim()==='2')
    await rowCount(20)
    let last=queries.at(-1).q
    assert.equal(view==='exec-results'?last.page:last.offset, view==='exec-results'?'2':'20')
    await page.locator('.el-pagination .btn-next').click()
    await rowCount(5)
    if (view!=='exec-results') {
      const search=page.getByPlaceholder('按测试点搜索')
      // Use the observed input label in each view rather than relying on request state.
      const input=await search.count()?search:page.locator('input[placeholder]').filter({visible:true}).last()
      if(view==='adopted-cases'){
        for(let i=0;i<5;i++){await page.getByText('移除采纳',{exact:true}).first().click();await page.waitForTimeout(150)}
        await rowCount(20)
        assert.equal(queries.at(-1).q.offset,'20')
      }
      await input.fill('首页');await input.press('Enter')
      await page.waitForFunction(()=>document.querySelector('.el-pager .is-active')?.textContent.trim()==='1')
      await rowCount(20);assert.equal(queries.at(-1).q.offset,'0')
    } else {
      await page.getByText('执行状态', {exact:true}).click()
      await page.locator('.el-select-dropdown:visible').getByText('执行中',{exact:true}).click()
      await rowCount(20)
      assert.equal(queries.at(-1).q.page,'1')
      assert.equal(queries.at(-1).q.status,'running')
      assert.equal(await page.getByRole('button',{name:'手动终止',exact:true}).count(),1)
      const calls=queries.length;await page.waitForTimeout(6200);assert.equal(queries.length,calls,'unexpected auto refresh')
    }
    await page.screenshot({path:path.join(root,`backend/artifacts/verified-dedup-pagination/${view}.png`),fullPage:true})
    console.log('PASS',view,'20/20/5, page changes and filter reset')
  }
  await page.goto(base+'/exec-results?project_id=1&batch_id=fixture-batch')
  await rowCount(20);assert.equal(queries.at(-1).q.batch_id,'fixture-batch')
  assert.deepEqual(errors,[])
  console.log('PASS deep link, removal page fallback, stop button, no polling, no browser errors')
} catch(error) { if(activePage) { console.log(await activePage.locator('.el-pagination').innerText()); await activePage.screenshot({path:path.join(root,'backend/artifacts/verified-dedup-pagination/ui-failure.png'),fullPage:true}) }; throw error } finally { await browser?.close(); await new Promise(resolve=>server.close(resolve)) }
