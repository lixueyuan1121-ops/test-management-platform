// 抓 work.n.cn 对话 WebSocket 事件帧,规整成 trace(思考/工具·mcp 调用/产物)。
// 协议(见 openclaw360-web/src/ui/AGENTS.md):帧 {type:"event",event,payload,seq};
// event=="agent" 用 payload.stream 二次判别:thinking→data.text;tool→data.{name,originalToolName,phase,toolCallId,args,result}。
// nami_panel 是信封,内层在 payload.data,需展开一层。mcp 靠 originalToolName 的 mcp__<server>__<tool> 前缀。
// 同一 toolCallId 跨 start/update/result 多帧,按 id 聚合。

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

// 初始状态
function newState() {
  return { sessionId: null, runId: null, thinking: '', toolsById: new Map(), toolOrder: [], answer: '', artifacts: [], sawAny: false, wsConnected: false };
}

// 处理一帧(已 JSON.parse 的对象)。纯函数式副作用在 state 上。异常安全由调用方包 try。
function handleFrame(state, frame) {
  if (!frame || frame.type !== 'event') return;
  let payload = _unwrapPanel(frame.payload || {});
  const event = frame.event;
  if (payload && payload.sessionId && !state.sessionId) state.sessionId = payload.sessionId;
  if (payload && payload.runId && !state.runId) state.runId = payload.runId;

  if (event === 'agent') {
    state.sawAny = true;
    const stream = payload.stream;
    const data = payload.data || {};
    if (stream === 'thinking') {
      const t = (data.text != null ? data.text : (data.data && data.data.text)) || '';
      if (t) state.thinking += t;
    } else if (stream === 'assistant') {
      const t = (data.text != null ? data.text : '') || '';
      if (t) state.answer += t;
    } else if (stream === 'tool') {
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
    reset() { const s = newState(); Object.assign(state, s); state.toolsById = s.toolsById; state.toolOrder = s.toolOrder; },
    buildTrace(runId) {
      const tool_calls = state.toolOrder.map(id => {
        const e = state.toolsById.get(id);
        return { tool_call_id: e.tool_call_id, name: e.name, original_tool_name: e.original_tool_name,
                 is_mcp: e.is_mcp, mcp_server: e.mcp_server, args: e.args, result_text: e.result_text, reached_result: e.reached_result };
      });
      return { session_id: state.sessionId, run_id: runId || state.runId,
               thinking: state.thinking, tool_calls, artifacts: state.artifacts,
               answer: state.answer, ws_captured: state.sawAny, ws_connected: state.wsConnected };
    },
  };
}

module.exports = { attachWsTrace, handleFrame, newState, _isMcp, _mcpServer, _isEmptyResult };
