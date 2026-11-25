import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { makeHostsEnv } from './hosts-env.mjs';
import { makeMdTaskRunner } from './md-task.mjs';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { clientProcessIds, stopPosixClient } from './client-processes.mjs';

test('only the configured installation, its helpers and descendants are selected for exit', () => {
  const exe = '/apps/Client.app/Contents/MacOS/Client';
  const rows = [
    { pid: 4, parent: 3, command: 'worker' }, { pid: 3, parent: 2, command: 'renderer' },
    { pid: 2, parent: 1, command: exe }, { pid: 5, parent: 1, command: '/elsewhere/Client.app/Contents/MacOS/Client' },
    { pid: 6, parent: 1, command: '/apps/Client.app/Contents/Frameworks/Client Helper' },
  ];
  assert.deepEqual([...clientProcessIds(rows, exe)].sort(), [2, 3, 4, 6]);
});

test('pre/test use the configured local file and accept old group names in either direction', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'md-groups-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const hosts = makeHostsEnv({ groupsDir: dir, runtimeDir: dir });
  for (const [name, old] of [['pre', 'work-staging'], ['test', 'work-test']]) {
    const canonical = path.join(dir, name + '.hosts'), legacy = path.join(dir, old + '.hosts');
    fs.writeFileSync(legacy, '# old');
    assert.equal(hosts.groupFile(name), legacy);
    fs.writeFileSync(canonical, '# new');
    assert.equal(hosts.groupFile(name), canonical);
    fs.unlinkSync(legacy);
    assert.equal(hosts.groupFile(old), canonical);
  }
});

test('the configured executable actually exits while a second copy stays alive', { skip: process.platform === 'win32' }, async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'md-client-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const children = [];
  t.after(() => { for (const child of children) child.kill(); });
  for (const name of ['target', 'other']) {
    const file = path.join(dir, name);
    fs.copyFileSync('/bin/sleep', file);
    const child = spawn(file, ['60']);
    children.push(child);
    await once(child, 'spawn');
  }
  const stopped = once(children[0], 'exit');
  await stopPosixClient(path.join(dir, 'target'));
  await stopped;
  assert.equal(children[1].exitCode, null);
  assert.equal(children[1].signalCode, null);
});

for (const restartFails of [false, true]) {
  test(`hosts are restored after a ${restartFails ? 'failed' : 'successful'} restart`, async (t) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'md-run-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const cli = path.join(dir, 'fake-agent.js');
    fs.writeFileSync(cli, "require('fs').writeFileSync('result.md','| 用例 | 结论 | 说明 |\\n| --- | --- | --- |\\n| open | pass | good |')");
    const events = [];
    let report;
    const runner = makeMdTaskRunner({ runtimeDir: dir, RUNNER_ID: 'a', log() {},
      async api(method, url, body) {
        if (method === 'GET') return [{ run_id: 11, content: '# task', env_group: 'pre' }];
        if (method === 'PATCH') { events.push('report'); report = body; }
      },
      hosts: {
        async apply(group) { events.push('hosts'); return { group, verify: [] }; },
        async restore() { events.push('restore'); return true; },
      },
      async restartClient() { events.push('restart'); if (restartFails) throw Error('target still running'); },
      codexBin: cli, codexArgs: [], codexTimeoutMs: 5000,
    });
    await runner.pollOnce();
    assert.deepEqual(events, ['hosts', 'restart', 'restore', 'report']);
    assert.equal(report.env_check.client_restarted, !restartFails);
    assert.equal(report.env_check.restored, true);
    assert.equal(report.verdict, restartFails ? 'blocked' : 'pass');
    assert.equal(fs.existsSync(path.join(dir, 'md-runs/md-11/result.md')), !restartFails);
  });
}
