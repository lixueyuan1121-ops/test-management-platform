import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createViewportSession } from './viewport-session.mjs';

function fixture(original = null) {
  let size = original || { width: 2560, height: 1392 };
  const calls = [];
  const session = { async send(method, args) { calls.push(method); size = args ? { width: args.width, height: args.height } : { width: 2560, height: 1392 }; }, async detach() { calls.push('detach'); } };
  const page = { context: () => ({ newCDPSession: async () => session }), viewportSize: () => original,
    evaluate: async () => size, setViewportSize: async value => { size = value; } };
  return { page, calls };
}

test('viewport validates before mutation, verifies dimensions and restores native/preexisting viewport', async () => {
  for (const original of [null, { width: 1440, height: 900 }]) {
    const { page, calls } = fixture(original);
    const viewport = createViewportSession();
    await assert.rejects(viewport.set(page, { width: '1280', height: 720 }), /整数/);
    assert.equal(calls.length, 0);
    assert.deepEqual((await viewport.set(page, { width: 1280, height: 720 })).actual, { width: 1280, height: 720 });
    await viewport.set(page, { width: 1024, height: 768 });
    assert.deepEqual((await viewport.restore()).actual, original || { width: 2560, height: 1392 });
    assert.equal(await viewport.restore(), null);
    assert.equal(calls.filter(x => x === 'detach').length, 1);
  }
});

test('partial setup failure retains cleanup and mismatched size is rejected', async () => {
  const { page, calls } = fixture();
  page.evaluate = async () => ({ width: 100, height: 100 });
  const viewport = createViewportSession();
  await assert.rejects(viewport.set(page, { width: 1280, height: 720 }), /实际 100×100/);
  await viewport.restore();
  assert.ok(calls.includes('Emulation.clearDeviceMetricsOverride'));
});
