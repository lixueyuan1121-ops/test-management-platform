// src/workbuddy-runner.js
// WorkBuddy 对话驱动器：CDP 已连的 page 上开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+trace。
// 对外接口对齐 DesktopRunner（runOne/runConversationTurns/result 形状），使 bin/ai-eval.js 的 reportRun 无缝复用。
const { WorkbuddyDomTrace } = require('./workbuddy-dom-trace');

function looksIncomplete(answer) {
  const t = (answer || '').trim();
  if (!t) return true;
  return /思考中|生成中|Thinking/i.test(t.slice(-20));
}

class WorkbuddyRunner {
  constructor(page, workbuddyConfig = {}, executionConfig = {}, logger = null) {
    this.page = page; this.wb = workbuddyConfig; this.execution = executionConfig; this.logger = logger;
    this.trace = new WorkbuddyDomTrace(this._traceSel());
  }
  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }
  // 把 config.workbuddy 的选择器映射成 WorkbuddyDomTrace 认的键
  _traceSel() {
    const w = this.wb;
    return {
      answer: w.answerSelector, thinkingCollapse: w.thinkingCollapseSelector,
      thinkingHeader: w.thinkingHeaderSelector, thinkingTitle: w.thinkingTitleSelector,
      thinkingContent: w.thinkingContentSelector, sourcesTrigger: w.sourcesTriggerSelector,
      sourcesCount: w.sourcesCountSelector, sourcesPanelTitle: w.sourcesPanelTitleSelector,
      sourcesList: w.sourcesListSelector, footer: w.footerSelector,
    };
  }
  getDomTrace() { return this.trace; }

  async _openCleanConversation() {
    const nt = this.page.locator(this.wb.newTaskSelector).first();
    if (await nt.count()) { await nt.click().catch(() => {}); await this.page.waitForTimeout(1200); }
    await this.page.locator(this.wb.inputSelector).first().waitFor({ state: 'visible', timeout: 15000 });
    return true;
  }

  // 选模型档：点开 cr-model-selector__trigger，选名字匹配 dialogOptions.model 的 item。未指定则不动。
  async _applyDialogOptions() {
    const model = (this.execution.dialogOptions || {}).model;
    if (!model) return;
    try {
      await this.page.locator(this.wb.modelTriggerSelector).first().click();
      await this.page.waitForTimeout(600);
      const opt = this.page.locator(this.wb.modelOptionSelector, { hasText: model }).first();
      if (await opt.count()) { await opt.click(); await this.page.waitForTimeout(400); }
      else { this._warn(`   模型档「${model}」未在下拉中找到，用当前默认`); await this.page.keyboard.press('Escape').catch(() => {}); }
    } catch (e) { this._warn(`   选模型档失败(用默认): ${(e.message || '').split('\n')[0]}`); }
  }

  async _footerCount() { return await this.page.locator(this.wb.footerSelector).count(); }

  async _sendOne(testCase) {
    await this._applyDialogOptions();
    const input = this.page.locator(this.wb.inputSelector).first();
    await input.click();
    await input.type(testCase.question, { delay: 12 });
    await this.page.waitForTimeout(300);
    await input.press('Enter');
  }

  // 等本轮完成：footer 数量到达 baseline+1（本轮 footer 出现即收口）。
  async _waitComplete(baselineFooterCount) {
    const timeout = this.execution.responseTimeout || 180000;
    try {
      await this.page.locator(this.wb.footerSelector).nth(baselineFooterCount).waitFor({ timeout });
      await this.page.waitForTimeout(1500); // 让答案/元信息渲染稳定
      return { completed: true, reason: 'footer' };
    } catch (e) { return { completed: false, reason: 'timeout' }; }
  }

  _buildResult(testCase, trace, meta) {
    const answerText = trace.answer || '';
    const completed = !!meta.completed;
    const incomplete = !meta.errorMsg && (!completed || looksIncomplete(answerText));
    const success = !meta.errorMsg && !incomplete && answerText.trim().length > 0;
    return {
      caseId: testCase.caseId, row: testCase.row, account: testCase.account || 'workbuddy',
      conversationId: testCase.conversationId, turnIndex: testCase.turnIndex, question: testCase.question,
      answer: meta.errorMsg ? `[执行失败] ${meta.errorMsg}` : success ? answerText : `[未完成:${meta.completeReason}]`,
      shareLink: null, artifactShareLink: (trace.artifacts[0] && trace.artifacts[0].share_link) || null,
      hasArtifact: trace.artifacts.length > 0,
      reportedDuration: trace.reported_duration || null, reportedDurationRaw: null,
      beanCost: trace.bean_cost || null, cost: null, costRaw: null,
      durationMs: (meta.endTime || Date.now()) - (meta.startTime || Date.now()),
      startTime: meta.startTime, endTime: meta.endTime,
      success, incomplete, completeReason: meta.completeReason || 'unknown',
      missingFields: [], reloadRecoveredFields: [],
    };
  }

  async runOne(testCase) {
    const startTime = Date.now();
    this.trace.reset();
    try {
      await this._openCleanConversation();
      const baseline = await this._footerCount();
      await this._sendOne(testCase);
      const done = await this._waitComplete(baseline);
      await this.trace.captureTurn(this.page);
      const trace = this.trace.buildTrace(testCase.run_id);
      return this._buildResult(testCase, trace, { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now() });
    } catch (e) {
      const msg = (e.message || '').split('\n')[0];
      return this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() });
    }
  }

  async runConversationTurns(turns, onTurnDone) {
    const sorted = turns.slice().sort((a, b) => (a.turnIndex || 0) - (b.turnIndex || 0));
    const results = [];
    let aborted = false, abortMsg = '';
    for (let i = 0; i < sorted.length; i++) {
      const testCase = sorted[i]; const startTime = Date.now();
      if (aborted) {
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'skipped', errorMsg: abortMsg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {}); continue;
      }
      try {
        this.trace.reset();
        if (i === 0) { await this._openCleanConversation(); }
        const baseline = await this._footerCount();
        await this._sendOne(testCase);                 // 后续轮不新建对话，在同一对话追加
        const done = await this._waitComplete(baseline);
        await this.trace.captureTurn(this.page);
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
      } catch (e) {
        const msg = (e.message || '').split('\n')[0];
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
        if (i === 0) { aborted = true; abortMsg = '首轮失败,多轮上下文未建立,后续轮跳过'; }
      }
    }
    return results;
  }
}
module.exports = { WorkbuddyRunner };
