import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeUrl, probeCdp, windowsClientStartScript, boundedDiagnostic, pageDiagnostics } from './execution-diagnostics.mjs';
import { runScript } from './step-executor.mjs';

test('diagnostics remove credentials and transient URL parameters', () => {
  assert.equal(safeUrl('https://user:password@work.n.cn/page?token=secret#session'), 'https://work.n.cn/page');
  assert.equal(safeUrl('not-a-url secret'), '[invalid URL]');
});
test('CDP diagnostics distinguish refused port, non-CDP response and ready browser', async () => {
  assert.equal((await probeCdp(9333, async () => { throw Object.assign(new Error(), { cause: { code: 'ECONNREFUSED' } }); })).error, 'ECONNREFUSED');
  assert.equal((await probeCdp(9333, async () => ({ ok: true, json: async () => ({}) }))).ok, false);
  const ready = await probeCdp(9333, async () => ({ ok: true, json: async () => ({ Browser: 'Chrome', webSocketDebuggerUrl: 'ws://secret' }) }));
  assert.equal(ready.ok, true); assert.match(ready.endpoint, /9333/); assert.doesNotMatch(JSON.stringify(ready), /secret/);
});
test('Windows startup targets only the configured executable and safely quotes paths', () => {
  const script = windowsClientStartScript("D:\\Apps\\O'Brien\\Namiwork.exe", 9333);
  assert.match(script, /O''Brien/); assert.match(script, /ExecutablePath -eq \$clientPath/);
  assert.match(script, /-WindowStyle Hidden/); assert.match(script, /remote-debugging-port=9333/);
  assert.doesNotMatch(script, /Get-Process namiclaw/);
  assert.throws(() => windowsClientStartScript('x', '9222; bad'));
});
test('frame diagnostics show shell miss and iframe match without clicking or changing scope', async () => {
  const frame = (url, count) => ({ url: () => url, evaluate: async () => ({ ready_state: 'complete' }), locator: () => ({ count: async () => count }) });
  const shell = frame('https://work.n.cn/?token=secret', 0), child = frame('https://demo.work.n.cn/?session=secret', 1);
  const page = { isClosed: () => false, url: shell.url, frames: () => [shell, child], mainFrame: () => shell };
  const result = await pageDiagnostics(page, { target: { selector: 'button', frame: 'shell' } });
  assert.equal(result.requested_frame, 'shell');
  assert.deepEqual(result.frames.map(f => f.raw_selector_count), [0, 1]);
  assert.doesNotMatch(JSON.stringify(result), /secret/);
});
test('failed target remains failed with context in report; diagnostic errors do not mask original failure', async () => {
  for (const broken of [false, true]) {
    const logs = [];
    const result = await runScript({ click: async () => { throw new Error('original target timeout'); },
      diagnostics: async () => { if (broken) throw new Error('diagnostic failed'); return { requested_frame: 'shell' }; }
    }, [{ action: 'click', target: { selector: 'button', frame: 'shell' } }, { action: 'assert_visible' }], s => logs.push(s));
    assert.equal(result.verdict, 'fail'); assert.match(result.reason, /original target timeout/);
    assert.ok(result.report[0].diagnostic.context); assert.ok(logs.some(s => s.includes('失败现场')));
  }
});
test('hanging diagnostics have a bounded deadline', async () => {
  assert.equal((await boundedDiagnostic(() => new Promise(() => {}), 10)).unavailable, 'diagnostic timeout');
});
