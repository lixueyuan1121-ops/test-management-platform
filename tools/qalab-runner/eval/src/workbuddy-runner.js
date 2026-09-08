// src/workbuddy-runner.js
// WorkBuddy 对话驱动器：CDP 已连的 page 上开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+trace。
// 对外接口对齐 DesktopRunner（runOne/runConversationTurns/result 形状），使 bin/ai-eval.js 的 reportRun 无缝复用。
const { WorkbuddyDomTrace } = require('./workbuddy-dom-trace');
const { setClipboardFiles } = require('./clipboard-file');

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
      answer: w.answerSelector, messageContent: w.messageContentSelector, thinkingCollapse: w.thinkingCollapseSelector,
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
    // 附件(如有):必须先粘贴附件、确认「附件卡片挂上」再输 query,绝不「无附件裸发 query」——那样是
    // 对着没附件的回答评分,污染判定。WorkBuddy 附件走原生文件对话框(setInputFiles 不适用),真机坐实
    // 可行方案 = 系统剪贴板放 file-url + 聚焦编辑器 + CDP Meta+V 粘贴 → Slate 渲染成 file inline block。
    const atts = testCase.attachmentPaths || [];
    if (atts.length > 0) {
      await this._pasteAttachments(input, atts);   // 未挂上即抛错(本条判失败),不裸发
    }
    await input.click();
    await input.type(testCase.question, { delay: 12 });
    await this.page.waitForTimeout(300);
    await input.press('Enter');
  }

  // 把本地附件粘贴进输入框并确认挂上。链路(mac 真机坐实,~1s/附件;win 同构待真机验证):
  //   ① 系统剪贴板写文件(mac=NSPasteboard file-url / win=CF_HDROP,见 clipboard-file)② 聚焦编辑器
  //   ③ CDP page.keyboard 粘贴(mac=Meta+V / win=Control+V)④ 轮询等 file inline block
  //     ([data-content-block-meta-type=file]) 计数达期望且 blockStatus=completed
  // ⚠️ 必须 CDP 发键(page.keyboard),System Events 发键落不进 Electron 渲染进程(mac 真机验证过)。
  // 就绪判据取自真机 DOM:粘贴后编辑器出现 <span data-content-block-meta-type="file">,其 data-contentblock
  // JSON 的 _meta.blockStatus 由 uploading→completed。超时未就绪显式抛错(本条判失败,不裸发 query)。
  async _pasteAttachments(input, paths) {
    const sel = this.wb.attachmentPasteReadySelector || '[data-content-block-meta-type="file"]';
    const timeoutMs = this.execution.attachmentUploadTimeout || 60000;
    try {
      setClipboardFiles(paths);   // 一次性把全部附件写进剪贴板(mac writeObjects 多 URL / win SetFileDropList)
    } catch (e) {
      throw new Error(`附件写入剪贴板失败: ${(e.message || '').split('\n')[0]}`);
    }
    await input.click();          // 聚焦编辑器,光标落入
    await this.page.waitForTimeout(200);
    const pasteKey = process.platform === 'win32' ? 'Control+v' : 'Meta+v';   // win 用 Ctrl,mac 用 Cmd
    await this.page.keyboard.press(pasteKey);   // CDP 发键,直达渲染进程
    // 轮询等附件卡片达期望数量且全部 completed
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const st = await this._attachmentReadyState(sel).catch(() => ({ count: 0, allCompleted: false }));
      if (st.count >= paths.length && st.allCompleted) {
        this._log(`   附件已粘贴挂上(${st.count}/${paths.length},completed)`);
        return;
      }
      await this.page.waitForTimeout(500);
    }
    // 超时:附件未按期挂上 → 抛错(本条判失败),不裸发 query 污染判定
    const st = await this._attachmentReadyState(sel).catch(() => ({ count: 0, allCompleted: false }));
    throw new Error(`附件粘贴后未就绪(${(timeoutMs / 1000).toFixed(0)}s 后 ${st.count}/${paths.length} 卡片,completed=${st.allCompleted})`);
  }

  // 读附件 inline block 的就绪态:计数 + 是否全部 blockStatus=completed(uploading 期间不算就绪)。
  async _attachmentReadyState(sel) {
    return await this.page.evaluate((selector) => {
      const tags = Array.from(document.querySelectorAll(selector));
      let completed = 0;
      for (const t of tags) {
        // blockStatus 在祖先 [data-contentblock] 的 JSON 里
        const block = t.closest('[data-contentblock]') || t;
        let status = '';
        try { status = JSON.parse(block.getAttribute('data-contentblock'))._meta.blockStatus; } catch (_) {}
        if (status === 'completed') completed++;
      }
      return { count: tags.length, allCompleted: tags.length > 0 && completed === tags.length };
    }, sel);
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

  // 抓对话分享链接:点气泡"分享"按钮 → 分享面板"复制链接"渠道 → 读剪贴板(链接不在 DOM)。
  // 真机坐实:剪贴板形如「【WorkBuddy】<标题>\nhttps://workbuddy.link/p/xxx?ext2=copy_link」。
  // 失败(无分享按钮/剪贴板读不到)返回空串,不阻断流程(分享链接非硬性字段)。
  async _captureShareLink() {
    const btnSel = this.wb.shareBtnSelector;
    const chanSel = this.wb.shareChannelSelector;
    const copyText = this.wb.shareCopyLinkText || '复制链接';
    if (!btnSel || !chanSel) return '';
    try {
      // CDP attach 的页面默认无剪贴板读权限(readText 报 NotAllowedError:Read permission denied),
      // 而分享链接只在剪贴板(不进 DOM) → 先给本 context 授权(幂等,失败不阻断)。真机坐实:授权后 readText 正常。
      try { await this.page.context().grantPermissions(['clipboard-read', 'clipboard-write']); } catch (_) {}
      const share = this.page.locator(btnSel).last();
      if (!(await share.count())) return '';
      // 哨兵清剪贴板,便于确认"复制链接"确实写入(区分"没点到"与"链接是旧值")
      await this.page.evaluate(() => navigator.clipboard.writeText('__WB_SHARE_SENTINEL__')).catch(() => {});
      await share.click({ timeout: 5000 }).catch(() => {});
      await this.page.waitForTimeout(1000);
      const copyBtn = this.page.locator(chanSel, { hasText: copyText }).first();
      if (!(await copyBtn.count())) { await this.page.keyboard.press('Escape').catch(() => {}); return ''; }
      await copyBtn.click({ timeout: 5000, force: true }).catch(() => {});
      // 轮询剪贴板(复制异步,最多 8s)
      let clip = '__WB_SHARE_SENTINEL__';
      for (let i = 0; i < 16; i++) {
        await this.page.waitForTimeout(500);
        try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch (_) {}
        if (clip && clip !== '__WB_SHARE_SENTINEL__') break;
      }
      await this.page.keyboard.press('Escape').catch(() => {});  // 关分享面板
      if (!clip || clip === '__WB_SHARE_SENTINEL__') { this._warn('   对话分享链接:点复制链接后剪贴板未更新'); return ''; }
      const m = String(clip).match(/https?:\/\/[^\s]+/);
      return m ? m[0] : '';
    } catch (e) { this._warn(`   抓对话分享链接失败: ${(e.message || '').split('\n')[0]}`); return ''; }
  }

  // 抓「复制 message」原始结构化 JSON:点消息气泡「更多操作」→ 菜单「复制 message」→ 读剪贴板。
  // 真机坐实(2026-09-08):复制出完整 JSON(requestId/traceId/conversationId/思维链 reasoning/modelId/时间戳),
  // 价值超过 answer(仅正文),存 EvalRun.raw_message 供后续分析。失败(无按钮/剪贴板未更新)返回空串,不阻断流程。
  // ⚠️ 菜单靠真实指针:先 hover「更多操作」再 click(纯 click 时有时不弹,同附件级联菜单经验)。
  async _captureRawMessage() {
    const moreSel = this.wb.moreActionSelector;
    const itemSel = this.wb.copyMessageItemSelector;
    const itemText = this.wb.copyMessageText || '复制 message';
    if (!moreSel || !itemSel) return '';
    try {
      try { await this.page.context().grantPermissions(['clipboard-read', 'clipboard-write']); } catch (_) {}
      const more = this.page.locator(moreSel).last();   // 最后一条消息的更多操作
      if (!(await more.count())) return '';
      await this.page.evaluate(() => navigator.clipboard.writeText('__WB_MSG_SENTINEL__')).catch(() => {});
      await more.hover().catch(() => {});                // 先 hover(级联菜单靠真实指针)
      await this.page.waitForTimeout(300);
      await more.click({ timeout: 5000 }).catch(() => {});
      await this.page.waitForTimeout(800);
      const item = this.page.locator(itemSel, { hasText: itemText }).first();
      if (!(await item.count())) { await this.page.keyboard.press('Escape').catch(() => {}); return ''; }
      await item.click({ timeout: 5000 }).catch(() => {});
      let clip = '__WB_MSG_SENTINEL__';
      for (let i = 0; i < 16; i++) {
        await this.page.waitForTimeout(500);
        try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch (_) {}
        if (clip && clip !== '__WB_MSG_SENTINEL__') break;
      }
      await this.page.keyboard.press('Escape').catch(() => {});
      if (!clip || clip === '__WB_MSG_SENTINEL__') { this._warn('   复制 message:点后剪贴板未更新'); return ''; }
      return String(clip);
    } catch (e) { this._warn(`   抓复制 message 失败: ${(e.message || '').split('\n')[0]}`); return ''; }
  }

  _buildResult(testCase, trace, meta) {
    const answerText = trace.answer || '';
    const completed = !!meta.completed;
    const incomplete = !meta.errorMsg && (!completed || looksIncomplete(answerText));
    const success = !meta.errorMsg && !incomplete && answerText.trim().length > 0;
    // 耗时抓取证据(便于真机复验长会话回填):打印解析值 + 原文,一眼看出命中哪种格式/是否落空。
    if (trace.reported_duration) this._log(`   耗时抓取: ${trace.reported_duration}s (原文「${trace.reported_duration_raw || ''}」)`);
    else this._warn(`   耗时未抓到: 原文「${trace.reported_duration_raw || '(空)'}」——若非空请核对格式,贴给维护者补规则`);
    return {
      caseId: testCase.caseId, row: testCase.row, account: testCase.account || 'workbuddy',
      conversationId: testCase.conversationId, turnIndex: testCase.turnIndex, question: testCase.question,
      answer: meta.errorMsg ? `[执行失败] ${meta.errorMsg}` : success ? answerText : `[未完成:${meta.completeReason}]`,
      rawMessage: meta.rawMessage || null,
      shareLink: trace.share_link || null, artifactShareLink: (trace.artifacts[0] && trace.artifacts[0].share_link) || null,
      hasArtifact: trace.artifacts.length > 0,
      reportedDuration: trace.reported_duration || null, reportedDurationRaw: trace.reported_duration_raw || null,
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
      const rawMessage = await this._captureRawMessage();        // 抓「复制 message」原始 JSON(点更多操作→复制 message→剪贴板)
      this.trace.setShareLink(await this._captureShareLink());   // 抓对话分享链接(点分享→复制链接→剪贴板)
      const trace = this.trace.buildTrace(testCase.run_id);
      return this._buildResult(testCase, trace, { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now(), rawMessage });
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
        const rawMessage = await this._captureRawMessage();        // 抓「复制 message」原始 JSON
        this.trace.setShareLink(await this._captureShareLink());   // 抓对话分享链接
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now(), rawMessage });
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
