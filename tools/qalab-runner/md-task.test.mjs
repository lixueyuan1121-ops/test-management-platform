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
        if (method === 'PATCH') { events.push('report'); report = body; return { id: 11 }; }
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
    assert.equal(report.verdict, restartFails ? 'blocked' : 'completed');
    if (!restartFails) assert.match(report.result_md, /\| open \| pass \| good \|/);
    assert.equal(fs.existsSync(path.join(dir, 'md-runs/md-11/result.md')), !restartFails);
  });
}

test('failed case returns its screenshot, client log delta and Markdown, then replays only missing bytes', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'md-evidence-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const clientLogFile = path.join(dir, 'client.log');
  fs.writeFileSync(clientLogFile, 'prior session\n');
  const cli = path.join(dir, 'agent.js');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+XAmQAAAAASUVORK5CYII=', 'base64');
  fs.writeFileSync(cli, [
    "const fs = require('node:fs');",
    "fs.writeFileSync('result.md', '| 用例 | 结论 | 说明 |\\n| --- | --- | --- |\\n| C-001 | fail | 保存失败 |');",
    `fs.writeFileSync('evidence/error.png',Buffer.from('${png.toString('base64')}','base64'));`,
    `fs.writeFileSync('evidence/manifest.json',JSON.stringify([{case:'C-001',file:'error.png'}]));`,
  ].join('\n'));
  let first = true, reported = 0, executions = 0, logUploads = 0;
  const screenshots = [];
  const runner = makeMdTaskRunner({ runtimeDir: dir, RUNNER_ID: 'win', clientLogFile, log() {},
    hosts: { apply: async () => ({ verify: [] }), restore: async () => true },
    async restartClient() { executions++; fs.appendFileSync(clientLogFile, 'this run\nAuthorization: Bearer test-secret\n'); },
    codexBin: cli, codexArgs: [], codexTimeoutMs: 5000,
    async api(method, url, body) {
      if (method === 'GET') { if (!first) return []; first = false; return [{ run_id: 14, content: 'task' }]; }
      if (url.includes('/evidence?')) {
        const data = body.get('file');
        if (data.name === 'error.png') {
          assert.equal(body.get('case_label'), 'C-001');
          screenshots.push(Buffer.from(await data.arrayBuffer()));
        } else {
          assert.equal(data.name, '任务时段客户端.log');
          assert.equal(body.get('case_label'), '');
          assert.equal(await data.text(), 'this run\nAuthorization: [已隐藏]\n');
          if (++logUploads === 1) throw Error('network reset');
        }
        return { id: 1 };
      }
      if (method === 'PATCH') {
        reported++;
        assert.equal(body.evidence_summary.expected, 2);
        assert.match(body.result_md, /保存失败/);
        return { id: 14, status: 'failed' };
      }
    },
  });
  await runner.pollOnce(); await runner.pollOnce();
  assert.deepEqual(screenshots, [png]);
  assert.deepEqual([reported, executions, logUploads], [2, 1, 2]);
  assert.deepEqual(fs.readdirSync(path.join(dir, 'md-pending-reports')), []);
});

test('evidence manifest cannot read files outside the work folder and uses an explicitly marked end screenshot', async (t) => {
  const { collectMdEvidence } = await import('./md-evidence.mjs');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'md-linked-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const evidence = path.join(dir, 'evidence');
  fs.mkdirSync(evidence); fs.writeFileSync(path.join(dir, 'private.log'), 'not evidence');
  const rows = [{ case: 'C-001', file: '../private.log' }];
  if (process.platform !== 'win32') {
    fs.symlinkSync('../private.log', path.join(evidence, 'linked.log'));
    rows.push({ case: 'C-001', file: 'linked.log' });
  }
  fs.writeFileSync(path.join(evidence, 'manifest.json'), JSON.stringify(rows));
  const png = Buffer.from('89504e470d0a1a0a', 'hex');
  const result = await collectMdEvidence({ workDir: dir, clientStarted: true, captureScreenshot: async () => png });
  assert.deepEqual(result.files.map(f => [f.name, f.label]), [['任务结束时客户端.png', '']]);
  assert.equal(result.notes.length, rows.length);
});
