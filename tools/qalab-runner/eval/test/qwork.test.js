'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { buildQworkTrace, selectQworkTurn, qworkRawIndex } = require('../src/qwork-trace');
const { QworkRunner, findQworkModel } = require('../src/qwork-runner');
const { reportQworkRun, runQworkBatch } = require('../src/qwork-batch');
const { partitionProducts } = require('../src/product-routing');
const PlatformClient = require('../src/platform-client');

function history() {
  return { attempts: [
    { turnId: 't1', status: 'completed', startedAt: 1000, finishedAt: 4000, messageIndex: 0 },
    { turnId: 't2', status: 'completed', startedAt: 5000, finishedAt: 6000, messageIndex: 4 },
  ], messages: [
    { role: 'user', turn_id: 't1', source_index: 0, text: '第一轮' },
    { role: 'assistant', source_index: 1, blocks: [{ type: 'thinking', thinking: '可见思考' },
      { type: 'text', text: '正在读取附件' }, { type: 'tool_use', id: 'c1', name: 'read_file', input: '{"path":"input.txt"}' }],
      usage: { input_tokens: 10, output_tokens: 2, cache_read_input_tokens: 3 } },
    { role: 'tool', source_index: 2, blocks: [{ type: 'tool_result', tool_use_id: 'c1', output: '文件内容', is_error: false }] },
    { role: 'assistant', source_index: 3, text: '首轮完成', usage: { input_tokens: 4, output_tokens: 5 } },
    { role: 'user', turn_id: 't2', source_index: 4, text: '第二轮' },
    { role: 'assistant', source_index: 5, text: '续轮完成' },
  ] };
}
const traceFor = (turnId = 't1', extra = {}) => buildQworkTrace({ sessionId: 's1', turnId, history: history(), ...extra });

test('QWork 保留全部正文/思考/工具原文，回合切分不串数据', () => {
  const a = traceFor(), b = traceFor('t2', { previousTurnIds: ['t1'] });
  assert.equal(a.answer, '首轮完成');
  assert.deepEqual(a.assistant_segments, ['正在读取附件', '首轮完成']);
  assert.equal(a.thinking, '可见思考');
  assert.deepEqual(a.tool_calls[0].args, { path: 'input.txt' });
  assert.equal(a.tool_calls[0].result_text, '文件内容');
  assert.equal(a.tool_calls[0].success, true);
  assert.equal(a.usage.input_tokens, 14);
  assert.equal(a.reported_duration, 3);
  assert.equal(a.raw_history.messages.length, 4);
  assert.equal(b.raw_history.messages.length, 2);
  assert.equal(b.tool_calls.length, 0);
  assert.deepEqual(b.raw_history.previous_turn_ids, ['t1']);
  assert.equal(b.capture_diagnostics.status, 'complete');
});

test('缺失、失败、未知工具状态不误报成功；积分缺失不当作零', () => {
  const h = history();
  delete h.messages[2].blocks[0].is_error;
  assert.equal(traceFor('t1', { history: h }).tool_calls[0].success, null);
  h.messages[2].blocks[0].is_error = true;
  assert.equal(traceFor('t1', { history: h }).tool_calls[0].success, false);
  h.messages.splice(2, 1); h.attempts[0].status = 'interrupted';
  assert.equal(traceFor('t1', { history: h }).capture_diagnostics.status, 'partial');
  assert.equal(traceFor('t1', { eventsDropped: 3 }).capture_diagnostics.status, 'partial');
  assert.equal(traceFor().bean_cost, null);
  assert.equal(traceFor('t1', { credits: { status: 'pending', chargedMicrocredits: 100 } }).bean_cost, null);
  assert.equal(traceFor('t1', { credits: { status: 'settled', chargedMicrocredits: 0 } }).bean_cost, 0);
  assert.equal(traceFor('t1', { credits: { status: 'settled', chargedMicrocredits: 5500 } }).bean_cost, 0.0055);
  assert.throws(() => selectQworkTurn(h, 'other'), /未取得本轮/);
});

test('过期消息索引和缺失回合 ID 不得串入邻轮正文', () => {
  const h = history(); h.attempts[1].messageIndex = 0;
  assert.equal(selectQworkTurn(h, 't2').messages[0].text, '第二轮');
  delete h.messages[4].turn_id;
  assert.equal(selectQworkTurn(h, 't2').messages.length, 0);
  assert.equal(selectQworkTurn(h, 't1').messages.length, 4);
});

test('长工具返回完整保存在 trace，数据库只存小索引', () => {
  const h = history(), large = '完整中文🙂'.repeat(60000);
  h.messages[2].blocks[0].output = large;
  const trace = traceFor('t1', { history: h });
  assert.equal(trace.raw_history.messages[2].blocks[0].output, large);
  assert(Buffer.byteLength(qworkRawIndex(trace)) < 4000);
  assert.match(trace.capture_diagnostics.raw_sha256, /^[a-f0-9]{64}$/);
});

test('模型忽略英文大小写，精确匹配列表，未知/重复模型拒绝发送', () => {
  const catalog = { models: [{ label: 'GLM-5.2', value: 'z-ai/glm-5.2' }], defaultModel: 'z-ai/glm-5.2' };
  assert.equal(findQworkModel(catalog, 'glm-5.2').value, 'z-ai/glm-5.2');
  assert.equal(findQworkModel(catalog, 'Z-AI/GLM-5.2').label, 'GLM-5.2');
  assert.equal(findQworkModel(catalog).label, 'GLM-5.2');
  assert.throws(() => findQworkModel(catalog, 'GLM'), /不可用/);
  assert.throws(() => findQworkModel({ models: [...catalog.models, ...catalog.models] }, 'glm-5.2'), /不可用/);
});

function fakeRunner({ waiting = false, permission = false, sendFailure = false } = {}) {
  let sends = 0, cancels = 0, created = 0;
  const stored = { attempts: [], messages: [] };
  const runner = new QworkRunner({ waitForTimeout: () => new Promise(r => setTimeout(r, 2)) }, { pollMs: 1, permissionWaitMs: 0 }, { taskTimeout: 100 });
  runner._share = async () => ({ status: 'completed', url: 'https://qwork.360.cn/share/test', selected_all: true });
  runner._startEvents = async () => {};
  runner._stopEvents = async () => ({ events: [], dropped: 0 });
  runner._invoke = async (group, method, args) => {
    if (group === 'models') return { models: [{ label: 'M', value: 'm' }], defaultModel: 'm', verified: true };
    if (method === 'create') { created++; return { session_id: 's1' }; }
    if (method === 'list') return [{ session_id: { 0: 's1' }, model: 'm' }];
    if (method === 'history') return structuredClone(stored);
    if (method === 'prompt') {
      sends++;
      assert.deepEqual(args[2], {}, '空附件列表必须省略，否则 QWork 拒绝');
      if (sendFailure) throw new Error('发送接口超时');
      const turnId = `t${sends}`;
      stored.attempts.push({ turnId, status: waiting ? 'running' : 'completed', messageIndex: stored.messages.length });
      stored.messages.push({ role: 'user', turn_id: turnId, source_index: stored.messages.length, text: args[1] },
        { role: 'assistant', text: `本轮 ${sends}` });
    }
    if (method === 'cancel') { cancels++; stored.attempts.forEach(a => { if (a.status === 'running') a.status = 'interrupted'; }); }
    if (method === 'pendingPermissions') return permission ? [{ request_id: 'p1', session_id: 's1', summary: '未知权限操作' }] : [];
    return [];
  };
  return { runner, counts: () => ({ sends, cancels, created }) };
}
const cases = [1, 2].map(i => ({ caseId: `C${i}`, question: `问题 ${i}`, turnIndex: i - 1 }));

test('多轮复用同一会话，每条只发送一次', async () => {
  const { runner, counts } = fakeRunner();
  const traces = [];
  const results = await runner.runConversationTurns(cases, () => traces.push(structuredClone(runner.trace)));
  assert(results.every(r => r.success));
  assert.deepEqual(counts(), { sends: 2, cancels: 0, created: 1 });
  assert.equal(traces[0].answer, '本轮 1');
  assert.equal(traces[1].answer, '本轮 2');
  assert.equal(traces[1].raw_history.messages.length, 2);
});

test('超时和权限等待保存部分内容，取消当前任务，后续轮不发送', async () => {
  for (const permission of [false, true]) {
    const { runner, counts } = fakeRunner({ waiting: true, permission });
    const traces = [];
    const results = await runner.runConversationTurns(cases, () => traces.push(structuredClone(runner.trace)));
    assert.equal(results[0].errorCode, permission ? 'QWORK_INPUT_REQUIRED' : 'QWORK_TIMEOUT');
    assert.equal(results[0].answer, '本轮 1');
    assert.equal(results[1].errorCode, 'QWORK_PREVIOUS_TURN_FAILED');
    assert.equal(traces[0].capture_diagnostics.status, 'partial');
    assert.deepEqual(counts(), { sends: 1, cancels: 1, created: 1 });
    if (permission) assert.equal(traces[0].pending_interactions.permissions[0].request_id, 'p1');
  }
});

test('发送接口报错不能重发，附件准备失败不能裸跑', async () => {
  const { runner, counts } = fakeRunner({ sendFailure: true });
  assert.equal((await runner.runOne(cases[0])).success, false);
  assert.equal(counts().sends, 1);
  runner._attachments = async () => { throw new Error('附件失败'); };
  assert.equal((await runner.runOne(cases[0])).success, false);
  assert.equal(counts().sends, 1);
});

test('产品路由严格隔离，旧配置保留，多产品声明可用', () => {
  const groups = partitionProducts([{ target_engine: 'qwork' }, { target_engine: 'workbuddy' }, {}, { target_engine: 'unknown' }]);
  for (const name of ['qwork', 'workbuddy', 'namiwork', 'unsupported']) assert.equal(groups[name].length, 1);
  const config = { baseUrl: 'http://localhost', token: 'test', engines: ['workbuddy', 'qwork'] };
  assert.equal(new PlatformClient(config).engine, 'workbuddy,qwork');
  assert.equal(new PlatformClient({ ...config, engines: undefined, engine: 'workbuddy' }).engine, 'workbuddy');
});

test('轨迹上传重试在报告终态之前；彻底失败仍有本地完整数据', async () => {
  const outputDir = await fs.mkdtemp(path.join(os.tmpdir(), 'qwork-report-'));
  try {
    for (const fail of [false, true]) {
      const calls = [];
      const client = { uploadTrace: async () => { calls.push('upload'); if (fail || calls.length === 1) throw new Error('network'); },
        report: async (_id, body) => calls.push(body.status) };
      const body = await reportQworkRun(client, 1, { success: true, answer: '答案', beanCost: 0 }, traceFor(), { outputDir, retryMs: 1 });
      assert.deepEqual(calls, fail ? ['upload', 'upload', 'upload', 'failed'] : ['upload', 'upload', 'done']);
      assert.equal(body.bean_cost, '0');
      assert.equal(JSON.parse(await fs.readFile(path.join(outputDir, '1/trace.json'), 'utf8')).answer, '首轮完成');
      if (fail) assert.match(body.reason, /完整记录已保存/);
    }
  } finally { await fs.rm(outputDir, { recursive: true, force: true }); }
});

test('分享结果回填原生 URL；分享失败记录原因，不重发已完成回答', async () => {
  const outputDir = await fs.mkdtemp(path.join(os.tmpdir(), 'qwork-share-report-'));
  try {
    const { runner, counts } = fakeRunner();
    let sent;
    const client = { uploadTrace: async () => {}, report: async (_id, body) => { sent = body; } };
    const result = await runner.runOne(cases[0]);
    await reportQworkRun(client, 1, result, runner.trace, { outputDir });
    assert.equal(sent.share_link, 'https://qwork.360.cn/share/test');
    runner._share = async () => ({ status: 'failed', reason: '分享服务不可用' });
    const failedShare = await runner.runOne(cases[1]);
    await reportQworkRun(client, 2, failedShare, runner.trace, { outputDir });
    assert.equal(sent.status, 'done');
    assert.equal(sent.share_link, null);
    assert.equal(sent.reason, '分享服务不可用');
    assert.equal(counts().sends, 2);
    assert.equal(counts().cancels, 0);
  } finally { await fs.rm(outputDir, { recursive: true, force: true }); }
});

test('QWork 回写指标遵守平台字符串契约，保留小数、零值和缺失值', async () => {
  const outputDir = await fs.mkdtemp(path.join(os.tmpdir(), 'qwork-metrics-'));
  const trace = traceFor();
  try {
    for (const [value, expected] of [[141.682, '141.682'], [0.0262, '0.0262'], [451619, '451619'],
      [0, '0'], ['3.00', '3.00'], [null, null], [undefined, null], [NaN, null], [Infinity, null]]) {
      let sent;
      const client = { uploadTrace: async () => {}, report: async (_id, body) => { sent = body; } };
      await reportQworkRun(client, 1, { success: true, reportedDuration: value, beanCost: value, cost: value,
        durationMs: 143170 }, trace, { outputDir });
      for (const field of ['reported_duration', 'bean_cost', 'tokens']) assert.equal(sent[field], expected, field);
      assert.equal(sent.duration_ms, 143170);
      assert.deepEqual(JSON.parse(await fs.readFile(path.join(outputDir, '1/report.json'), 'utf8')), sent);
      const raw = JSON.parse(await fs.readFile(path.join(outputDir, '1/trace.json'), 'utf8'));
      assert.equal(raw.reported_duration, 3, '原始 trace 数值不受接口格式转换影响');
    }
  } finally { await fs.rm(outputDir, { recursive: true, force: true }); }
});

test('批处理：附件下载失败整组不发送；连接失败不使用其他产品', async () => {
  for (const connectionFailure of [false, true]) {
    const reports = [], claims = [];
    class Pool { async init() { if (connectionFailure) throw new Error('连接失败'); } async close() {} }
    class Runner { constructor() { throw new Error('不应创建执行器'); } }
    const items = [1, 2].map((id, i) => ({ run_id: id, target_engine: 'qwork', payload: { conversation_group: 'g', turn_index: i, attachments: ['file'] } }));
    await runQworkBatch(items, { claimGroup: async c => claims.push(c), report: async (id, body) => reports.push({ id, body }) }, {}, console,
      { Pool, Runner, downloadAttachments: async () => { throw new Error('附件不存在'); } });
    assert.equal(claims.length, 1);
    assert.deepEqual(reports.map(r => r.id), [1, 2]);
    assert(reports.every(r => r.body.status === 'failed'));
  }
});

test('完整批处理先上传再回写，各轮原文可分析；回写故障停止本组续租', async () => {
  const outputDir = await fs.mkdtemp(path.join(os.tmpdir(), 'qwork-batch-'));
  try {
    for (const reportFails of [false, true]) {
      const calls = [], traces = [], state = fakeRunner();
      class Pool { async init() {} async close() {} getMainPage() { return { evaluate: async () => 'QWork-test-UA' }; } }
      class Runner { constructor() { return state.runner; } }
      const claims = new Map([[1, 'a'], [2, 'a'], [3, 'unrelated']]);
      const client = { claims, claimGroup: async () => {}, uploadTrace: async (id, trace) => { calls.push(`trace${id}`); traces.push(trace); },
        report: async (id, body) => { calls.push(`report${id}`); if (reportFails) throw new Error('网络故障'); assert.equal(body.status, 'done'); claims.delete(id); } };
      const items = [1, 2].map((id, i) => ({ run_id: id, target_engine: 'qwork', payload: { prompt: `测试 ${i}`, conversation_group: 'g', turn_index: i } }));
      await runQworkBatch(items, client, { qwork: { outputDir } }, { info() {}, warn() {}, error() {} },
        { Pool, Runner, downloadAttachments: async () => [] });
      assert.deepEqual(calls, reportFails ? ['trace1', 'report1'] : ['trace1', 'report1', 'trace2', 'report2']);
      assert.equal(traces[0].raw_history.messages.length, 2);
      assert.equal(traces[0].runtime.user_agent, 'QWork-test-UA');
      assert.deepEqual([...claims.keys()], [3]);
      assert.equal(state.counts().sends, reportFails ? 1 : 2);
    }
  } finally { await fs.rm(outputDir, { recursive: true, force: true }); }
});
