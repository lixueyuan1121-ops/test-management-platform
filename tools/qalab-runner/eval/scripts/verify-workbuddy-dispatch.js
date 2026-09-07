// scripts/verify-workbuddy-dispatch.js —— 真机：模拟一条 workbuddy run 走 pool+runner+回写(mock client)
// 验证分流后的执行链路：WorkbuddyPool → WorkbuddyRunner.runOne → trace → report 组装。
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const { groupIntoConversations } = require('../src/conversation-group');
const config = require('../config/default.config.js');
const logger = { info: console.log, warn: console.warn, error: console.error };
const reported = [];
const client = { claim: async () => {}, report: async (id, b) => reported.push({ id, b }), uploadTrace: async () => {} };
(async () => {
  const items = [{ run_id: 9001, target_engine: 'workbuddy', payload: { prompt: '你好，请用一句话介绍你自己', turn_index: 0 } }];
  const pool = new WorkbuddyPool(config.workbuddyDesktop, logger); await pool.init();
  const runner = new WorkbuddyRunner(pool.getMainPage(), config.workbuddy, config.execution || {}, logger);
  for (const conv of groupIntoConversations(items)) {
    const it = conv[0]; const p = it.payload;
    await client.claim(it.run_id);
    const tc = { caseId: `RUN-${it.run_id}`, run_id: it.run_id, question: p.prompt, conversationId: `__run_${it.run_id}`, turnIndex: 0, account: 'workbuddy' };
    const r = await runner.runOne(tc);
    const trace = runner.getDomTrace().buildTrace(it.run_id);
    await client.uploadTrace(it.run_id, trace);
    await client.report(it.run_id, { status: r.success ? 'done' : 'failed', answer: r.answer, bean_cost: r.beanCost, session_id: trace.session_id, duration_ms: r.durationMs });
  }
  await pool.close();
  console.log('=== reported ===', JSON.stringify(reported, null, 2));
  if (!reported.length || reported[0].b.status !== 'done') { console.error('FAIL'); process.exit(1); }
  console.log('PASS: 分流+执行+回写组装');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
