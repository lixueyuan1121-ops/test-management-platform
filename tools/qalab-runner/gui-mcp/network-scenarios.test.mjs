import { test, before, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { chromium } from 'playwright-core';
import { createAutomationRuntime } from './runtime-loader.mjs';
import { createNetworkScenarios } from './network-scenarios.mjs';
import { runScript } from '../step-executor.mjs';
import { executeGui } from '../gui-execution.mjs';
import { makeEvidence, runtimeFingerprint, fingerprint } from '../execution-evidence.mjs';
import { writeFileSync, readFileSync } from 'node:fs';

const html = `<!doctype html><input id="name" value="原始内容"><button id="publish">发布</button><button id="home">首页</button><button id="retry">重试</button><div id="state">编辑</div><ul id="list"></ul><script>
let busy=false, generation=0;
const state=document.querySelector('#state');
async function load(){state.textContent='我的技能';try{const r=await fetch('/list?page=1');if(!r.ok)throw Error();const d=await r.json();document.querySelector('#list').innerHTML=d.data.list.map(x=>'<li>'+x.name+'</li>').join('');}catch{state.textContent='我的技能-读取失败';}}
document.querySelector('#publish').onclick=async()=>{if(busy)return;busy=true;const g=generation;try{const r=await fetch('/publish',{method:'POST'});if(!r.ok)throw Error();if(g===generation)await load();}catch{state.textContent='编辑-发布失败';}finally{busy=false;}};
document.querySelector('#home').onclick=()=>{generation++;state.textContent='首页';};document.querySelector('#retry').onclick=load;
</script>`;
let server, origin, browser, page, context, gui, network, runtime;
const verifiedCases = [];
before(async () => {
  server = createServer((req, res) => {
    if (req.url.startsWith('/list')) { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ data: { list: [{ name: '旧技能乙' }, { name: '新技能' }, { name: '旧技能甲' }] } })); }
    else if (req.url === '/publish') { res.setHeader('Content-Type', 'application/json'); res.end('{"code":0}'); }
    else { res.setHeader('Content-Type', 'text/html;charset=utf-8'); res.end(html); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE });
});
after(async () => { await browser?.close(); await new Promise(resolve => server.close(resolve)); });
beforeEach(async () => {
  context = await browser.newContext(); page = await context.newPage(); await page.goto(origin);
  runtime = createAutomationRuntime({ page, registry: {}, timeout: 1000, pollMs: 10 });
  network = createNetworkScenarios(() => page, runtime);
  gui = { ...runtime, connect: async () => ({ connected: true }), networkStep: (...a) => network.execute(...a), cleanupNetworkScenarios: () => network.cleanup() };
});
afterEach(async () => { await network.cleanup().catch(() => {}); await context.close(); });
const st = (action, args = {}, selector) => ({ action, args, ...(selector ? { target: { selector, frame: 'shell' } } : {}) });
const watch = (id, path, method = 'POST', extra = {}) => st('watch_network', { id, path, method, frame: 'shell', ...extra });
const fault = (id, path, mode, extra = {}, method = 'POST') => st('fault_route', { id, path, method, frame: 'shell', mode, ...extra });
const wait = (id, phase = 'completed') => st('wait_network', { id, phase, count: 1, timeout_ms: 2000 });
const text = expected => st('assert_text', { expected }, '#state');
const count = expected => st('assert_network_count', { id: 'pub', expected, settle_ms: 80 });
const click = selector => st('click', {}, selector);
const hits = id => st('assert_fault_hits', { id, expected: 1 });
const release = id => st('release_fault', { id });

const scenarios = {
  '发布失败保留内容': [watch('pub', '/publish'), fault('fail', '/publish', 'network_error'), click('#publish'), wait('pub'), text('编辑-发布失败'), st('assert_text', { expected: '原始内容' }, '#name'), hits('fail'), release('fail')],
  '列表重试不重复发布': [watch('pub', '/publish'), fault('list', '/list', 'response', { status: 503, body: { error: 'unavailable' } }, 'GET'), click('#publish'), text('我的技能-读取失败'), hits('list'), release('list'), click('#retry'), text('我的技能'), st('assert_visible', {}, '#list li:first-child'), count(1)],
  '空列表不回推荐': [fault('empty', '/list', 'response', { status: 200, body: { data: { list: [] } } }, 'GET'), click('#publish'), text('我的技能'), st('assert_absent', {}, '#list li'), hits('empty'), release('empty')],
  '迟到结果不跳转': [watch('pub', '/publish'), fault('late', '/publish', 'hold_response', { timeout_ms: 3000 }), click('#publish'), wait('pub', 'received'), click('#home'), hits('late'), release('late'), wait('pub'), count(1), text('首页')],
  '双击一次发布及接口顺序': [watch('pub', '/publish'), watch('list', '/list', 'GET', { query: { page: '1' }, response_path: 'data.list', item_field: 'name' }), st('click', { click_count: 2 }, '#publish'), wait('list'), count(1), st('assert_list_from_response', { id: 'list' }, '#list li')],
};
// The exact JSON is serialized between authoring and queue dispatch; two queue
// executions must both pass without an agent, outer hooks or manual cleanup.
for (const [title, script] of Object.entries(scenarios)) test(title + '：同一 JSON 脚本连续两次严格队列重放', async () => {
  const attempts = [];
  for (let i = 0; i < 2; i++) {
    const result = await executeGui({ payload: { script: JSON.parse(JSON.stringify(script)), strict_replay: true },
      reset: async () => { await page.goto(origin); return { ok: true, reset: true }; },
      scriptRun: s => runScript(gui, s), agentRun: () => { throw Error('不允许 AI 兜底'); } });
    assert.equal(result.verdict, 'pass', result.reason); assert.equal(result.execution.mode, 'script');
    assert.equal(result.report.length, script.length);
    for (const r of result.report.filter(r => r.action.startsWith('assert_'))) assert.ok(r.check && 'actual' in r.check && 'expected' in r.check);
    assert.equal(await page.evaluate(() => Object.keys(window).some(k => k.startsWith('__qalab_network_'))), false);
    attempts.push(result);
  }
  const last = attempts[1], payload = { script, selector_registry: {} };
  verifiedCases.push({ title, steps: title, expected: title, exec_kind: 'gui', verdict: 'pass', executor: 'QA Lab Runner / StepExecutor',
    environment: 'isolated loopback browser fixture, not Namiwork acceptance', scope: title,
    script, report: last.report, duration_ms: last.duration_ms, finished_at: new Date().toISOString(), selector_registry: {},
    execution_evidence: { ...makeEvidence(payload, last.report, runtimeFingerprint(), last.execution), repeat_count: 2, repeat_report_sha256: attempts.map(a => fingerprint(a.report)) } });
  if (process.env.QALAB_NETWORK_TEST_PACKET) writeFileSync(process.env.QALAB_NETWORK_TEST_PACKET, JSON.stringify({ project_id: 1, runner_device_id: 1,
    external_id: 'network-scenarios-fixture', requirement: 'isolated executable import protocol regression', cases: verifiedCases }, null, 2));
});

test('未命中模拟不能假通过；失败时仍恢复 fetch', async () => {
  const r = await runScript(gui, [fault('unused', '/unused', 'network_error'), text('编辑')]);
  assert.equal(r.verdict, 'fail'); assert.match(r.reason, /hits=0/);
  assert.equal(await page.evaluate(async () => (await fetch('/publish', { method: 'POST' })).status), 200);
});
test('用例早退时释放暂停响应且不污染下条', async () => {
  const r = await runScript(gui, [fault('late', '/publish', 'hold_response', { timeout_ms: 3000 }), click('#publish'), text('不可能')]);
  assert.equal(r.verdict, 'fail');
  assert.equal(await page.evaluate(() => Object.keys(window).some(k => k.startsWith('__qalab_network_'))), false);
});
test('请求计数不能在短暂等于 1 时提前通过', async () => {
  await network.execute('watch_network', { id: 'pub', path: '/publish', method: 'POST', frame: 'shell' });
  await page.evaluate(async () => { await fetch('/publish', { method: 'POST' }); setTimeout(() => fetch('/publish', { method: 'POST' }), 40); });
  const r = await network.execute('assert_network_count', { id: 'pub', expected: 1, settle_ms: 100 });
  assert.equal(r.pass, false); assert.equal(r.actual, 2);
});
test('列表接口缺失不能拿空页面当通过', async () => {
  await network.execute('watch_network', { id: 'list', path: '/list', method: 'GET', frame: 'shell', response_path: 'data.list', item_field: 'name' });
  await assert.rejects(network.execute('assert_list_from_response', { id: 'list', allow_empty: true }, { selector: '#list li', frame: 'shell' }), /未完整捕获/);
});
test('旧 Runner 能力缺失在第一步操作前阻塞', async () => {
  const old = { connect() { throw Error('不得调用'); } };
  const r = await runScript(old, [st('connect'), ...scenarios['空列表不回推荐']]);
  assert.equal(r.verdict, 'fail'); assert.equal(r.report.length, 0); assert.match(r.reason, /更新 Runner/);
});

test('平台导入后下发的五份 payload 可直接执行', { skip: !process.env.QALAB_NETWORK_QUEUE_INPUT }, async () => {
  const payloads = JSON.parse(readFileSync(process.env.QALAB_NETWORK_QUEUE_INPUT, 'utf8'));
  assert.equal(payloads.length, 5);
  for (const payload of payloads) {
    const result = await executeGui({ payload,
      reset: async () => { await page.goto(origin); return { ok: true, reset: true }; },
      scriptRun: s => runScript(gui, s), agentRun: () => { throw Error('平台下发不得触发 AI'); } });
    assert.equal(result.verdict, 'pass', `${payload.title}: ${result.reason}`);
    assert.equal(result.execution.mode, 'script');
  }
});
