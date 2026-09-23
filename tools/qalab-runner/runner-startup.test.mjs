// Offline startup tests: no platform traffic, client launch or task claims.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = dirname(fileURLToPath(import.meta.url));

test('real runner --check loads all modules without network or task execution', () => {
  const guard = 'data:text/javascript,' + encodeURIComponent(
    'globalThis.fetch = () => { console.error("UNEXPECTED_NETWORK"); process.exit(90); };');
  const result = spawnSync(process.execPath, ['--import', guard, join(dir, 'runner.mjs'), '--check'], {
    encoding: 'utf8', timeout: 10000,
    env: { ...process.env, BASE_URL: 'http://127.0.0.1:1', RUNNER_TOKEN: 'offline-test', RUNNER_ID: 'offline-test' },
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  assert.match(result.stdout, /启动检查通过/);
  assert.doesNotMatch(result.stdout + result.stderr, /UNEXPECTED_NETWORK|runner 启动 base=|checking runner update/);
});

function launcher({ codes = [0], args = [], missing = false } = {}) {
  const folder = mkdtempSync(join(tmpdir(), 'qalab-startup-'));
  try {
    const filename = process.platform === 'win32' ? 'run.cmd' : 'run.sh';
    copyFileSync(join(dir, filename), join(folder, filename));
    const script = missing ? "import './missing-required-module.mjs';" : `
      import { readFileSync, writeFileSync } from 'node:fs';
      let calls = [];
      try { calls = JSON.parse(readFileSync('calls.json', 'utf8')); } catch {}
      const kind = process.argv.includes('--update') ? 'update' : process.argv.includes('--check') ? 'check' : 'start';
      calls.push({kind, args:process.argv.slice(2)});
      writeFileSync('calls.json', JSON.stringify(calls));
      const codes = ${JSON.stringify(codes)};
      process.exit(kind === 'update' ? (codes[calls.filter(c => c.kind === 'update').length - 1] ?? 0) : 0);
    `;
    writeFileSync(join(folder, 'runner.mjs'), script);
    const result = process.platform === 'win32'
      ? spawnSync(process.env.ComSpec || 'cmd.exe', ['/d', '/c', filename, ...args], {
        cwd: folder, encoding: 'utf8', timeout: 10000,
      })
      : spawnSync('bash', [join(folder, filename), ...args], { encoding: 'utf8', timeout: 10000 });
    let calls = [];
    try { calls = JSON.parse(readFileSync(join(folder, 'calls.json'), 'utf8')); } catch {}
    return { ...result, calls };
  } finally { rmSync(folder, { recursive: true, force: true }); }
}

test('missing imported module stops at update phase instead of starting again', () => {
  const result = launcher({ missing: true });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ERR_MODULE_NOT_FOUND/);
  assert.match(result.stderr, /startup check failed/);
  assert.doesNotMatch(result.stdout, /starting qalab runner/);
});

test('unexpected update exit code is preserved and no task loop starts', () => {
  const result = launcher({ codes: [23] });
  assert.equal(result.status, 23);
  assert.deepEqual(result.calls.map(c => c.kind), ['update']);
});

test('update success/current starts once and preserves execution arguments', () => {
  const result = launcher({ codes: [0], args: ['--dry'] });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls, [{ kind: 'update', args: ['--update'] }, { kind: 'start', args: ['--dry'] }]);
});

test('updated code is rechecked before startup, bounded to three updates', () => {
  for (const codes of [[75, 0], [75, 75, 75]]) {
    const result = launcher({ codes });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.calls.map(c => c.kind), [...codes.map(() => 'update'), 'start']);
  }
  const failed = launcher({ codes: [75, 1] });
  assert.equal(failed.status, 1);
  assert.deepEqual(failed.calls.map(c => c.kind), ['update', 'update']);
});

test('launcher --check skips update entirely', () => {
  const result = launcher({ args: ['--check'] });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.calls, [{ kind: 'check', args: ['--check'] }]);
  assert.doesNotMatch(result.stdout, /checking runner update|starting qalab runner/);
});


test('Windows launchers are ASCII CRLF and do not override .env identity', () => {
  for (const filename of ['run.cmd', 'run-eval.cmd']) {
    const raw = readFileSync(join(dir, filename), 'utf8');
    assert.doesNotMatch(raw, /[^\x00-\x7f]/);
    assert.doesNotMatch(raw.replaceAll('\r\n', ''), /\n/);
    assert.doesNotMatch(raw, /set "(?:RUNNER_TOKEN|RUNNER_ID|BASE_URL|CDP_PORT)=/i);
  }
});
