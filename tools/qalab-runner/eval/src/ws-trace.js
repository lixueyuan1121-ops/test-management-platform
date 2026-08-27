// 抓 work.n.cn 对话 WebSocket 事件帧,规整成 trace(思考/工具·mcp 调用/产物)。
// 协议(见 openclaw360-web/src/ui/AGENTS.md):帧 {type:"event",event,payload,seq};
// event=="agent" 用 payload.stream 二次判别:thinking→data.text;tool→data.{name,originalToolName,phase,toolCallId,args,result}。
// nami_panel 是信封,内层在 payload.data,需展开一层。mcp 靠 originalToolName 的 mcp__<server>__<tool> 前缀。
// 同一 toolCallId 跨 start/update/result 多帧,按 id 聚合。

// ---- 对话文本清洗(子项3: 修复特殊字符/转义导致关键内容截断问题) ----
// 与后端 claude_runner.sanitize_dialog_text / split_think_blocks 同语义(单一逻辑,两端各自实现)。
const _B64_EMPTY_TEXT_BLOCK = 'eyJ0eXBlIjoidGV4dCIsInRleHQiOiIifQ==';
const _THINK_BLOCK_RE = /<\s*(think|thinking|thought)\s*>([\s\S]*?)<\s*\/\s*\1\s*>/gi;
const _THINK_OPEN_TAIL_RE = /<\s*(think|thinking|thought)\s*>(?![\s\S]*<\s*\/\s*\1\s*>)([\s\S]*)\s*$/i;
const _CTRL_CHARS_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;

/**
 * 去控制字符、去 base64 空块占位串(含流式被截断的前缀形态)、统一换行。
 * 与后端 sanitize_dialog_text 口径一致。
 */
function sanitizeDialogText(s) {
  if (!s) return '';
  s = String(s).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  // 完整占位串
  while (s.includes(_B64_EMPTY_TEXT_BLOCK)) {
    s = s.replace(_B64_EMPTY_TEXT_BLOCK, '');
  }
  // 流式收尾只吐出前半截:按">=16 字符前缀"删
  for (let cut = _B64_EMPTY_TEXT_BLOCK.length - 1; cut >= 16; cut--) {
    const prefix = _B64_EMPTY_TEXT_BLOCK.slice(0, cut);
    if (s.endsWith(prefix)) { s = s.slice(0, -prefix.length); break; }
  }
  return s.replace(_CTRL_CHARS_RE, '');
}

/**
 * 把正文里内联的 <think>/<thinking>/<thought> 块剥出来。
 * 返回 { body: string, thinking: string }。
 * 与后端 split_think_blocks 口径一致。
 */
function splitThinkBlocks(s) {
  s = String(s || '');
  if (!s.includes('<')) return { body: s, thinking: '' };
  const thinks = [];
  let body = s.replace(_THINK_BLOCK_RE, (_, _tag, content) => {
    const t = content.trim();
    if (t) thinks.push(t);
    return '';
  });
  // 未闭合的 <think> 开标签:开标签到文末归思考
  const m = _THINK_OPEN_TAIL_RE.exec(body);
  if (m) {
    const tail = (m[2] || '').trim();
    if (tail) thinks.push(tail);
    body = body.slice(0, m.index);
  }
  return { body: body.trim(), thinking: thinks.join('\n') };
}

function _unwrapPanel(payload) {
  // nami_panel 信封:真正字段在 payload.data(内层可能又是 {stream,data,...})。展开一层。
  if (payload && payload.stream === 'nami_panel' && payload.data && typeof payload.data === 'object') {
    return payload.data;
  }
  return payload;
}

function _isMcp(originalToolName) {
  return typeof originalToolName === 'string' && originalToolName.startsWith('mcp__');
}
function _mcpServer(originalToolName) {
  if (!_isMcp(originalToolName)) return null;
  const parts = originalToolName.split('__'); // mcp__<server>__<tool>
  return parts.length >= 2 ? parts[1] : null;
}

// 空结果判定:null / 空字符串 / 空对象 / 空数组 视为"空",不覆盖已有 result_text。
// (移植权威 app-tool-stream.ts 的 isEmptyToolResultPayload 语义:同一 toolCallId 完整结果后
//  可能再来一帧 phase:result 且 result:{} 空对象,若照写会把已抓到的真实工具输出覆盖没了。)
function _isEmptyResult(r) {
  if (r == null) return true;
  if (typeof r === 'string') return r.trim() === '';
  if (Array.isArray(r)) return r.length === 0;
  if (typeof r === 'object') return Object.keys(r).length === 0;
  return false;
}

// NO_REPLY 是"静默回复"占位(见 app-tool-stream isSilentReplyStream / handleChatEvent),非真实答案,当空处理。
function _isSilentReply(t) {
  return typeof t === 'string' && t.trim() === 'NO_REPLY';
}

// 从 stream:"assistant" 帧的 data 取正文快照(移植权威 namiAssistantStreamText,app-tool-stream.ts:603):
// 优先 data.text → data.data.text → data.data.delta → data.delta。⚠️ 这是【快照】非 delta(见消费端
// chat.ts:1211 longer-wins 覆盖策略),故抓取侧须"取更长者覆盖",绝不 += 累加。
function _assistantStreamText(data) {
  if (!data || typeof data !== 'object') return '';
  if (typeof data.text === 'string') return data.text;
  const nested = data.data;
  if (nested && typeof nested === 'object') {
    if (typeof nested.text === 'string') return nested.text;
    if (typeof nested.delta === 'string') return nested.delta;
  }
  if (typeof data.delta === 'string') return data.delta;
  return '';
}

// 从 stream:"thinking" 帧的 data 取思考快照(移植权威 extractThinkingStreamPayloadText,app-tool-stream.ts:482):
// 优先 data.data.text → data.text。同为【快照】非 delta(消费端 chat.ts:1230 longer-wins 覆盖),抓取侧取更长者覆盖。
function _thinkingStreamText(data) {
  if (!data || typeof data !== 'object') return '';
  const inner = data.data;
  if (inner && typeof inner === 'object' && typeof inner.text === 'string') return inner.text;
  if (typeof data.text === 'string') return data.text;
  return '';
}

// 从 chat/final 帧的 message 取【完整答案正文】(移植权威 extractRawText,message-extract.ts:104):
// content 为字符串→直接用;为数组→拼接所有 type:"text" 块;否则回退 message.text。这是最权威的最终答案来源。
function _msgAnswer(message) {
  if (!message || typeof message !== 'object') return '';
  const content = message.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    const parts = [];
    for (const p of content) {
      if (p && typeof p === 'object' && p.type === 'text') {
        const t = typeof p.text === 'string' ? p.text
          : (p.text && typeof p.text === 'object' ? JSON.stringify(p.text) : '');
        if (t) parts.push(t);
      }
    }
    if (parts.length) return parts.join('\n');
  }
  if (typeof message.text === 'string') return message.text;
  return '';
}

// 从 chat/final 帧的 message 取【完整思考】(移植权威 extractThinking,message-extract.ts:59):
// content 数组里所有 type:"thinking" 块的 thinking 文本拼接。
function _msgThinking(message) {
  if (!message || typeof message !== 'object') return '';
  const content = message.content;
  if (!Array.isArray(content)) return '';
  const parts = [];
  for (const p of content) {
    if (p && typeof p === 'object' && p.type === 'thinking' && typeof p.thinking === 'string') {
      const c = p.thinking.trim();
      if (c) parts.push(c);
    }
  }
  return parts.join('\n');
}

// 初始状态
// answer/thinking 双来源:
//  · finalAnswer/finalThinking = 权威来源,来自 event:"chat" + state:"final" 的 message(完整、已收口)
//  · answerSegments/answerCur/thinkingStream = 流式兜底,来自 event:"agent" stream:assistant/thinking(final 帧丢失时用)
// buildTrace 优先取 final,缺失才拼流式段。
function newState() {
  return {
    sessionId: null, runId: null,
    finalAnswer: '', finalThinking: '',
    answerSegments: [], answerCur: '', thinkingStream: '',
    toolsById: new Map(), toolOrder: [], artifacts: [],
    sawAny: false, wsConnected: false,
  };
}

// 处理一帧(已 JSON.parse 的对象)。纯函数式副作用在 state 上。异常安全由调用方包 try。
// 抓两类事件:
//  · event:"chat" + state:"final" → payload.message 是【权威完整答案+思考】(收口态,最可靠)
//  · event:"agent" → stream 二次判别:thinking/assistant(流式快照,final 缺失时兜底)/ tool(工具调用)
function handleFrame(state, frame) {
  if (!frame || frame.type !== 'event') return;
  const event = frame.event;

  // ── event:"chat":对话生命周期终态。state:"final" 的 message 携带完整答案正文+思考 ──
  // (见 openclaw360-web app-gateway.ts handleChatGatewayEvent:1073 / message-extract.ts extractRawText。)
  if (event === 'chat') {
    const payload = frame.payload || {};
    if (payload.sessionId && !state.sessionId) state.sessionId = payload.sessionId;
    if (payload.runId && !state.runId) state.runId = payload.runId;
    if (payload.state === 'final') {
      state.sawAny = true;
      const ans = _msgAnswer(payload.message);
      // NO_REPLY 是静默占位非真实答案;取更长者覆盖(多次 final 去重,保留最全)
      if (ans && !_isSilentReply(ans) && ans.length >= state.finalAnswer.length) state.finalAnswer = ans;
      const th = _msgThinking(payload.message);
      if (th && th.length >= state.finalThinking.length) state.finalThinking = th;
    }
    return;
  }

  if (event !== 'agent') return;
  let payload = _unwrapPanel(frame.payload || {});
  if (payload && payload.sessionId && !state.sessionId) state.sessionId = payload.sessionId;
  if (payload && payload.runId && !state.runId) state.runId = payload.runId;

  state.sawAny = true;
  const stream = payload.stream;
  const data = payload.data || {};
  if (stream === 'thinking') {
    // 【快照】非 delta:每帧是从头到当前的全量文本。longer-wins 覆盖,绝不累加(见 chat.ts:1230)。
    const t = _thinkingStreamText(data);
    if (t && t.length >= state.thinkingStream.length) state.thinkingStream = t;
  } else if (stream === 'assistant') {
    // 【快照】非 delta:longer-wins 覆盖当前段。工具边界会把 answerCur 切成一段推入 answerSegments
    // (见 app-tool-stream.ts:1127 chatStreamSegments 语义),保证"段→工具→段"视觉/文本顺序。
    const t = _assistantStreamText(data);
    if (t && !_isSilentReply(t) && t.length >= state.answerCur.length) state.answerCur = t;
  } else if (stream === 'tool') {
    // 工具边界:若当前有累积的 assistant 段,先切段(移植 chatStreamSegments 提交语义)
    if (state.answerCur) { state.answerSegments.push(state.answerCur); state.answerCur = ''; }
    const id = data.toolCallId || data.subToolCallId || `_anon_${state.toolOrder.length}`;
    let entry = state.toolsById.get(id);
    if (!entry) {
      entry = { tool_call_id: id, name: data.name || '', original_tool_name: data.originalToolName || data.name || '',
                is_mcp: false, mcp_server: null, args: undefined, result_text: '', reached_result: false };
      state.toolsById.set(id, entry); state.toolOrder.push(id);
    }
    if (data.name) entry.name = data.name;
    if (data.originalToolName) entry.original_tool_name = data.originalToolName;
    entry.is_mcp = _isMcp(entry.original_tool_name);
    entry.mcp_server = _mcpServer(entry.original_tool_name);
    if (data.args !== undefined) entry.args = data.args;
    // 结果文本:result/partialResult。⚠️ 跳过"空 result"帧——同一 toolCallId 完整结果后
    // 可能再来一帧 phase:result 且 result:{} (空对象),不能让它覆盖已抓到的真实输出
    // (移植权威 app-tool-stream.ts 的 isEmptyToolResultPayload 语义)。
    // 注:partialResult 是累积快照(非 delta),非空覆盖语义本身正确;只须挡空 result。
    const r = data.result != null ? data.result : data.partialResult;
    if (r != null && !_isEmptyResult(r)) {
      entry.result_text = typeof r === 'string' ? r : JSON.stringify(r);
    }
    if (data.phase === 'result') entry.reached_result = true;
  }
}

// 挂到 page:监听 framereceived(收到的服务端帧即对话数据)。返回 collector。
function attachWsTrace(page) {
  const state = newState();
  const onWs = (ws) => {
    state.wsConnected = true;   // 至少挂上了一个 WS(区分"WS没挂上"vs"挂上了但本次无对话活动")
    ws.on('framereceived', (ev) => {
      try {
        const payloadStr = typeof ev === 'string' ? ev : (ev && ev.payload);
        if (!payloadStr || typeof payloadStr !== 'string') return;
        if (payloadStr[0] !== '{') return; // 非 JSON 文本帧(如心跳)跳过
        handleFrame(state, JSON.parse(payloadStr));
      } catch (_) { /* 单帧解析失败不影响整体 */ }
    });
  };
  try { page.on('websocket', onWs); } catch (_) {}
  return {
    _state: state,
    reset() { const s = newState(); Object.assign(state, s); state.toolsById = s.toolsById; state.toolOrder = s.toolOrder; state.answerSegments = s.answerSegments; state.artifacts = s.artifacts; },
    buildTrace(runId) {
      const tool_calls = state.toolOrder.map(id => {
        const e = state.toolsById.get(id);
        return { tool_call_id: e.tool_call_id, name: e.name, original_tool_name: e.original_tool_name,
                 is_mcp: e.is_mcp, mcp_server: e.mcp_server, args: e.args, result_text: e.result_text, reached_result: e.reached_result };
      });
      // answer:优先权威 final(收口完整);缺失才拼流式段(段 + 未切的当前段)。
      const streamedAnswer = [...state.answerSegments, state.answerCur].filter(Boolean).join('\n').trim();
      const rawAnswer = state.finalAnswer || streamedAnswer;
      // 清洗(子项3):去控制字符/base64 占位噪声,再把内联 <think> 块剥进 thinking——
      // 修复"答案中途夹思考标签→判定侧把答案误读为被截断/思考为空"两类误判。
      const cleanedAnswer = sanitizeDialogText(rawAnswer);
      const { body: answer, thinking: inlineThink } = splitThinkBlocks(cleanedAnswer);
      // thinking:优先 final message 里的 thinking 块;缺失才用流式 thinking 快照;再兜底答案里剥出的内联思考。
      let thinking = sanitizeDialogText(state.finalThinking || state.thinkingStream);
      if (!thinking.trim() && inlineThink) thinking = inlineThink;
      return { session_id: state.sessionId, run_id: runId || state.runId,
               thinking, tool_calls, artifacts: state.artifacts,
               answer, ws_captured: state.sawAny, ws_connected: state.wsConnected };
    },
  };
}

module.exports = { attachWsTrace, handleFrame, newState, sanitizeDialogText, splitThinkBlocks, _isMcp, _mcpServer, _isEmptyResult, _msgAnswer, _msgThinking, _assistantStreamText, _thinkingStreamText };
