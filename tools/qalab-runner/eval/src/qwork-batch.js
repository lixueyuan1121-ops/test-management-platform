'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { QworkPool } = require('./qwork-pool');
const { QworkRunner } = require('./qwork-runner');
const { qworkRawIndex } = require('./qwork-trace');
const { groupIntoConversations } = require('./conversation-group');
const { downloadAttachments } = require('./attachment-downloader');
const { runnerMetadata } = require('./artifact-collector');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// 保存完整证据后再报告终态，综合判定因此能立即读到 trace。
async function reportQworkRun(client, runId, result, trace, { outputDir = './output/qwork', retryMs = 1000 } = {}) {
  const dir = path.resolve(outputDir, String(runId));
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, 'trace.json'), JSON.stringify(trace), { mode: 0o600 });
  await fs.writeFile(path.join(dir, 'result.json'), JSON.stringify(result), { mode: 0o600 });
  let uploadError;
  if (Buffer.byteLength(JSON.stringify(trace)) > 20 * 1024 * 1024) uploadError = new Error('完整轨迹超过平台 20MB 限制');
  else for (let attempt = 0; attempt < 3; attempt++) {
    try { await client.uploadTrace(runId, trace); uploadError = null; break; }
    catch (error) { uploadError = error; if (attempt < 2) await pause(retryMs * (attempt + 1)); }
  }
  const body = {
    status: result.success && !uploadError ? 'done' : 'failed',
    answer: result.answer || null, raw_message: qworkRawIndex(trace),
    bean_cost: result.beanCost ?? null, tokens: result.cost ?? null,
    reported_duration: result.reportedDuration ?? null, duration_ms: result.durationMs ?? null,
    session_id: trace.session_id || null,
    reason: uploadError ? `QWork 轨迹上传失败：${uploadError.message}；完整记录已保存 ${dir}`
      : result.success ? null : result.errorMessage || result.completeReason || 'QWork 本轮未完成',
  };
  await fs.writeFile(path.join(dir, 'report.json'), JSON.stringify(body), { mode: 0o600 });
  await client.report(runId, body);
  return body;
}

async function runQworkBatch(items, client, config, logger, dependencies = {}) {
  const Pool = dependencies.Pool || QworkPool, Runner = dependencies.Runner || QworkRunner;
  const pool = new Pool(config.qworkDesktop, logger);
  let initError;
  try { await pool.init(); } catch (error) { initError = error; }
  try {
    for (const conv of groupIntoConversations(items)) {
      try { await client.claimGroup(conv); }
      catch (error) { logger.warn(`[qwork] 认领失败，跳过：${error.message}`); continue; }
      let preparationError = initError;
      if (!preparationError) try {
        for (const item of conv) {
          item._attachmentPaths = await (dependencies.downloadAttachments || downloadAttachments)(item.payload?.attachments || [],
            path.resolve(config.qwork?.outputDir || './output/qwork', String(item.run_id), 'attachments'), { logger });
        }
      } catch (error) { preparationError = error; }
      if (preparationError) {
        for (const item of conv) await client.report(item.run_id, { status: 'failed', reason: `QWork 准备失败，未发送题目：${preparationError.message}` })
          .catch(error => { client.claims?.delete(item.run_id); logger.error(`[qwork] 回写 ${item.run_id} 失败：${error.message}`); });
        continue;
      }
      const runner = new Runner(pool.getMainPage(), { ...config.qworkDesktop, ...config.qwork }, config.execution || {}, logger);
      const testCases = conv.map(item => ({ caseId: `RUN-${item.run_id}`, run_id: item.run_id,
        question: item.payload?.prompt || '', dialogOptions: item.payload?.dialog_options || {},
        attachmentPaths: item._attachmentPaths, conversationId: item.payload?.conversation_group || `run-${item.run_id}`,
        turnIndex: item.payload?.turn_index || 0 }));
      const onTurnDone = async (result, testCase) => {
        const trace = runner.getDomTrace().buildTrace(testCase.run_id);
        trace.execution_config = result.executionConfig || null;
        trace.runtime = { ...runnerMetadata(), user_agent: await pool.getMainPage().evaluate(() => navigator.userAgent).catch(() => null) };
        trace.input_files = await Promise.all((testCase.attachmentPaths || []).map(async file => ({
          name: path.basename(file), sha256: createHash('sha256').update(await fs.readFile(file)).digest('hex'),
        })));
        const body = await reportQworkRun(client, testCase.run_id, result, trace, config.qwork);
        logger.info(`[qwork] 回写 ${testCase.run_id}：${body.status}；${trace.capture_diagnostics?.message_count || 0} 条消息，${trace.tool_calls?.length || 0} 次工具调用`);
      };
      // 回写失败就停止本组，避免后续轮继续执行但平台缺失前一轮证据。
      try { await runner.runConversationTurns(testCases, onTurnDone); }
      catch (error) {
        // 本组已停止工作；停止续租，交由服务端过期认领恢复，不能一直把条目标成运行中。
        for (const item of conv) client.claims?.delete(item.run_id);
        logger.error(`[qwork] 会话执行/回写失败，已停止本组和本组心跳：${error.message}`);
      }
    }
  } finally { await pool.close(); }
}
module.exports = { runQworkBatch, reportQworkRun };
