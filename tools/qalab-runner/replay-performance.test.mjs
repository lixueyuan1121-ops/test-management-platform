import test from 'node:test';
import assert from 'node:assert/strict';
import { createExecutionTimer } from './execution-timing.mjs';
import { compileReplayDraft } from './replay-draft.mjs';
import { executeGui } from './gui-execution.mjs';
import { runScript } from './step-executor.mjs';
import { replaySetupUpdate } from '../../frontend/src/utils/replaySetup.js';

const setup = [{ action: 'click', target: { selector: '#history' } }, { action: 'assert_visible', target: { selector: '#history-panel' } }];
const script = [{ action: 'click', target: { selector: '#download' } }, { action: 'assert_text', target: { selector: '#state' }, args: { expected: '完成' } }];
test('explicit setup is prepended once, retains requirement, and prevents AI navigation', async () => {
  const draft = { steps: '下载并检查', precondition: '打开历史记录', script, setup_script: setup };
  const compiled = compileReplayDraft(draft);
  assert.equal(compiled.precondition, ''); assert.equal(compiled.script.length, 4);
  assert.match(compiled.steps, /打开历史记录/); assert(!('setup_script' in compiled));
  assert.deepEqual(compileReplayDraft(compiled), compiled);
  assert.equal(replaySetupUpdate(draft, script, setup).script.length, 4);
  const calls = [];
  const result = await executeGui({ payload: { ...compiled, strict_replay: true }, engine: 'codex',
    reset: async () => ({ ok: true, reset: true }), navigate: async () => assert.fail('AI must not run'),
    agentRun: async () => assert.fail('fallback must not run'), scriptRun: async steps => { calls.push(...steps.map(s => s.target.selector)); return { verdict: 'pass' }; } });
  assert.equal(result.execution.mode, 'script');
  assert.deepEqual(calls, ['#history', '#history-panel', '#download', '#state']);
});
test('text-only preconditions/judge cannot enter strict replay, before reset or mutation', async () => {
  assert.throws(() => compileReplayDraft({ script, precondition: '打开历史记录' }), /setup_script/);
  assert.throws(() => compileReplayDraft({ script: [{ action: 'click' }], setup_script: setup }), /业务断言/);
  assert.throws(() => compileReplayDraft({ script, setup_script: [setup[0]] }), /到位断言/);
  for (const extra of [{ precondition: '打开记录' }, { script: [...script, { action: 'judge' }] }]) {
    const result = await executeGui({ payload: { script, ...extra, strict_replay: true }, reset: async () => assert.fail('must reject before reset') });
    assert.equal(result.verdict, 'fail'); assert.match(result.reason, /固化为脚本/);
  }
});
test('selected Codex legacy navigation/fallback stays labeled; real failures do not fallback', async () => {
  const opts = { payload: { script, precondition: '打开记录' }, engine: 'codex', reset: async () => ({ ok: true, reset: true }), navigate: async () => ({ ok: true }), scriptRun: async () => ({ verdict: 'pass' }) };
  assert.equal((await executeGui(opts)).execution.mode, 'codex_precondition');
  opts.payload = { script }; opts.scriptRun = async () => ({ needClaude: true, reason: 'unsupported' });
  opts.agentRun = async () => ({ verdict: 'fail', reason: 'Codex not installed' });
  const result = await executeGui(opts); assert.equal(result.execution.mode, 'codex_fallback'); assert.equal(result.verdict, 'fail');
  opts.scriptRun = async () => ({ verdict: 'fail', fail_kind: 'business' }); opts.agentRun = async () => assert.fail('no retry by another model');
  assert.equal((await executeGui(opts)).fail_kind, 'business');
});
test('monotonic stage timings accumulate, include failures and total preparation/upload time', async () => {
  let clock = 100;
  const timer = createExecutionTimer(() => clock);
  await timer.measure('prepare_ms', async () => { clock += 30; });
  await assert.rejects(timer.measure('script_ms', async () => { clock += 25; throw new Error('blocked'); }));
  await timer.measure('upload_ms', async () => { clock += 10; });
  await timer.measure('upload_ms', async () => { clock += 20; });
  assert.deepEqual(timer.snapshot(), { prepare_ms: 30, script_ms: 25, upload_ms: 30, runner_total_ms: 85 });
});
test('passed and failing steps retain measured time without changing assertion results', async () => {
  const result = await runScript({ click: async () => {}, assertText: async () => ({ pass: false, actual: '失败', mode: 'equals' }), unmockAll: async () => {} }, script);
  assert.equal(result.verdict, 'fail'); assert.equal(result.fail_kind, 'business');
  assert.equal(result.report.length, 2);
  for (const step of result.report) assert(Number.isInteger(step.duration_ms) && step.duration_ms >= 0);
});
