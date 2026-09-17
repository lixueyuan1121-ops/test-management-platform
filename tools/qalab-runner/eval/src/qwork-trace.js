'use strict';
// QWork 0.1.16: workGui.sessions.history 的原生 transcript；与 WorkBuddy 格式分开解析。
const { createHash } = require('node:crypto');
const { _isMcp, _mcpServer, sanitizeDialogText } = require('./ws-trace');
const idOf = value => typeof value === 'string' ? value : value?.[0] || '';
const textOf = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
const parsed = value => { if (typeof value !== 'string') return value; try { return JSON.parse(value); } catch { return value; } };

function selectQworkTurn(history, turnId) {
  if (!Array.isArray(history?.messages) || !Array.isArray(history?.attempts)) throw new Error('QWork 历史格式不支持');
  const attempt = history.attempts.find(a => a.turnId === turnId);
  if (!attempt) throw new Error('QWork 历史中尚未取得本轮回合');
  const messages = history.messages;
  let start = messages.findIndex(m => m.role === 'user' && m.turn_id === turnId);
  // 原生 turn_id 优先，不能让过期 messageIndex 指向上一轮。
  if (start < 0 && attempt.messageIndex != null) start = messages.findIndex(m => !m.turn_id
    && m.source_index === attempt.messageIndex && m.role === 'user');
  if (start < 0) return { attempt, messages: [], missing: ['本轮用户消息尚未落盘'] };
  let end = messages.findIndex((m, i) => i > start && m.role === 'user' && m.turn_id !== turnId);
  if (end < 0) end = messages.length;
  return { attempt, messages: messages.slice(start, end), missing: [] };
}

function buildQworkTrace({ sessionId, turnId, history, events = [], eventsDropped = 0,
  files = null, deliveries = null, credits = null, diagnostics = [], previousTurnIds = [] }) {
  const selected = selectQworkTurn(history, turnId);
  const { attempt, messages } = selected;
  const calls = new Map(), outputs = [], thinking = [], answers = [];
  const usage = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 };
  let usageCount = 0;
  for (const [messageIndex, message] of messages.entries()) {
    if (message.role === 'assistant') {
      if (message.usage) {
        usageCount++;
        for (const key of Object.keys(usage)) usage[key] += Number(message.usage[key]) || 0;
      }
      const parts = [];
      for (const [blockIndex, block] of (message.blocks || []).entries()) {
        if (block.type === 'thinking') thinking.push(block.thinking || block.text || '');
        if (block.type === 'text' && block.text) parts.push(block.text);
        if (block.type === 'tool_use') {
          const id = block.id || `qwork_${messageIndex}_${blockIndex}`;
          calls.set(id, { tool_call_id: id, name: block.name || 'unknown', original_tool_name: block.name,
            is_mcp: _isMcp(block.name || ''), mcp_server: _mcpServer(block.name || ''),
            args: parsed(block.input), result_text: '', reached_result: false, success: null,
            status: 'unknown', evidence_source: 'qwork_session_history', source_index: message.source_index });
        }
      }
      const answer = parts.join('\n') || message.text;
      if (answer?.trim()) answers.push(answer);
    }
    for (const block of message.blocks || []) if (block.type === 'tool_result') outputs.push(block);
  }
  const unmatched = [];
  for (const output of outputs) {
    const call = calls.get(output.tool_use_id);
    if (!call) { unmatched.push(output.tool_use_id); continue; }
    const result = output.output ?? output.content;
    call.result_text = sanitizeDialogText(textOf(result));
    call.reached_result = result != null;
    call.success = output.is_error === true ? false : call.reached_result && output.is_error === false ? true : null;
    call.status = output.is_error === true ? 'failed' : call.reached_result ? 'completed' : 'unknown';
  }
  const missing = [...selected.missing];
  if (attempt.status !== 'completed') missing.push(`回合未完成：${attempt.status || '未知'}`);
  if (!answers.length) missing.push('未取得本轮回答');
  if (eventsDropped) missing.push(`实时事件超过采集上限，${eventsDropped} 条未保留`);
  if ([...calls.values()].some(c => !c.reached_result)) missing.push('部分工具尚无返回记录');
  if (unmatched.length) missing.push('存在未配对的工具返回');
  const raw = { schema: 'qwork-history-v1', session_id: sessionId, turn_id: turnId,
    previous_turn_ids: previousTurnIds, messages, attempts: [attempt], events,
    files, deliveries, credits, files_scope: 'session_snapshot' };
  const bytes = JSON.stringify(raw);
  const charged = credits?.status === 'settled' && Number.isSafeInteger(credits.chargedMicrocredits)
    ? credits.chargedMicrocredits / 1e6 : null;
  return {
    schema_version: 1, product: 'qwork', session_id: sessionId, request_id: turnId,
    diagnostic_trace_id: attempt.diagnosticTraceId || messages[0]?.trace_id || null,
    answer: sanitizeDialogText(answers.at(-1) || ''), thinking: sanitizeDialogText(thinking.join('\n')),
    assistant_segments: answers.map(sanitizeDialogText), tool_calls: [...calls.values()],
    // 文件清单是元数据，不能把路径存在等同于文件内容已验收。
    artifacts: [], qwork_file_changes: files, usage: usageCount ? usage : null,
    reported_duration: attempt.finishedAt >= attempt.startedAt ? (attempt.finishedAt - attempt.startedAt) / 1000 : null,
    model: messages.filter(m => m.role === 'assistant').at(-1)?.model || attempt.model || null,
    bean_cost: charged, cost_unit: 'QWork积分', turn_status: attempt.status,
    raw_message_captured: messages.length > 0, raw_history: raw,
    ws_captured: false, ipc_captured: true,
    capture_diagnostics: { source: 'QWork sessions.history + events.onSessionEvent',
      status: missing.length ? 'partial' : 'complete', missing, warnings: diagnostics,
      message_count: messages.length, tool_call_count: calls.size, tool_result_count: outputs.length,
      event_count: events.length, events_dropped: eventsDropped, unmatched_tool_results: unmatched,
      raw_sha256: createHash('sha256').update(bytes).digest('hex'), raw_bytes: Buffer.byteLength(bytes) },
  };
}

// 原始数据放 trace 文件，数据库 TEXT 只存索引，避免长工具输出撑爆 64KB 字段。
function qworkRawIndex(trace) {
  return JSON.stringify({ schema: 'qwork-trace-index-v1', product: 'qwork',
    session_id: trace.session_id, turn_id: trace.request_id, raw_history_path: 'trace.raw_history',
    sha256: trace.capture_diagnostics?.raw_sha256, capture: trace.capture_diagnostics });
}

module.exports = { idOf, selectQworkTurn, buildQworkTrace, qworkRawIndex };
