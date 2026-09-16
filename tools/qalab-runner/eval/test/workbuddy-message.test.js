const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseWorkbuddyMessage } = require('../src/workbuddy-message');
const { WorkbuddyDomTrace } = require('../src/workbuddy-dom-trace');
const assistant = (content, requestId = 'current') => ({ messageType: 'assistant', requestId, conversationId: 'session', content });
const tool = (overrides = {}) => ({ type: 'tool-call', tool: { id: 'tool-1', name: 'Bash', status: 'executed', args: { command: 'fixture' }, result: { success: true, result: { type: 'text', text: 'fixture output' } }, ...overrides } });

test('复制 message 的嵌套工具名称/参数/结果进入 trace，而非只在 raw_message 中留存', () => {
  const raw = JSON.stringify({ requestId: 'current', messages: [assistant([{ type: 'reasoning', text: '分析过程' }, tool(), { type: 'text', text: '文本答案' }])] });
  const collector = new WorkbuddyDomTrace();
  assert.equal(collector.mergeRawMessage(raw), true);
  const trace = collector.buildTrace('run');
  assert.equal(trace.tool_calls.length, 1);
  assert.equal(trace.tool_calls[0].name, 'Bash');
  assert.deepEqual(trace.tool_calls[0].args, { command: 'fixture' });
  assert.match(trace.tool_calls[0].result_text, /fixture output/);
  assert.equal(trace.tool_calls[0].reached_result, true);
  assert.equal(trace.session_id, 'session');
  assert.equal(trace.thinking, '分析过程');
  assert.equal(trace.answer, '文本答案');
  assert.equal(trace.raw_message_captured, true);
});

test('按 requestId 隔离本轮，不把历史工具调用及用户引用内容计入', () => {
  const result = parseWorkbuddyMessage({ requestId: 'current', messages: [assistant([tool({ id: 'old' })], 'old'),
    { messageType: 'user', content: [tool({ id: 'user' })] }, assistant([tool({ id: 'current' })])] });
  assert.deepEqual(result.tool_calls.map(t => t.tool_call_id), ['current']);
});

test('同一工具 id 快照去重，保留最终失败及真实返回，不误算成功', () => {
  const result = parseWorkbuddyMessage({ messages: [assistant([tool({ status: 'pending', result: undefined })]),
    assistant([tool({ status: 'failed', result: { success: false, result: 'permission denied' } })])] });
  assert.equal(result.tool_calls.length, 1);
  assert.equal(result.tool_calls[0].success, false);
  assert.equal(result.tool_calls[0].reached_result, true);
});

test('执行中即使已有部分输出也不能标记得到最终结果', () => {
  const result = parseWorkbuddyMessage(assistant([tool({ status: 'stream_executing' })]));
  assert.equal(result.tool_calls[0].reached_result, false);
  assert.equal(result.tool_calls[0].success, null);
});

test('保留 MCP 标识，未知结构与回答自述不虚构工具/产物', () => {
  const result = parseWorkbuddyMessage(assistant([tool({ name: 'mcp__images__edit' }), { type: 'text', text: '文件已保存到 /private/output.png' }]));
  assert.equal(result.tool_calls[0].is_mcp, true);
  assert.equal(result.tool_calls[0].mcp_server, 'images');
  assert.equal(parseWorkbuddyMessage(assistant([{ type: 'text', text: '调用了工具' }])).tool_calls.length, 0);
  for (const raw of ['', 'not json', '{}', '{"messages":[]}', '{"messages":[{}]}']) assert.equal(parseWorkbuddyMessage(raw), null);
});

test('保留 DOM 最终答案，reset 清除前一题的工具、requestId 和诊断', () => {
  const collector = new WorkbuddyDomTrace();
  collector._data.answer = '最终答案';
  collector.mergeRawMessage(assistant([tool(), { type: 'text', text: '中间进度' }]));
  assert.equal(collector.buildTrace().answer, '最终答案');
  collector.setExecutionError({ stage: '测试' });
  collector.reset();
  assert.deepEqual(collector.buildTrace().tool_calls, []);
  assert.equal(collector.buildTrace().request_id, null);
  assert.equal(collector.buildTrace().execution_error, null);
});
