// WorkBuddy “复制 message”的公开 JSON 格式。只消费本轮 assistant 内容；
// 不从回答中的“已完成”描述推断工具成功，也不读取工具提及的本地文件。
const { _isMcp, _mcpServer, sanitizeDialogText } = require('./ws-trace');
const record = value => value && typeof value === 'object' && !Array.isArray(value);

function parseWorkbuddyMessage(raw) {
  let data;
  try { data = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { return null; }
  if (!record(data)) return null;
  const messages = Array.isArray(data.messages) ? data.messages : [data];
  const assistant = messages.filter(m => record(m) && (m.messageType === 'assistant' || m.role === 'assistant')
    && Array.isArray(m.content) && (!data.requestId || !m.requestId || m.requestId === data.requestId));
  if (!assistant.length) return null;
  // 没有 wrapper requestId 的旧格式只选择最后一轮，不混入历史记录。
  const requestId = data.requestId || assistant.at(-1).requestId;
  const current = requestId ? assistant.filter(m => !m.requestId || m.requestId === requestId) : assistant;
  const thinking = [], answers = [], calls = new Map();
  for (const message of current) {
    for (const block of message.content) {
      if (!record(block)) continue;
      if (['reasoning', 'thinking'].includes(block.type) && typeof block.text === 'string') thinking.push(block.text);
      if (block.type === 'text' && typeof block.text === 'string') answers.push(block.text);
      if (block.type !== 'tool-call' || !record(block.tool) || typeof block.tool.name !== 'string') continue;
      const tool = block.tool;
      const id = String(tool.id || `workbuddy_${calls.size}`);
      const result = tool.result;
      const terminal = ['executed', 'completed', 'failed', 'cancelled'].includes(tool.status);
      const reached = terminal && result != null;
      calls.set(id, {
        tool_call_id: id, name: tool.name, original_tool_name: tool.name,
        is_mcp: _isMcp(tool.name), mcp_server: _mcpServer(tool.name),
        args: tool.args, status: tool.status || 'unknown',
        result_text: result == null ? '' : sanitizeDialogText(typeof result === 'string' ? result : JSON.stringify(result)),
        reached_result: reached,
        success: tool.status === 'failed' || tool.status === 'cancelled' || result?.success === false ? false
          : reached && ['executed', 'completed'].includes(tool.status) ? true : null,
        evidence_source: 'workbuddy_raw_message',
      });
    }
  }
  return {
    request_id: requestId || null,
    session_id: current.at(-1).conversationId || null,
    thinking: sanitizeDialogText(thinking.join('\n')),
    // DOM 保留最终常显回答；这里只在 DOM 没抓到回答时提供原始文本兜底。
    answer: sanitizeDialogText(answers.join('\n')),
    tool_calls: [...calls.values()], message_count: current.length,
  };
}

module.exports = { parseWorkbuddyMessage };
