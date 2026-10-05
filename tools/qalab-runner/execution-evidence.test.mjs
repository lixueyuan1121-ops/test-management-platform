import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, existsSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fingerprint, executionContract, makeEvidence, normalizeScript } from './execution-evidence.mjs';
import { executeGui } from './gui-execution.mjs';
import { verifyImport } from './verify-import.mjs';
import { runScript } from './step-executor.mjs';

const script = [{ action: 'click', target: { selector: '#start', frame: 'shell' } }, { action: 'assert_text', target: { selector: '#result', frame: 'shell' }, args: { expected: '完成' } }];
const registry = { registry: {}, vmIframe: '' };
const report = [{ action: 'click', ok: true }, { action: 'assert_text', ok: true, check: { actual: '完成', expected: '完成' } }];
const runtime = 'a'.repeat(64);
const execution = { mode: 'script', strict_replay: true, reset: true, precondition: false, fallback_reason: null };
function harness(payload = { script, strict_replay: true }) {
  const events = [];
  return { events, options: { payload, reset: async () => { events.push('reset'); return { ok: true, reset: true }; },
    navigate: async () => { events.push('navigate'); return { ok: true }; },
    scriptRun: async () => { events.push('script'); return { verdict: 'pass', report }; },
    agentRun: async () => { events.push('agent'); return { verdict: 'pass' }; } } };
}
test('legacy queue pipeline resets and navigates before script; records actual execution mode', async () => {
  const { events, options } = harness({ script, precondition: '打开记录' });
  const result = await executeGui(options);
  assert.deepEqual(events, ['reset','navigate','script']);
  assert.equal(result.execution.mode, 'claude_precondition');
  assert.equal(result.verdict, 'pass');
});
test('strict replay blocks missing script, disabled reset, failed precondition and unsupported actions', async () => {
  for (const kind of ['empty','reset','navigation','unsupported']) {
    const { events, options } = harness();
    if (kind === 'empty') options.payload.script = [];
    if (kind === 'reset') options.reset = async () => ({ ok: true, reset: false });
    if (kind === 'navigation') { options.payload.precondition = '打开记录'; options.navigate = async () => ({ ok: false }); }
    if (kind === 'unsupported') options.scriptRun = async () => ({ needClaude: true, reason: 'old runner' });
    assert.equal((await executeGui(options)).verdict, 'fail');
    assert(!events.includes('agent'));
  }
});
test('legacy fallback is preserved and labeled; script failures never trigger fallback', async () => {
  const { events, options } = harness({ script });
  options.scriptRun = async () => ({ needClaude: true, reason: 'unsupported' });
  const result = await executeGui(options);
  assert.equal(result.execution.mode, 'claude_fallback');
  assert.equal(result.execution.fallback_reason, 'unsupported');
  assert(events.includes('agent'));
  options.scriptRun = async () => ({ verdict: 'fail', fail_kind: 'business', reason: 'wrong result' });
  events.length = 0;
  assert.equal((await executeGui(options)).verdict, 'fail');
  assert(!events.includes('agent'));
});
test('changed dispatch is blocked before any operation', async () => {
  const { events, options } = harness();
  options.payload.execution_contract_sha256 = fingerprint(executionContract(options.payload));
  options.payload.script = structuredClone(script); options.payload.script[0].target.selector = '#other';
  assert.equal((await executeGui(options)).verdict, 'fail');
  assert.deepEqual(events, []);
});
test('diagnostic count survives step executor without clicking an arbitrary candidate', async () => {
  const result = await runScript({ click: async () => { throw Object.assign(new Error('ambiguous'), { code: 'AMBIGUOUS_TARGET', diagnostic: { match_count: 18 } }); } }, script);
  assert.equal(result.fail_kind, 'selector');
  assert.equal(result.report[0].diagnostic.match_count, 18);
  assert.equal(result.report[0].diagnostic.target.selector, '#start');
});
test('local verifier emits actual final script after two passes and refuses failures/overwrite', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'qalab-evidence-test-'));
  try {
    const input = join(dir, 'draft.json'), output = join(dir, 'verified.json');
    writeFileSync(input, JSON.stringify({ project_id: 1, runner_device_id: 1, requirement: 'test', cases: [{ title: 'case', steps: 'click', expected: '完成', environment: 'fixture', scope: 'fixture', script }] }));
    let count = 0, released = 0, failing = false;
    const options = { input, output, fetchRegistry: async () => registry, acquireLease: async () => async () => released++,
      execute: async item => { count++; return { verdict: failing && count % 2 === 0 ? 'fail' : 'pass', report, duration_ms: 10, execution_evidence: makeEvidence(item.payload, report, runtime, execution) }; } };
    const result = await verifyImport(options);
    assert.equal(count, 2); assert.equal(released, 1);
    assert.deepEqual(result.cases[0].script, normalizeScript(script));
    assert.equal(result.cases[0].execution_evidence.repeat_count, 2);
    const validator = new URL('../skills/qalab-requirement-test/scripts/validate-evidence.mjs', import.meta.url);
    const valid = spawnSync(process.execPath, [validator.pathname], { input: readFileSync(output), encoding: 'utf8' });
    assert.equal(valid.status, 0, valid.stderr);
    result.cases[0].script[0].target.selector = '#tampered';
    assert.equal(spawnSync(process.execPath, [validator.pathname], { input: JSON.stringify(result) }).status, 1);
    await assert.rejects(verifyImport(options), /已存在/);
    failing = true; options.output = join(dir, 'failed.json');
    await assert.rejects(verifyImport(options), /未通过/);
    assert(!existsSync(options.output)); assert.equal(released, 2);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('cross-language contract is stable for key order, unicode and numeric spellings', () => {
  assert.equal(fingerprint({ b: 1, a: '视觉🙂' }), fingerprint({ a: '视觉🙂', b: 1.0 }));
  assert.equal(fingerprint(-0), fingerprint(0));
  assert.notEqual(fingerprint({ a: 1 }), fingerprint({ a: '1' }));
});
