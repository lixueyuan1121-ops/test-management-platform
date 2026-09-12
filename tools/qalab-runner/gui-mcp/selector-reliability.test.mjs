import { test, before, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { CAPTURE_INIT, DRAIN_SCRIPT, ACK_SCRIPT, STOP_SCRIPT, rawEventToStep, dedupeSteps } from '../record-capture.mjs';
import { createAutomationRuntime } from './runtime-loader.mjs';

let browser, context, page;
before(async () => { browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { context = await browser.newContext(); page = await context.newPage(); });
afterEach(async () => { await context.close(); });

test('Alt 选择断言不触发 pointer 或 click 业务处理，普通点击仍可执行', async () => {
  await page.setContent('<button data-testid="delete">删除</button>');
  await page.evaluate(CAPTURE_INIT, { sessionId: '1' });
  await page.evaluate(() => { window.actions = []; const b = document.querySelector('button');
    for (const event of ['pointerdown', 'mousedown', 'click']) b.addEventListener(event, () => actions.push(event)); });
  await page.getByTestId('delete').click({ modifiers: ['Alt'] });
  assert.deepEqual(await page.evaluate(() => actions), []);
  const events = await page.evaluate(DRAIN_SCRIPT);
  assert.equal(events.length, 1);
  assert.equal(rawEventToStep(events[0], 'shell').action, 'assert');
  await page.getByTestId('delete').click();
  assert.deepEqual(await page.evaluate(() => actions), ['pointerdown', 'mousedown', 'click']);
});

test('非标准 testid 属性与完整 role 元数据可直接回放', async () => {
  await page.setContent('<button data-test-id="save">保存</button><button>取消</button>');
  await page.evaluate(CAPTURE_INIT, { sessionId: '1' });
  await page.locator('[data-test-id=save]').click();
  const [ev] = await page.evaluate(DRAIN_SCRIPT);
  assert.ok(ev.candidates.some(c => c.by === 'css' && c.value.includes('data-test-id')));
  assert.ok(ev.candidates.some(c => c.by === 'role' && c.name === '保存' && c.exact === true));
  assert.ok(!ev.candidates.some(c => c.by === 'testid'));
  const runtime = createAutomationRuntime({ page, registry: { save: { frame: 'shell', candidates: ev.candidates } }, timeout: 100 });
  assert.equal((await runtime.assertVisible({ key: 'save' })).pass, true);
});

test('重复注入和停止保留未确认事件；真实连击保留；确认后清除', async () => {
  await page.setContent('<button data-testid="add">加一</button>');
  await page.evaluate(CAPTURE_INIT, { sessionId: '1' });
  await page.getByTestId('add').click();
  await page.evaluate(CAPTURE_INIT, { sessionId: '1' });
  await page.getByTestId('add').click();
  await page.evaluate(STOP_SCRIPT);
  await page.getByTestId('add').click();
  const events = await page.evaluate(DRAIN_SCRIPT);
  assert.equal(events.length, 2);
  assert.equal(dedupeSteps(events.map(e => rawEventToStep(e))).length, 2);
  assert.equal((await page.evaluate(DRAIN_SCRIPT)).length, 2);
  await page.evaluate(ACK_SCRIPT, events.map(e => e.event_id));
  assert.equal((await page.evaluate(DRAIN_SCRIPT)).length, 0);
});

test('精确语义候选优先于宽泛 CSS，隐藏旧候选不遮挡可见候选', async () => {
  await page.setContent('<button class="common">取消</button><button class="common">保存</button><button data-testid="old" hidden>旧</button>');
  const runtime = createAutomationRuntime({ page, registry: { save: { frame: 'shell', candidates: [
    { by: 'css', value: '.common' }, { by: 'role', value: 'button', name: '保存', exact: true }, { by: 'testid', value: 'old' },
  ] } }, timeout: 100 });
  await page.evaluate(() => document.querySelectorAll('button').forEach(b => b.onclick = () => window.picked = b.textContent));
  await runtime.click({ key: 'save' });
  assert.equal(await page.evaluate(() => window.picked), '保存');
});

test('auto 按候选顺序跨 frame 定位，并拒绝跨 frame 重复目标', async () => {
  await page.setContent('<button id="legacy">壳</button><iframe id="vm"></iframe>');
  await page.frames()[1].setContent('<button data-testid="primary">业务</button>');
  const runtime = createAutomationRuntime({ page, vmIframe: '#vm', registry: { save: { frame: 'auto', candidates: [
    { by: 'testid', value: 'primary' }, { by: 'css', value: '#legacy' },
  ] } }, timeout: 100 });
  assert.equal((await runtime.getText({ key: 'save' })).text, '业务');
  await page.locator('#legacy').evaluate(el => el.setAttribute('data-testid', 'primary'));
  const ambiguous = createAutomationRuntime({ page, vmIframe: '#vm', registry: { save: { frame: 'auto', candidates: [{ by: 'testid', value: 'primary' }] } } });
  await assert.rejects(ambiguous.click({ key: 'save' }), { code: 'AMBIGUOUS_TARGET' });
});

test('浏览器探测与录制对三种测试属性、带引号的值保持相同定位身份', async () => {
  const { DISCOVER_SCRIPT } = await import('./gui-core.mjs');
  const { candidateIdentity } = await import('./runtime-loader.mjs');
  await page.setContent('<button>保存</button>');
  for (const attr of ['data-testid', 'data-test-id', 'data-test']) {
    await page.locator('button').evaluate((el, attr) => {
      for (const a of ['data-testid', 'data-test-id', 'data-test']) el.removeAttribute(a);
      el.setAttribute(attr, 'save"quote');
    }, attr);
    await page.evaluate(CAPTURE_INIT, { sessionId: attr });
    await page.locator('button').click();
    const [record] = await page.evaluate(DRAIN_SCRIPT);
    const [probe] = await page.evaluate(DISCOVER_SCRIPT);
    assert.equal(candidateIdentity(record.candidates[0]), candidateIdentity(probe.candidates[0]));
    const runtime = createAutomationRuntime({ page, registry: { x: { frame: 'shell', candidates: [probe.candidates[0]] } } });
    assert.equal((await runtime.assertVisible({ key: 'x' })).pass, true);
  }
});

test('open Shadow DOM 里的控件可以捕获并按语义候选回放', async () => {
  await page.setContent('<div id="host"></div>');
  await page.evaluate(() => document.querySelector('#host').attachShadow({ mode: 'open' }).innerHTML = '<button data-testid="shadow-save">保存</button>');
  await page.evaluate(CAPTURE_INIT, { sessionId: 'shadow' });
  await page.getByTestId('shadow-save').click({ modifiers: ['Alt'] });
  const [record] = await page.evaluate(DRAIN_SCRIPT);
  assert.equal(record.tag, 'button');
  const runtime = createAutomationRuntime({ page, registry: { x: { frame: 'shell', candidates: record.candidates } } });
  assert.equal((await runtime.assertVisible({ key: 'x' })).pass, true);
});


test('表单录制经 API 保存、注册表快照和 StepExecutor 回放保留勾选/下拉/未失焦输入', async () => {
  const html = '<input data-testid="name"><input type="checkbox" data-testid="agree"><select data-testid="choice"><option value="a">A</option><option value="b">B</option></select><button data-testid="save">保存</button><p data-testid="status">成功</p>';
  await page.setContent(html);
  await page.evaluate(CAPTURE_INIT, {sessionId:'1'});
  await page.getByTestId('agree').check();
  await page.getByTestId('choice').selectOption('b');
  await page.getByTestId('name').fill('尚未失焦的输入');
  await page.getByTestId('status').click({modifiers:['Alt']});
  await page.getByTestId('name').fill('最后输入');
  await page.evaluate(STOP_SCRIPT);
  const events = dedupeSteps((await page.evaluate(DRAIN_SCRIPT)).map(ev => rawEventToStep(ev, 'shell')));
  assert.ok(events.some(e => e.action === 'set_checked' && e.checked));
  assert.ok(events.some(e => e.action === 'select_option' && e.values[0] === 'b'));
  assert.equal(events.at(-1).value, '最后输入');
  const {execFileSync} = await import('node:child_process');
  const {fileURLToPath} = await import('node:url');
  const backend = fileURLToPath(new URL('../../../backend/', import.meta.url));
  const fixture = `import json,sys
from scripts.test_selector_reliability import SelectorReliability
from app.api.exec_queue import _payload_of
from app.models import TestCase
h=SelectorReliability(); h.setUp()
try:
 events=json.load(sys.stdin); sid=h.start()
 r=h.upload(sid,events); assert r.status_code==200,r.text
 h.client.post(f'/api/record/{sid}/stop')
 r=h.upload(sid,events,final=True); assert r.status_code==200,r.text
 r=h.client.post(f'/api/record/{sid}/save-as-case',json={'title':'Form','task_id':1}); assert r.status_code==200,r.text
 payload=_payload_of(h.db.get(TestCase,r.json()['data']['id']),h.db)
 print(json.dumps(payload,ensure_ascii=False))
finally: h.tearDown()`;
  const payload = JSON.parse(execFileSync(backend+'.venv/bin/python', ['-B','-c',fixture], {cwd:backend, input:JSON.stringify(events), encoding:'utf8', stdio:['pipe','pipe','pipe']}));
  await page.setContent(html);
  const runtime = createAutomationRuntime({page, registry:payload.selector_registry.registry, vmIframe:payload.selector_registry.vmIframe, timeout:200});
  const {runScript} = await import('../step-executor.mjs');
  const result = await runScript({...runtime, connect:async()=>({})}, typeof payload.script === 'string' ? JSON.parse(payload.script) : payload.script);
  assert.equal(result.verdict, 'pass', JSON.stringify(result));
  assert.equal(await page.getByTestId('agree').isChecked(), true);
  assert.equal(await page.getByTestId('choice').inputValue(), 'b');
  assert.equal(await page.getByTestId('name').inputValue(), '最后输入');
});

test('frame 路径去掉动态参数，保留不同业务路径并拒绝重复路径目标', async () => {
  const {recordingFrame} = await import('./gui-core.mjs');
  const frame = {url:()=> 'https://same.example/orders?token=temporary#panel'};
  assert.equal(recordingFrame(frame, {}, {}), 'url:https://same.example/orders');
  await context.route('https://same.example/**', route => route.fulfill({body:'<button data-testid="save">保存</button>', contentType:'text/html'}));
  await page.setContent('<iframe src="https://same.example/orders?session=1"></iframe><iframe src="https://same.example/profile"></iframe>');
  await page.frames()[1].waitForLoadState();
  const runtime = createAutomationRuntime({page, registry:{x:{frame:'url:https://same.example/orders', candidates:[{by:'testid',value:'save'}]}}, timeout:200});
  assert.equal((await runtime.assertVisible({key:'x'})).pass,true);
  await page.locator('iframe').nth(1).evaluate(el => el.src='https://same.example/orders?session=2');
  await page.frames()[2].waitForURL('**/orders?session=2');
  await assert.rejects(runtime.click({key:'x'}), {code:'INVALID_FRAME'});
});


test('嵌套 within 限定正确容器；配置 iframe 缺失不会误用外壳同名元素', async () => {
  await page.setContent('<section data-testid="section"><div data-testid="row">甲<button data-testid="edit">编辑</button></div><div data-testid="row">乙<button data-testid="edit">编辑</button></div></section>');
  const registry=Object.fromEntries(['section','row','edit'].map(key=>[key,{frame:'shell',candidates:[{by:'testid',value:key}]}]));
  const runtime=createAutomationRuntime({page,registry,timeout:50});
  const hit=await runtime.inspect({key:'edit',within:{key:'row',has_text:'乙',within:{key:'section'}}});
  assert.equal(await hit.loc.evaluate(el=>el.parentElement.textContent),'乙编辑');
  const missing=createAutomationRuntime({page,vmIframe:'#missing-frame',registry:{edit:{...registry.edit,frame:'vm'}},timeout:20});
  await assert.rejects(missing.click({key:'edit'}),{code:'INVALID_FRAME'});
});

test('保存验证确认是原来选中的 DOM 元素，同文案其他按钮及重渲染旧截图都拒绝', async () => {
  const {createGuiCore} = await import('./gui-core.mjs');
  await context.route('https://selection-fixture.invalid/**', route => route.fulfill({contentType:'text/html; charset=utf-8',body:'<button id="one">保存</button><button id="two">保存</button>'}));
  await page.goto('https://selection-fixture.invalid/');
  await page.evaluate(()=>localStorage.setItem('openclaw.testids','1'));
  const original=chromium.connectOverCDP;
  chromium.connectOverCDP=async()=>({isConnected:()=>true,contexts:()=>[context],close:async()=>{}});
  const gui=createGuiCore({registry:{},vmIframe:'',timeout:100});
  try {
    const probe=await gui.probe();
    const element=probe.groups[0].elements.find(e=>e.candidates.some(c=>c.value==='#one'));
    const item={key:'save',frame:'shell',element_ref:element.element_ref,expected:{tag:'button',text:'保存'}};
    const wrong=await gui.validateSelection({items:[{...item,candidates:[{by:'css',value:'#two'}]}]});
    assert.equal(wrong.validation[0].ok,false);
    const good=await gui.validateSelection({items:[{...item,candidates:[{by:'css',value:'#one'}]}]});
    assert.equal(good.validation[0].identity_verified,true,JSON.stringify(good));
    assert.equal(good.validation[0].ok,true,JSON.stringify(good));
    await page.locator('#one').evaluate(el=>el.replaceWith(el.cloneNode(true)));
    const stale=await gui.validateSelection({items:[{...item,candidates:[{by:'css',value:'#one'}]}]});
    assert.equal(stale.validation[0].ok,false);
    gui.setRegistry({ambiguous:{frame:'shell',candidates:[{by:'role',value:'button',name:'保存',exact:true}]}},'');
    assert.equal((await gui.verifyKeys(['ambiguous'])).verify.ambiguous,false);
  } finally { await gui.close();chromium.connectOverCDP=original; }
});
