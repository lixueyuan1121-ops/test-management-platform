import { test, before, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { createAutomationRuntime, responseArgsBeforeAction } from './runtime-loader.mjs';
import { runScript } from '../step-executor.mjs';

let browser, context, page, runtime;
const entry = (value, frame = 'shell') => ({ frame, candidates: [{ by: 'testid', value }] });
const registry = {
  status: entry('status'), row: entry('row'), button: entry('button'), input: entry('input'),
  abortButton: entry('abort-button'), chatMsgActions: entry('chat-msg-actions'),
};
before(async () => { browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE }); });
after(async () => { await browser?.close(); });
beforeEach(async () => {
  context = await browser.newContext(); page = await context.newPage();
  runtime = createAutomationRuntime({ page, registry, timeout: 500, pollMs: 15 });
});
afterEach(async () => { await context?.close(); });

test('text assertion waits for asynchronous business state and preserves negation', async () => {
  await page.setContent('<div data-testid="status">pending</div>');
  await page.evaluate(() => { setTimeout(() => document.querySelector('div').textContent = 'ready', 80); });
  assert.equal((await runtime.assertText({ key: 'status', expected: 'ready' })).pass, true);
  assert.equal((await runtime.assertText({ key: 'status', expected: 'error', negate: true })).pass, true);
  assert.equal((await runtime.assertText({ key: 'status', expected: 'ready', negate: true, timeout_ms: 25 })).pass, false);
});

test('visibility waits for rendering; valid missing target is a business assertion failure', async () => {
  await page.setContent('<div data-testid="status" hidden>ready</div>');
  await page.evaluate(() => { setTimeout(() => document.querySelector('div').hidden = false, 60); });
  assert.equal((await runtime.assertVisible({ key: 'status' })).pass, true);
  await page.setContent('');
  const result = await runtime.assertVisible({ key: 'status', timeout_ms: 25 });
  assert.equal(result.pass, false);
  assert.equal(result.fail_kind, 'business');
});

test('absence waits for removal and checks all duplicates, not only a hidden first element', async () => {
  await page.setContent('<div data-testid="status" hidden>hidden</div><div data-testid="status">visible</div>');
  assert.equal((await runtime.assertAbsent({ key: 'status', timeout_ms: 25 })).pass, false);
  await page.evaluate(() => { setTimeout(() => document.querySelectorAll('div')[1].remove(), 40); });
  assert.equal((await runtime.assertAbsent({ key: 'status' })).pass, true);
});

test('absence checks fallback candidates too', async () => {
  await page.setContent('<div data-testid="old" hidden></div><div data-testid="new">visible</div>');
  const rt = createAutomationRuntime({ page, registry: { x: { frame: 'shell', candidates: [{ by: 'testid', value: 'old' }, { by: 'testid', value: 'new' }] } }, timeout: 30 });
  assert.equal((await rt.assertAbsent({ key: 'x' })).pass, false);
});

test('invalid selectors, missing keys and closed pages cannot pass absence', async () => {
  await assert.rejects(runtime.assertAbsent({ selector: '[' }), /selector|Unexpected|parsing/i);
  await assert.rejects(runtime.assertAbsent({ key: 'missing' }), { code: 'UNKNOWN_KEY' });
  await page.close();
  await assert.rejects(runtime.assertAbsent({ key: 'status' }), /closed/i);
});

test('duplicate actions fail without clicking; within and explicit nth target the right record', async () => {
  await page.setContent('<div data-testid="row">Alice<button data-testid="button" onclick="this.textContent=\'clicked\'">Edit</button></div><div data-testid="row">Bob<button data-testid="button" onclick="this.textContent=\'clicked\'">Edit</button></div>');
  await assert.rejects(runtime.click({ key: 'button' }), { code: 'AMBIGUOUS_TARGET' });
  assert.deepEqual(await page.getByTestId('button').allTextContents(), ['Edit', 'Edit']);
  await runtime.click({ key: 'button', within: { key: 'row', has_text: 'Bob' } });
  assert.deepEqual(await page.getByTestId('button').allTextContents(), ['Edit', 'clicked']);
  await runtime.click({ key: 'button', nth: 0 });
  assert.deepEqual(await page.getByTestId('button').allTextContents(), ['clicked', 'clicked']);
  await assert.rejects(runtime.assertAbsent({ key: 'button', within: { key: 'row', has_text: 'Nobody' } }), { code: 'CONTAINER_MISSING' });
});

test('candidate priority wins over DOM order', async () => {
  await page.setContent('<button id="legacy">legacy</button><button data-testid="preferred">preferred</button>');
  const rt = createAutomationRuntime({ page, registry: { x: { frame: 'shell', candidates: [{ by: 'testid', value: 'preferred' }, { by: 'css', value: '#legacy' }] } } });
  assert.equal((await rt.getText({ key: 'x' })).text, 'preferred');
});

test('raw selectors and vm keys use the same iframe; explicit URL frames do not fall back', async () => {
  await page.setContent('<div class="target">shell</div><iframe id="vm" srcdoc="&lt;div class=target data-testid=status&gt;inside&lt;/div&gt;"></iframe>');
  const rt = createAutomationRuntime({ page, registry: { status: entry('status', 'vm') }, vmIframe: '#vm', timeout: 500 });
  assert.equal((await rt.getText({ key: 'status' })).text, 'inside');
  assert.equal((await rt.getText({ selector: '.target' })).text, 'inside');
  await assert.rejects(rt.assertAbsent({ selector: '.target', frame: 'url:missing-frame' }), { code: 'INVALID_FRAME' });
});

test('targeted press focuses the specified input, and fill preserves multiline text', async () => {
  await page.setContent('<input id="other"><textarea data-testid="input"></textarea>');
  await page.locator('#other').focus();
  await runtime.fill({ key: 'input', text: 'line1\nline2' });
  await page.locator('#other').focus();
  await runtime.pressKey({ key: 'input', key_name: 'End' });
  await runtime.type({ key: 'input', text: '!' });
  assert.equal(await page.getByTestId('input').inputValue(), 'line1\nline2!');
  assert.equal(await page.locator('#other').inputValue(), '');
});

test('rich input fallback fills a unique editable child; ambiguity cannot append text elsewhere', async () => {
  await page.setContent('<div data-testid="input"><textarea>old</textarea></div>');
  await runtime.fill({ key: 'input', text: 'new' });
  assert.equal(await page.locator('textarea').inputValue(), 'new');
  await page.setContent('<div data-testid="input"><input><textarea>old</textarea></div>');
  await assert.rejects(runtime.fill({ key: 'input', text: 'new' }), { code: 'INVALID_INPUT' });
  assert.equal(await page.locator('textarea').inputValue(), 'old');
});

test('new registry response markers wait for this round and support fast completed replies', async () => {
  await page.setContent('<div data-testid="chat-msg-actions">old</div>');
  await runtime.captureResponse();
  assert.equal((await runtime.waitResponse({ timeout_ms: 35, stable_ms: 0 })).done, false);
  await runtime.captureResponse();
  await page.evaluate(() => { const d = document.createElement('div'); d.dataset.testid = 'chat-msg-actions'; d.textContent = 'new'; document.body.append(d); });
  assert.equal((await runtime.waitResponse({ timeout_ms: 50, stable_ms: 0 })).done, true);
  await assert.rejects(runtime.waitResponse(), { code: 'RESPONSE_BASELINE' });
});

test('generation must end even when the new reply toolbar already exists', async () => {
  await page.setContent('');
  await runtime.captureResponse();
  await page.setContent('<button data-testid="abort-button">stop</button><div data-testid="chat-msg-actions">new</div>');
  await page.evaluate(() => { setTimeout(() => document.querySelector('button').remove(), 70); });
  const r = await runtime.waitResponse({ timeout_ms: 400, stable_ms: 20 });
  assert.equal(r.done, true); assert.equal(r.saw_generating, true);
});

test('response configuration fails immediately and an active previous round blocks capture', async () => {
  const rt = createAutomationRuntime({ page, registry: {} });
  await assert.rejects(rt.captureResponse(), { code: 'RESPONSE_CONFIG' });
  await page.setContent('<button data-testid="abort-button">stop</button>');
  await assert.rejects(runtime.captureResponse(), { code: 'RESPONSE_BUSY' });
});

test('executor captures before submit, preserves waits, reports deterministic success', async () => {
  await page.setContent(`<button data-testid="button" onclick="document.body.insertAdjacentHTML('beforeend','<div data-testid=chat-msg-actions>done</div>')">send</button>`);
  const gui = { ...runtime, connect: async () => ({}), shotBuffer: async () => null };
  const script = [{ action: 'click', target: { key: 'button' } }, { action: 'wait_for', target: { key: 'chatMsgActions' } }, { action: 'wait_response', args: { timeout_ms: 200, stable_ms: 0 } }, { action: 'assert_visible', target: { key: 'chatMsgActions' } }];
  assert.deepEqual(responseArgsBeforeAction(script, 0), script[2].args);
  assert.equal((await runScript(gui, script)).verdict, 'pass');
});

test('real routing handles query strings, records hits and removes interception', async () => {
  await page.goto('about:blank');
  await runtime.mockRoute({ url: '**/api/items', body: { items: [1] } });
  const r = await page.evaluate(async () => (await fetch('https://fixture.invalid/api/items?page=1')).json());
  assert.deepEqual(r, { items: [1] });
  assert.equal(runtime.mockStats()[0].hits, 1);
  assert.equal((await runtime.unmockRoute({ url: '**/api/items' })).hits, 1);
  assert.deepEqual(runtime.mockStats(), []);
});

// Run the actual exported source against a real Page, replacing only the external
// CDP connection and the Playwright Test reporter shell. Runtime and steps remain
// exactly as downloaded from the platform.
async function exported(script, exportRegistry = registry, vmIframe = '') {
  const { execFileSync } = await import('node:child_process');
  const { fileURLToPath } = await import('node:url');
  const backend = fileURLToPath(new URL('../../../backend/', import.meta.url));
  return execFileSync(`${backend}.venv/bin/python`, ['-B', '-c', 'import json,sys; from app.services.playwright_exporter import export_case_to_playwright; d=json.load(sys.stdin); print(export_case_to_playwright(d["case"],d["registry"],d["vmIframe"]))'], {
    cwd: backend,
    input: JSON.stringify({ case: { title: 'export contract', exec_kind: 'gui', script }, registry: exportRegistry, vmIframe }),
    encoding: 'utf8',
  });
}
async function runExport(source, inspectAttachments = () => {}) {
  const { mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const directory = await mkdtemp(join(tmpdir(), 'qalab-export-test-'));
  let execute;
  const register = (title, fn) => { execute = fn; };
  register.setTimeout = () => {};
  register.step = async (title, fn) => fn();
  const attachments = [];
  globalThis.__qalabExportHarness = { test: register, chromium: { connectOverCDP: async () => ({ contexts: () => [context], close: async () => {} }) } };
  try {
    const code = source.replace("import { test, chromium } from '@playwright/test';", 'const { test, chromium } = globalThis.__qalabExportHarness;');
    await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Date.now()}-${Math.random()}`);
    try { await execute({}, { outputPath: (name) => join(directory, name), attach: async (name, info) => { attachments.push(info); } }); }
    finally { await inspectAttachments(attachments); }
  } finally {
    delete globalThis.__qalabExportHarness;
    await rm(directory, { recursive: true, force: true });
  }
}

test('exported negation and absence execute rather than becoming TODOs; failures produce a real trace', async () => {
  await page.setContent('<div data-testid="status">good</div>');
  await runExport(await exported([{ action: 'assert_text', target: { key: 'status' }, args: { expected: 'bad', negate: true, timeout_ms: 30 } }]));
  const source = await exported([{ action: 'assert_absent', target: { key: 'status' }, args: { timeout_ms: 30 } }]);
  let traced = false;
  await assert.rejects(runExport(source, async (attachments) => {
    const { readFile } = await import('node:fs/promises');
    assert.equal(attachments.length, 1);
    const zip = await readFile(attachments[0].path);
    traced = zip.subarray(0, 2).toString() === 'PK' && zip.length > 100;
  }), /断言失败/);
  assert.equal(traced, true);
});

test('exported submit captures the same baseline, preserves multiline fill and iframe scope', async () => {
  await page.setContent('<iframe id="vm"></iframe>');
  const frame = page.frames().find(f => f !== page.mainFrame());
  await frame.setContent(`<textarea data-testid="input"></textarea><button data-testid="button" onclick="document.body.insertAdjacentHTML('beforeend','<div data-testid=chat-msg-actions>done</div>')">send</button>`);
  const reg = Object.fromEntries(Object.entries(registry).map(([key, entry]) => [key, { ...entry, frame: 'vm' }]));
  const script = [
    { action: 'fill', target: { selector: 'textarea' }, args: { text: 'line1\nline2' } },
    { action: 'click', target: { key: 'button' } },
    { action: 'wait_response', args: { timeout_ms: 300, stable_ms: 0 } },
    { action: 'assert_visible', target: { key: 'chatMsgActions' } },
  ];
  await runExport(await exported(script, reg, '#vm'));
  assert.equal(await frame.locator('textarea').inputValue(), 'line1\nline2');
});

test('exported mocks use the same matcher and zero-hit mocks fail', async () => {
  await page.setContent('<div data-testid="status">ready</div>');
  const script = [{ action: 'mock_route', args: { url: '**/never-called', body: {} } },
    { action: 'assert_visible', target: { key: 'status' } },
    { action: 'unmock_route', args: { url: '**/never-called' } }];
  await assert.rejects(runExport(await exported(script)), /Mock 未命中/);
});

test('revealing an old hidden completion marker is not a new response', async () => {
  await page.setContent('<div data-testid="chat-msg-actions" hidden>old</div>');
  await runtime.captureResponse();
  await page.getByTestId('chat-msg-actions').evaluate(el => el.hidden = false);
  assert.equal((await runtime.waitResponse({ timeout_ms: 30, stable_ms: 0 })).done, false);
});

test('GUI adapter selects the configured iframe and releases trace on its CDP connection', async () => {
  const { createGuiCore } = await import('./gui-core.mjs');
  const { mkdtemp, readFile, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  await context.route('https://gui-fixture.invalid/**', (route) => route.fulfill({ contentType: 'text/html', body: '<iframe id="other"></iframe><iframe id="vm"></iframe>' }));
  await page.goto('https://gui-fixture.invalid/');
  const original = chromium.connectOverCDP;
  const directory = await mkdtemp(join(tmpdir(), 'qalab-gui-trace-test-'));
  chromium.connectOverCDP = async () => ({ isConnected: () => true, contexts: () => [context], close: async () => {} });
  const gui = createGuiCore({ registry, vmIframe: '#vm', timeout: 200 });
  try {
    await gui.ensureConnected();
    assert.equal(await gui.contentFrame(), page.frames()[2]);
    const vm = await gui.contentFrame();
    await vm.setContent('<button data-testid="record-button">Record</button>');
    await gui.startRecording();
    await vm.getByTestId('record-button').click();
    const recorded = await gui.drainRecordEvents();
    assert.ok(recorded.some(({ ev, frame }) => frame === 'vm' && ev.type === 'click'), JSON.stringify(recorded));
    await gui.stopRecording();
    await gui.startTrace();
    const path = join(directory, 'trace.zip');
    await gui.stopTrace(path);
    assert.equal((await readFile(path)).subarray(0, 2).toString(), 'PK');
    await assert.rejects(gui.stopTrace(path), /未启动/);
  } finally {
    await gui.close();
    chromium.connectOverCDP = original;
    await rm(directory, { recursive: true, force: true });
  }
});

test('switching projects and then falling back cannot retain the previous registry', async () => {
  const { createGuiCore } = await import('./gui-core.mjs');
  const first = { a: entry('a') };
  const gui = createGuiCore({ registry: first, vmIframe: '#first', coreKeys: ['a'] });
  gui.setRegistry({ b: entry('b') }, '#second', ['b']);
  assert.deepEqual(gui.coreKeys, ['b']);
  gui.setRegistry();
  assert.deepEqual(gui.registry, first);
  assert.deepEqual(gui.coreKeys, ['a']);
  assert.equal(gui.vmIframe, '#first');
});


test('upstream XPath candidates and form values survive the shared runtime and exporter merge', async () => {
  await page.setContent('<input id="input" value="old"><textarea id="textarea">stale</textarea><select id="select"><option value="current">Label</option></select>');
  const reg = Object.fromEntries(['input', 'textarea', 'select'].map((id) => [id, { frame: 'shell', candidates: [{ by: 'xpath', value: `//*[@id="${id}"]` }] }]));
  const rt = createAutomationRuntime({ page, registry: reg, timeout: 500, pollMs: 15 });
  await page.locator('#textarea').fill('new text');
  assert.equal((await rt.getText({ key: 'textarea' })).text, 'new text');
  assert.equal((await rt.getText({ key: 'select' })).text, 'current');
  await page.evaluate(() => setTimeout(() => document.querySelector('#input').value = '23', 50));
  assert.equal((await rt.assertText({ key: 'input', expected: '23' })).pass, true);
  await runExport(await exported([
    { action: 'assert_text', target: { key: 'input' }, args: { expected: '23' } },
    { action: 'assert_text', target: { key: 'textarea' }, args: { expected: 'new text' } },
    { action: 'assert_text', target: { key: 'select' }, args: { expected: 'current' } },
  ], reg));
});


test('表单状态动作在导出脚本中等价执行', async () => {
  await page.setContent('<input type="checkbox" data-testid="check"><select data-testid="select"><option value="a">A</option><option value="b">B</option></select>');
  const reg = {check:{frame:'shell',candidates:[{by:'testid',value:'check'}]}, select:{frame:'shell',candidates:[{by:'testid',value:'select'}]}};
  await runExport(await exported([
    {action:'set_checked',target:{key:'check'},args:{checked:true}},
    {action:'select_option',target:{key:'select'},args:{values:['b']}},
    {action:'assert_text',target:{key:'select'},args:{expected:'b'}},
  ], reg));
  assert.equal(await page.getByTestId('check').isChecked(),true);
  assert.equal(await page.getByTestId('select').inputValue(),'b');
});
