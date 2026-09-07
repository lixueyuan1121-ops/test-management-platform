// scripts/verify-workbuddy-runone.js —— 真机：连客户端 → runOne 发一条 → 打印 result + trace
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const { WorkbuddyRunner } = require('../src/workbuddy-runner');
const config = require('../config/default.config.js');
const logger = { info: console.log, warn: console.warn, error: console.error };
(async () => {
  const pool = new WorkbuddyPool(config.workbuddyDesktop, logger);
  await pool.init();
  const runner = new WorkbuddyRunner(pool.getMainPage(), config.workbuddy, config.execution || {}, logger);
  const result = await runner.runOne({ caseId: 'VERIFY-1', run_id: 0, question: '你好，请用一句话介绍你自己', conversationId: '__v1', turnIndex: 0, account: 'workbuddy' });
  const trace = runner.getDomTrace().buildTrace(0);
  console.log('=== result ===', JSON.stringify({ success: result.success, answer: (result.answer||'').slice(0,60), beanCost: result.beanCost, reportedDuration: result.reportedDuration, durationMs: result.durationMs }, null, 2));
  console.log('=== trace ===', JSON.stringify({ answer: (trace.answer||'').slice(0,60), thinking: (trace.thinking||'').slice(0,40), tool_calls: trace.tool_calls.length, artifacts: trace.artifacts.length, model: trace.model, bean_cost: trace.bean_cost, reported_duration: trace.reported_duration }, null, 2));
  if (!result.success) { console.error('FAIL: runOne 未成功'); process.exit(1); }
  await pool.close();
  console.log('PASS: WorkbuddyRunner.runOne');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
