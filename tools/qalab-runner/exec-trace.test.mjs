import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runWithTrace } from './exec-trace.mjs';

for (const verdict of ['pass', 'fail']) {
  test(`trace releases recorder and retains only failure (${verdict})`, async () => {
    const directory = await mkdtemp(join(tmpdir(), 'qalab-trace-test-'));
    const events = [];
    const gui = { async startTrace() { events.push('start'); }, async stopTrace(path) { events.push(path || 'discard'); } };
    try {
      const r = await runWithTrace(gui, async () => { events.push('execute'); return { verdict }; }, { runId: 1, directory });
      assert.deepEqual(events.slice(0, 2), ['start', 'execute']);
      assert.equal(!!r.tracePath, verdict === 'fail');
      assert.equal(events[2], verdict === 'fail' ? join(directory, 'trace-1.zip') : 'discard');
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
}

test('execution exceptions still stop trace and recording errors remain visible', async () => {
  let stopped = 0;
  const r = await runWithTrace({ async startTrace() {}, async stopTrace() { stopped++; throw new Error('CDP disconnected'); } },
    async () => { throw new Error('broken frame'); }, { runId: 2, directory: tmpdir() });
  assert.equal(r.verdict, 'fail');
  assert.match(r.reason, /broken frame/);
  assert.match(r.report.at(-1).trace_error, /CDP disconnected/);
  assert.ok(stopped >= 1);
});

test('unavailable tracing cannot silently disappear or overwrite business results', async () => {
  const r = await runWithTrace({ async startTrace() { throw new Error('unsupported'); } }, async () => ({ verdict: 'pass', reason: 'assertion passed' }));
  assert.equal(r.verdict, 'pass');
  assert.equal(r.report.at(-1).trace_error, 'unsupported');
});
