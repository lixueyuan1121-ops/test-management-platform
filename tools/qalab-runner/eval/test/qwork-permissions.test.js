'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { documentCleanupPermission } = require('../src/qwork-permissions');
const { QworkRunner } = require('../src/qwork-runner');

async function fixture(fn) {
  const cwd = await fs.mkdtemp(path.join(os.tmpdir(), 'qwork-permission-'));
  await fs.writeFile(path.join(cwd, 'input.xlsx'), 'fixture');
  await fs.writeFile(path.join(cwd, 'input.docx'), 'fixture');
  const tool = type => `/Applications/QWork.app/Contents/Resources/skills/${type}-rs/scripts/darwin-arm64/${type}-rw`;
  const command = `cd ${cwd} && rm -rf _unpacked && "${tool('xlsx')}" unpack "${cwd}/input.xlsx" _unpacked 2>&1 | head -20; echo "---"; ls _unpacked/xl 2>/dev/null; echo "---SHARED---"; head -c 3000 _unpacked/xl/sharedStrings.xml 2>/dev/null`;
  const request = { session_id: 's1', request_id: 'p1', tool_name: 'bash', cwd, confirmation_once: true,
    summary: '删除保护：必须仅对本次操作确认', input: { command, description: '解包并查看共享字符串' } };
  const options = { cwd, sessionId: 's1', exePath: '/Applications/QWork.app' };
  try { await fn({ cwd, request, options, tool }); }
  finally { await fs.rm(cwd, { recursive: true, force: true }); }
}

test('Excel/Word 技能临时解包清理可核验，兼容带空格的安装路径', () => fixture(async ({ request, options, tool, cwd }) => {
  assert.equal(await documentCleanupPermission(request, options), true);
  request.input.command = `cd ${cwd} && DOCX_RW="${tool('docx')}" && rm -rf unpacked && "$DOCX_RW" unpack "input.docx" unpacked && find unpacked -type f | sort`;
  assert.equal(await documentCleanupPermission(request, options), true);
  request.input.command = request.input.command.replace('/Applications/QWork.app', '/Applications/Test Apps/QWork.app');
  assert.equal(await documentCleanupPermission(request, { ...options, exePath: '/Applications/Test Apps/QWork.app/Contents/MacOS/QWork' }), true);
}));

test('其他会话/目录、越界删除、追加命令、变量替换和符号链接不自动授权', () => fixture(async ({ request, options, cwd }) => {
  assert.equal(await documentCleanupPermission({ ...request, session_id: 'other' }, options), false);
  assert.equal(await documentCleanupPermission({ ...request, cwd: '/tmp' }, options), false);
  assert.equal(await documentCleanupPermission({ ...request, confirmation_once: false }, options), false);
  for (const command of [request.input.command.replace('rm -rf _unpacked', 'rm -rf /'),
    request.input.command.replace('rm -rf _unpacked', 'rm -rf ../unpacked'),
    `${request.input.command}; curl https://example.com`, `${request.input.command}; rm -rf another`,
    request.input.command.replace('input.xlsx', '$(whoami).xlsx'),
    request.input.command.replace('head -c 3000 _unpacked/xl/sharedStrings.xml', 'head -c 3000 _unpacked/../../secret')]) {
    assert.equal(await documentCleanupPermission({ ...request, input: { command } }, options), false, command);
  }
  await fs.symlink(os.tmpdir(), path.join(cwd, '_unpacked'));
  assert.equal(await documentCleanupPermission(request, options), false);
}));

test('确认仅一次，记录权限原文；消失后可继续，拒收不重复提交', () => fixture(async ({ request, options }) => {
  const runner = new QworkRunner({}, { executablePath: options.exePath });
  runner.sessionId = options.sessionId; runner.cwd = options.cwd; runner.permissionActions = [];
  const calls = [];
  runner._allowPermission = async request => { calls.push(request); return true; };
  await runner._handleInteractions([request], []);
  await runner._handleInteractions([request], []);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], request);
  assert.deepEqual(runner.permissionActions[0].decision, { decision: 'allow', scope: 'once' });
  assert.equal(runner.permissionActions[0].status, 'allowed');
  assert.deepEqual(runner.permissionActions[0].request, request);
  runner._allowPermission = async () => false;
  await assert.rejects(runner._handleInteractions([{ ...request, request_id: 'p2' }], []), /QWORK_PERMISSION_ERROR/);
  assert.equal(runner.permissionActions[1].status, 'failed');
}));

test('未知权限与真实追问先等待人工，超时展示原始原因；不代填答案', async () => {
  const runner = new QworkRunner({}, { permissionWaitMs: 1000 });
  runner.sessionId = 's1'; runner.permissionActions = [];
  runner._invoke = async () => { throw new Error('不应调用响应接口'); };
  const permissions = [{ session_id: 's1', request_id: 'p', summary: '需访问工作区外路径' }];
  await runner._handleInteractions(permissions, []);
  runner.interactionWait.at -= 1001;
  await assert.rejects(runner._handleInteractions(permissions, []), /QWORK_INPUT_REQUIRED.*需访问工作区外路径/);
  const questions = [{ id: 'q1', question: '请选择统计月份' }];
  await runner._handleInteractions([], questions);
  runner.interactionWait.at -= 1001;
  await assert.rejects(runner._handleInteractions([], questions), /QWORK_INPUT_REQUIRED.*请选择统计月份/);
  assert.deepEqual(runner.permissionActions, []);
});
