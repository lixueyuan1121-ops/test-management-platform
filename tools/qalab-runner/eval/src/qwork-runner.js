'use strict';
// 走 QWork 渲染器自身使用的会话 IPC，不依赖折叠侧栏、虚拟列表或剪贴板中的旧消息。
const { randomUUID } = require('node:crypto');
const { normalize, configError } = require('./dialog-config');
const { prepareQworkFiles } = require('./qwork-native-files');
const { idOf, buildQworkTrace, qworkRawIndex } = require('./qwork-trace');

function findQworkModel(catalog, wanted) {
  const models = catalog?.models || [];
  const target = normalize(wanted || catalog?.defaultModel);
  const matches = models.filter(m => [m.value, m.label].some(v => normalize(v) === target));
  if (matches.length !== 1 || matches[0].disabled) throw configError(
    `QWork 模型「${wanted || catalog?.defaultModel || '默认'}」不可用；可选：${models.map(m => m.label || m.value).join('、')}`);
  return matches[0];
}

class QworkRunner {
  constructor(page, config = {}, execution = {}, logger = null) {
    this.page = page; this.config = config; this.execution = execution; this.logger = logger;
    this.sessionId = null; this.previousTurnIds = []; this.trace = {};
  }
  getDomTrace() { return { buildTrace: runId => ({ ...this.trace, run_id: runId }) }; }
  _invoke(group, method, args = [], timeout = 15000) {
    return this.page.evaluate(async ({ group, method, args, timeout }) => {
      const api = window.workGui?.[group];
      if (typeof api?.[method] !== 'function') throw new Error(`QWork 接口不可用：${group}.${method}`);
      let timer;
      try {
        return await Promise.race([api[method](...args), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`QWork 接口超时：${group}.${method}`)), timeout);
        })]);
      } finally { clearTimeout(timer); }
    }, { group, method, args, timeout });
  }
  async _configure(testCase, create) {
    const options = testCase.dialogOptions || {};
    this.executionConfig = { schema_version: 1, requested: options, observed: {}, status: 'checking', transport: 'qwork_client_ipc' };
    if (options.chatMode || options.thinkingDepth) throw configError('QWork 暂不支持下发纳米Work的对话模式或思考深度');
    const catalog = await this._invoke('models', 'list');
    if (catalog?.verified === false || catalog?.stale) throw configError('QWork 模型列表未验证或已过期');
    const model = findQworkModel(catalog, options.model || this.config.model);
    if (create) {
      const created = await this._invoke('sessions', 'create', [{ model: model.value, cwd: '',
        title: `QALAB ${testCase.caseId || testCase.run_id || randomUUID()} ${testCase.question.slice(0, 35)}`,
        permission_mode: 'default', conversation_mode: 'default' }], 60000);
      this.sessionId = idOf(created?.session_id);
      if (!this.sessionId) throw new Error('QWork 创建会话未返回 ID，未发送题目');
    } else await this._invoke('sessions', 'setModel', [this.sessionId, model.value]);
    const sessions = await this._invoke('sessions', 'list');
    const current = sessions.find(s => idOf(s.session_id) === this.sessionId);
    if (!current || normalize(current.model) !== normalize(model.value)) throw configError('QWork 模型回读不一致，未发送题目');
    this.executionConfig.observed = { model: current.model, model_label: model.label, permission_mode: current.permission_mode,
      conversation_mode: current.conversation_mode };
    this.executionConfig.status = 'verified';
  }
  async _attachments(paths = []) {
    if (!paths.length) return [];
    const prepared = await prepareQworkFiles(this.page, paths, this.config);
    if (!Array.isArray(prepared) || prepared.length !== paths.length || prepared.some(p => !p.preparedId))
      throw new Error('QWork 附件准备未完成，未发送题目');
    return prepared.map(p => p.preparedId);
  }

  async _startEvents() {
    this.eventKey = `__qalabQwork_${randomUUID().replaceAll('-', '')}`;
    await this.page.evaluate(({ key, sessionId, maxBytes }) => {
      const state = { events: [], bytes: 0, dropped: 0 };
      const off = window.workGui.events.onSessionEvent(event => {
        const id = typeof event.session_id === 'string' ? event.session_id : event.session_id?.[0];
        if (id !== sessionId) return;
        const item = { received_at: Date.now(), event };
        const bytes = new TextEncoder().encode(JSON.stringify(item)).length;
        if (state.bytes + bytes > maxBytes) { state.dropped++; return; }
        state.bytes += bytes; state.events.push(item);
      });
      window[key] = { state, off };
    }, { key: this.eventKey, sessionId: this.sessionId, maxBytes: this.config.eventMaxBytes || 5 * 1024 * 1024 });
  }
  async _stopEvents() {
    if (!this.eventKey) return { events: [], dropped: 0 };
    const key = this.eventKey; this.eventKey = null;
    return await this.page.evaluate(key => {
      const capture = window[key]; if (!capture) return { events: [], dropped: 0 };
      capture.off?.(); delete window[key]; return capture.state;
    }, key);
  }
  async _waitTurn(beforeIds) {
    const deadline = Date.now() + (this.execution.taskTimeout || this.execution.responseTimeout || 600000);
    let next, history;
    while (Date.now() < deadline) {
      history = await this._invoke('sessions', 'history', [this.sessionId]);
      const candidates = history.attempts.filter(a => !beforeIds.includes(a.turnId));
      if (candidates.length > 1) throw new Error('QWork 出现多个新回合，无法唯一归属本条题目');
      next = candidates[0];
      if (next) {
        this.turnId = next.turnId;
        if (!['pending', 'running'].includes(next.status)) return { history, attempt: next };
        const permissions = await this._invoke('sessions', 'pendingPermissions', [this.sessionId]);
        const questions = await this._invoke('sessions', 'pendingQuestions', [this.sessionId]);
        if (permissions.length || questions.length) {
          this.pendingInteractions = { permissions, questions };
          throw new Error('[QWORK_INPUT_REQUIRED] QWork 等待权限确认或补充回答，已保存请求内容');
        }
      }
      await this.page.waitForTimeout(this.config.pollMs || 1000);
    }
    throw new Error('[QWORK_TIMEOUT] QWork 本轮执行超时，已保留返回内容');
  }
  async _capture(history, turnId) {
    const diagnostics = [];
    const optional = async (group, method, args) => {
      try { return await this._invoke(group, method, args, 8000); }
      catch (error) { diagnostics.push({ field: `${group}.${method}`, reason: error.message.split('\n')[0] }); return null; }
    };
    const attempt = history.attempts.find(a => a.turnId === turnId);
    const traceId = attempt?.diagnosticTraceId;
    const [files, deliveries, credits] = await Promise.all([
      optional('files', 'latestFileChanges', [this.sessionId]), optional('sessions', 'deliveries', [this.sessionId]),
      traceId ? optional('account', 'turnCredits', [traceId]) : null,
    ]);
    const stream = await this._stopEvents();
    this.trace = buildQworkTrace({ sessionId: this.sessionId, turnId, history,
      events: stream.events, eventsDropped: stream.dropped, files, deliveries, credits, diagnostics,
      previousTurnIds: [...this.previousTurnIds] });
    this.trace.pending_interactions = this.pendingInteractions || null;
    this.trace.execution_config = this.executionConfig;
    this.previousTurnIds.push(turnId);
  }
  async _run(testCase, create) {
    const started = Date.now();
    this.trace = { product: 'qwork', tool_calls: [], artifacts: [] };
    this.turnId = null; this.pendingInteractions = null; this.executionConfig = null; this.cancelError = null;
    let error = null, history, completed = false, dispatched = false;
    try {
      await this._configure(testCase, create);
      const prepared = await this._attachments(testCase.attachmentPaths);
      const baseline = await this._invoke('sessions', 'history', [this.sessionId]);
      await this._startEvents();
      // 只发送一次；接口超时也不重发，避免生成重复任务。
      dispatched = true;
      await this._invoke('sessions', 'prompt', [this.sessionId, testCase.question,
        prepared.length ? { preparedAttachmentIds: prepared } : {}], 60000);
      const done = await this._waitTurn(baseline.attempts.map(a => a.turnId));
      history = done.history;
      completed = done.attempt.status === 'completed';
      if (!completed) error = new Error(`[QWORK_EXECUTION_FAILED] ${done.attempt.failure?.message || done.attempt.status}`);
    } catch (failure) { error = failure; }
    if (error && dispatched) {
      // 只取消本执行器创建的会话，保证下一条开始前当前轮不再继续产生副作用。
      try { await this._invoke('sessions', 'cancel', [this.sessionId]); }
      catch (failure) { this.cancelError = failure.message; }
    }
    try {
      if (this.sessionId && dispatched) {
        history = await this._invoke('sessions', 'history', [this.sessionId]);
        const next = history.attempts.filter(a => !this.previousTurnIds.includes(a.turnId));
        this.turnId ||= next.length === 1 ? next[0].turnId : null;
        if (this.turnId) await this._capture(history, this.turnId);
        else throw new Error('未确认本轮回合 ID，未使用其他轮次的内容');
      }
    } catch (failure) {
      error ||= failure; this.trace.capture_error = failure.message;
      // 仅存本执行器创建会话的诊断原文，无法分轮时不拿它填本轮答案。
      if (history) this.trace.unresolved_history = { ...history, scope: 'session_diagnostic_only' };
    }
    finally {
      const stream = await this._stopEvents().catch(() => null);
      if (stream?.events?.length) this.trace.unresolved_events = stream.events;
    }
    const success = !error && completed && !!this.trace.answer;
    if (error && this.executionConfig?.status === 'checking') this.executionConfig.status = 'failed';
    this.trace.execution_config = this.executionConfig;
    this.trace.session_id ||= this.sessionId;
    if (error) this.trace.execution_error = error.message;
    if (this.cancelError) this.trace.cancel_error = this.cancelError;
    return { caseId: testCase.caseId, row: testCase.row, account: 'qwork',
      conversationId: testCase.conversationId, turnIndex: testCase.turnIndex, question: testCase.question,
      answer: this.trace.answer || '', rawMessage: qworkRawIndex(this.trace),
      reportedDuration: this.trace.reported_duration, beanCost: this.trace.bean_cost,
      cost: this.trace.usage ? Object.values(this.trace.usage).reduce((n, v) => n + v, 0) : null,
      executionConfig: this.executionConfig, durationMs: Date.now() - started, success,
      incomplete: !completed, completeReason: success ? 'completed' : error?.message || '未取得本轮回答',
      errorMessage: error?.message || null, errorCode: error ? error.message.match(/\[([A-Z_]+)\]/)?.[1] || 'QWORK_EXECUTION_ERROR' : null };
  }
  async runOne(testCase) { this.sessionId = null; this.previousTurnIds = []; return this._run(testCase, true); }
  async runConversationTurns(turns, onTurnDone) {
    this.sessionId = null; this.previousTurnIds = [];
    let aborted = false;
    const results = [];
    for (const [i, testCase] of turns.entries()) {
      if (aborted) this.trace = { product: 'qwork', session_id: this.sessionId, tool_calls: [], artifacts: [], capture_error: '上一轮失败，未发送本轮' };
      const result = aborted ? { success: false, answer: '', errorCode: 'QWORK_PREVIOUS_TURN_FAILED',
        errorMessage: '上一轮未完成，后续轮未发送，避免上下文错位' } : await this._run(testCase, i === 0);
      results.push(result); await onTurnDone?.(result, testCase);
      if (!result.success) aborted = true;
    }
    return results;
  }
}
module.exports = { QworkRunner, findQworkModel };
