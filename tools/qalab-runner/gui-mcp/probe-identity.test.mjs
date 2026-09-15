import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright-core';
import { createGuiCore } from './gui-core.mjs';

test('probe identity survives valid selection and rejects wrong/replaced/stale elements', async () => {
  const server = createServer((req, res) => res.setHeader('Content-Type', 'text/html; charset=utf-8').end('<html><body><button id="edit" aria-label="编辑">编辑</button><button id="copy" aria-label="复制">复制</button></body></html>'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const portReservation = createServer();
  await new Promise(resolve => portReservation.listen(0, '127.0.0.1', resolve));
  const cdpPort = portReservation.address().port;
  await new Promise(resolve => portReservation.close(resolve));
  const profile = await mkdtemp(join(tmpdir(), 'qalab-probe-identity-'));
  let context, core;
  try {
    context = await chromium.launchPersistentContext(profile, { headless: true, executablePath: process.env.PLAYWRIGHT_TEST_EXECUTABLE, args: [`--remote-debugging-port=${cdpPort}`] });
    const page = context.pages()[0];
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(() => localStorage.setItem('openclaw.testids', '1'));
    core = createGuiCore({ cdpUrl: `http://127.0.0.1:${cdpPort}`, registry: {}, timeout: 500 });
    const probe = await core.probe();
    const elements = probe.groups.flatMap(g => g.elements);
    assert(elements.length >= 2);
    assert(elements.every(e => e.element_ref));
    assert.equal(new Set(elements.map(e => e.element_ref)).size, elements.length);
    const edit = elements.find(e => e.candidates.some(c => c.by === 'css' && c.value === '#edit'));
    assert(edit);
    const item = { key: 'editButton', frame: 'shell', candidates: [{ by: 'css', value: '#edit' }], element_ref: edit.element_ref, expected: { tag: 'button', text: '编辑' } };
    const check = async v => (await core.validateSelection({ items: [v] })).validation[0];
    let result = await check(item);
    assert.equal(result.ok, true, JSON.stringify(result)); assert.equal(result.identity_verified, true);
    result = await check({ ...item, candidates: [{ by: 'css', value: '#copy' }], expected: { tag: 'button', text: '复制' } });
    assert.equal(result.ok, false); assert.equal(result.identity_verified, false);
    await page.evaluate(() => { const old = document.querySelector('#edit'); old.replaceWith(old.cloneNode(true)); });
    result = await check(item);
    assert.equal(result.ok, false); assert.equal(result.identity_verified, false);
    const fresh = (await core.probe()).groups.flatMap(g => g.elements).find(e => e.candidates.some(c => c.value === '#edit'));
    assert.equal((await check({ ...item, element_ref: fresh.element_ref })).ok, true);
    assert.equal((await check(item)).identity_verified, false);
    await page.evaluate(() => { document.querySelector('#edit').hidden = true; });
    assert.equal((await check({ ...item, element_ref: fresh.element_ref })).ok, false);
  } finally {
    await context?.close();
    await new Promise(resolve => server.close(resolve));
    await rm(profile, { recursive: true, force: true });
  }
});
