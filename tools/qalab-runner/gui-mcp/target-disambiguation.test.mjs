import { test, before, after, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { createAutomationRuntime } from './runtime-loader.mjs';
import { runScript } from '../step-executor.mjs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
let browser, context, page;
before(async () => { browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE }); });
after(async () => { await browser?.close(); });
beforeEach(async () => { context = await browser.newContext(); page = await context.newPage(); });
afterEach(async () => { await context.close(); });
const runtime = registry => createAutomationRuntime({ page, registry: registry || {}, timeout: 10000 });
test('MCP exposes read-only inspection and exact text to model tools', async () => {
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [fileURLToPath(new URL('./server.mjs', import.meta.url))], stderr: 'pipe' });
  const client = new Client({ name: 'target-contract-test', version: '1' });
  try {
    await client.connect(transport);
    const { tools } = await client.listTools();
    for (const name of ['gui_inspect', 'gui_click', 'gui_assert_visible']) {
      const tool = tools.find(t => t.name === name);
      assert.ok(tool, name);
      assert.equal(tool.inputSchema.properties.has_text_exact.type, 'boolean');
      assert.equal(tool.inputSchema.properties.within.type, 'object');
    }
  } finally { await client.close(); }
});
test('reproduce 18 XPath matches; read-only inspection and rejected click have no side effects', async () => {
  await page.setContent('<div>'.repeat(15) + '<button onclick="window.clicked=true">视觉PPT制作</button>' + '</div>'.repeat(15));
  const target = { selector: "xpath=//*[contains(normalize-space(.), '视觉PPT制作')]", frame: 'shell' };
  const gui = runtime();
  const check = await gui.inspectTarget(target);
  assert.equal(check.match_count, 18);
  assert.equal(check.unique, false);
  assert.equal(check.matches.length, 18);
  assert.ok(check.matches.some(item => item.tag === 'button'));
  await assert.rejects(gui.click(target), { code: 'AMBIGUOUS_TARGET' });
  assert.equal(await page.evaluate(() => !!window.clicked), false);
  const fixed = { selector: 'button', frame: 'shell', has_text: '视觉PPT制作', has_text_exact: true };
  assert.equal((await gui.inspectTarget(fixed)).unique, true);
  const result = await runScript(gui, [{ action: 'click', target: fixed }, { action: 'assert_visible', target: fixed }]);
  assert.equal(result.verdict, 'pass', JSON.stringify(result));
  assert.equal(await page.evaluate(() => window.clicked), true);
});
test('same testid + exact text distinguishes suffixes and escapes regex characters', async () => {
  await page.setContent('<button data-testid="skill">PPT (A+B)</button><button data-testid="skill">PPT (A+B) 企业版</button>');
  const gui = runtime({ skill: { frame: 'shell', candidates: [{ by: 'testid', value: 'skill' }] } });
  await assert.rejects(gui.click({ key: 'skill', has_text: 'PPT (A+B)' }), { code: 'AMBIGUOUS_TARGET' });
  assert.equal((await gui.getText({ key: 'skill', has_text: ' PPT  (A+B) ', has_text_exact: true })).text, 'PPT (A+B)');
});
test('same full name in separate tabs still fails until scoped to the intended tab', async () => {
  await page.setContent('<section id="mine"><button>视觉PPT制作</button></section><section id="market"><button>视觉PPT制作</button></section>');
  const gui = runtime();
  const target = { selector: 'button', frame: 'shell', has_text: '视觉PPT制作', has_text_exact: true };
  assert.equal((await gui.inspectTarget(target)).match_count, 2);
  await assert.rejects(gui.click(target), { code: 'AMBIGUOUS_TARGET' });
  const fixed = { ...target, within: { selector: '#mine', frame: 'shell' } };
  assert.equal((await gui.inspectTarget(fixed)).unique, true);
  await gui.click(fixed);
});
test('cross-frame and hidden duplicates require explicit scope instead of a silent first match', async () => {
  await page.setContent('<button>保存</button><iframe id="vm"></iframe>');
  await page.frames()[1].setContent('<button>保存</button>');
  const gui = createAutomationRuntime({ page, registry: {}, vmIframe: '#vm' });
  const target = { selector: 'button', frame: 'auto', has_text: '保存', has_text_exact: true };
  const info = await gui.inspectTarget(target);
  assert.equal(info.match_count, 2);
  assert.deepEqual(info.matches.map(m => m.frame), ['shell', 'vm']);
  assert.equal((await gui.inspectTarget({ ...target, frame: 'vm' })).unique, true);
  await page.setContent('<button hidden>保存</button><button>保存</button>');
  assert.equal((await runtime().inspectTarget({ ...target, frame: 'shell' })).unique, false);
  assert.equal((await runtime().inspectTarget({ ...target, frame: 'shell', visible: true })).unique, true);
});
test('invalid exact filters fail before acting and diagnostics are bounded', async () => {
  const gui = runtime();
  for (const target of [{ selector: 'button', has_text_exact: true }, { selector: 'button', has_text: 'x', has_text_exact: 'true' }]) {
    await assert.rejects(gui.click(target), { code: 'INVALID_TARGET' });
  }
  await page.setContent('<button>重复</button>'.repeat(80));
  const result = await gui.inspectTarget({ selector: 'button', frame: 'shell' });
  assert.equal(result.match_count, 80);
  assert.equal(result.matches.length, 20);
  assert.equal(result.truncated, true);
});
