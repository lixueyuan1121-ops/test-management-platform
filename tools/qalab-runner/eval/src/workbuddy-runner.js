const { readSelection, verifySelection, configError, normalize } = require('./dialog-config');
// src/workbuddy-runner.js
// WorkBuddy 对话驱动器：CDP 已连的 page 上开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+trace。
// 对外接口对齐 DesktopRunner（runOne/runConversationTurns/result 形状），使 bin/ai-eval.js 的 reportRun 无缝复用。
const { WorkbuddyDomTrace } = require('./workbuddy-dom-trace');
const { setClipboardFiles } = require('./clipboard-file');
const { randomUUID } = require('crypto');

function workbuddyShareUrl(text) {
  for (const candidate of String(text || '').match(/https?:\/\/[^\s"'<>]+/g) || []) {
    try {
      const url = new URL(candidate);
      if (url.hostname === 'workbuddy.link' && /^\/p\/[^/]+/.test(url.pathname)) return url.href;
    } catch (_) {}
  }
  return '';
}

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
    if (!(await this._dismissShareUi())) throw new Error('分享底栏未关闭，无法新建任务');
    const nt = this.page.locator(this.wb.newTaskSelector).first();
    await nt.click({ timeout: 10000 });  // 点击失败必须报错，不能把下一题发进上一会话
    await this.page.waitForTimeout(1200);
    await this.page.locator(this.wb.inputSelector).first().waitFor({ state: 'visible', timeout: 15000 });
    return true;
  }

  // 选模型档：点开 cr-model-selector__trigger，选名字匹配 dialogOptions.model 的 item。未指定则不动。
  async _applyDialogOptions() {
    const options = this.execution.dialogOptions || {};
    this.executionConfig = { schema_version: 1, requested: { ...options }, observed: {}, status: 'checking' };
    try {
      if (options.chatMode || options.thinkingDepth) throw configError('WorkBuddy 暂不支持指定对话模式或思考深度');
      const trigger = this.page.locator(this.wb.modelTriggerSelector).first();
      if (options.model) {
        await trigger.click({ timeout: 4000 });
        await this.page.waitForTimeout(600);
        const opts = this.page.locator(this.wb.modelOptionSelector);
        let selected = null;
        for (let i = 0; i < await opts.count(); i++) {
          const option = opts.nth(i);
          if (normalize(await option.innerText()) === normalize(options.model)) { selected = option; break; }
        }
        if (!selected) throw configError(`找不到模型「${options.model}」`);
        await selected.click({ timeout: 4000 });
        await this.page.keyboard.press('Escape').catch(() => {});
        this.executionConfig.observed.model = await verifySelection(this.page, trigger, options.model);
      } else this.executionConfig.observed.model = await readSelection(trigger).catch(() => []);
      this.executionConfig.status = options.model ? 'verified' : 'observed';
    } catch (error) {
      const failure = error.message?.startsWith('[CONFIG_ERROR]') ? error : configError(error.message);
      this.executionConfig.status = 'config_error';
      this.executionConfig.error = failure.message;
      await this.page.keyboard.press('Escape').catch(() => {});
      throw failure;
    }
  }

  async _footerCount() { return await this.page.locator(this.wb.footerSelector).count(); }

  async _sendOne(testCase) {
    // 多轮对话不会经过 _openCleanConversation，发送前也要清理上轮分享态。
    if (!(await this._dismissShareUi())) throw new Error('分享底栏未关闭，无法发送下一轮');
    if (testCase.dialogOptions) this.execution.dialogOptions = testCase.dialogOptions;
    try { await this._applyDialogOptions(); }
    finally { testCase.executionConfig = structuredClone(this.executionConfig || null); }
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

  _questionCard() {
    const sel = this.wb.questionSelector || '[class*="_questionFloating_"]:not([class*="_confirmVariant_"])';
    return this.page.locator(`:is(${sel}):visible`).first();
  }

  // 只处理反问卡片，不在整页搜索“继续/允许”等按钮。保留预选答案；无预选时选第一项。
  // 5.5.6 单选中间题没有提交按钮，点击当前选项会自动翻页；末题/多选需点发送或下一题。
  async _handleQuestion() {
    const card = this._questionCard();
    if (!(await card.count())) return false;
    try {
      const options = card.locator(this.wb.questionOptionSelector || '[class*="_optionItem_"]');
      let choice = null;
      for (let i = 0; i < await options.count(); i++) {
        const option = options.nth(i);
        if (!(await option.isVisible()) || !(await option.isEnabled())) continue;
        const chosen = await option.evaluate(el => el.getAttribute('aria-checked') === 'true'
          || el.getAttribute('aria-selected') === 'true' || /(?:^|\s)_selected_/.test(el.className)
          || !!el.querySelector('[aria-checked="true"], input:checked'));
        if (chosen) { choice = option; break; }
      }
      const hadSelection = !!choice;
      if (!choice) {
        for (let i = 0; i < await options.count(); i++) {
          const option = options.nth(i);
          if (await option.isVisible() && await option.isEnabled()) { choice = option; break; }
        }
      }
      // 纯自由输入题没有默认答案，保持待回答，不能编造答案或误判完成。
      if (!choice) return true;
      const advance = card.getByRole('button', { name: /^(继续|下一步|下一题|发送|提交|完成|Continue|Next|Next question|Send|Submit|Done)$/i });
      if (hadSelection) {
        for (let i = 0; i < await advance.count(); i++) {
          const btn = advance.nth(i);
          if (await btn.isVisible() && await btn.isEnabled()) {
            await btn.click({ timeout: 2000 });
            this._log('   反问：保留默认选项并继续');
            return true;
          }
        }
        if (await advance.count()) return true;  // 按钮暂禁用时等待，不能反选已勾选的多选项
      }
      await choice.click({ timeout: 2000 });
      // 本次仅做一次动作。单选可能已翻页/提交，多选在下一轮点击继续，避免误点下一题。
      this._log('   反问：已选择默认项，等待继续/下一题');
    } catch (e) { this._warn(`   反问交互待重试: ${(e.message || '').split('\n')[0]}`); }
    return true;  // 卡片存在就不能按完成处理，包括按钮暂不可点的情况
  }

  // 工作流/计划执行确认不是普通反问：已选的“开始执行”本身就是提交动作。
  // 底部“发送”用于调整计划，不应被通用按钮文案匹配误点。
  async _handleWorkflow() {
    const sel = this.wb.workflowSelector || '.exit-plan-mode-floating, .conversation-exit-plan-panel, .pending-plan-panel';
    const optionSel = this.wb.workflowOptionSelector || '.exit-plan-mode-floating__option, .conversation-exit-plan-panel__option, .pending-plan-panel__option';
    const resolvedSel = this.wb.workflowResolvedSelector || '.exit-plan-mode-floating__decision--approved, .exit-plan-mode-floating__decision--keep-planning, .conversation-exit-plan-panel__decision--approved, .conversation-exit-plan-panel__decision--adjusted';
    const cards = this.page.locator(`:is(${sel}):visible`);
    for (let i = 0; i < await cards.count(); i++) {
      const card = cards.nth(i);
      try {
        // 已批准的结果面板可能仍可见；跳过它，继续查找后续待确认面板。
        if (await card.locator(`:is(${resolvedSel}):visible`).count()) continue;
        const options = card.locator(`:is(${optionSel}):visible`);
        const count = await options.count();
        if (!count) return true;  // 加载/状态转换期间不把旧 footer 当成任务完成
        let choice = options.first();
        for (let j = 0; j < count; j++) {
          const option = options.nth(j);
          if (await option.evaluate(el => el.getAttribute('aria-checked') === 'true'
            || el.getAttribute('aria-selected') === 'true' || /__option--selected(?:\s|$)/.test(el.className))) {
            choice = option; break;
          }
        }
        // 旧版仅用 tabindex=-1 表示正在提交；新版还设置 aria-disabled。
        if (!(await choice.isEnabled()) || await choice.getAttribute('tabindex') === '-1') return true;
        await choice.click({ timeout: 2000 });
        this._log('   工作流确认：已选择默认执行项，等待任务继续');
      } catch (e) { this._warn(`   工作流确认待重试: ${(e.message || '').split('\n')[0]}`); }
      return true;
    }
    return false;
  }

  // 持续处理反问及工作流确认；本轮 footer 稳定出现且没有待处理交互后才收口。
  async _waitComplete(baselineFooterCount) {
    const timeout = this.execution.responseTimeout || 180000;
    const deadline = Date.now() + timeout;
    let stableSince = 0;
    while (Date.now() < deadline) {
      if (await this._handleQuestion() || await this._handleWorkflow()) stableSince = 0;
      else if (await this.page.locator(this.wb.footerSelector).nth(baselineFooterCount).isVisible()) {
        if (!stableSince) stableSince = Date.now();
        if (Date.now() - stableSince >= 1500) return { completed: true, reason: 'footer' };
      } else stableSince = 0;
      await this.page.waitForTimeout(Math.min(500, Math.max(0, deadline - Date.now())));
    }
    return { completed: false, reason: 'timeout' };
  }

  async _dismissShareUi() {
    const barSel = this.wb.shareBarSelector || '.wb-share-bar__inner';
    const bar = this.page.locator(`:is(${barSel}):visible`).first();
    for (let i = 0; i < 2; i++) {
      if (!(await bar.count())) return true;
      try {
        const close = bar.locator(this.wb.shareCloseSelector || 'button[aria-label="退出分享"]');
        if (await close.count()) await close.first().click({ timeout: 2000 });
        else await this.page.keyboard.press('Escape');
        await bar.waitFor({ state: 'hidden', timeout: 2000 });
      } catch (_) { await this.page.keyboard.press('Escape').catch(() => {}); }
    }
    const closed = !(await bar.count());
    if (!closed) this._warn('   分享底栏仍可见，下一轮发送前将重试关闭');
    return closed;
  }

  // 分享入口可能只预选部分消息，甚至没有选择；复制按钮仍可点击，不能据此认定可分享。
  async _ensureShareAllSelected(bar) {
    const selector = this.wb.shareSelectAllSelector || '.wb-share-bar__left [role="checkbox"]';
    const timeout = this.wb.shareClickTimeout || 5000;
    const all = bar.locator(`:is(${selector}):visible`).first();
    try {
      await all.waitFor({ state: 'visible', timeout });
      if (await all.getAttribute('aria-checked') === 'true') return;  // 已全选时不能再点成取消全选
      await all.click({ timeout });
      // 点击完成不代表选择已生效；等客户端确认全选后才能复制，不反复点击切换状态。
      await bar.locator(`:is(${selector})[aria-checked="true"]:visible`).first().waitFor({ state: 'visible', timeout });
      this._log('   分享：已确认全选对话内容');
    } catch (e) {
      throw new Error(`分享全选未确认: ${(e.message || '').split('\n')[0]}`);
    }
  }

  // 抓对话分享链接:点气泡"分享" → 确认全选 → "复制链接" → 读剪贴板(链接不在 DOM)。
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
      for (let attempt = 0; attempt < 2; attempt++) {
        if (!(await this._dismissShareUi())) return '';
        try {
          const share = this.page.locator(`:is(${btnSel}):visible`).last();
          const clickTimeout = this.wb.shareClickTimeout || 5000;
          await share.waitFor({ state: 'visible', timeout: clickTimeout });
          // 写入必须成功，否则无法区分旧链接。每次尝试独立哨兵，拒收无关剪贴板内容。
          const sentinel = `__WB_SHARE_${randomUUID()}__`;
          await this.page.evaluate(value => navigator.clipboard.writeText(value), sentinel);
          await share.click({ timeout: clickTimeout });
          const bar = this.page.locator(`:is(${this.wb.shareBarSelector || '.wb-share-bar__inner'}):visible`).first();
          await bar.waitFor({ state: 'visible', timeout: clickTimeout });
          await this._ensureShareAllSelected(bar);
          const copyId = bar.locator(this.wb.shareCopySelector || '[data-track-id="share_copy_link"]');
          const copy = await copyId.count() ? copyId.first()
            : bar.locator(`:is(${chanSel}):visible`).filter({ hasText: copyText }).first();
          await copy.click({ timeout: clickTimeout });  // 等可见、稳定、可点击；不 force、不吞点击失败
          const deadline = Date.now() + (this.wb.shareCopyTimeout || 15000);
          while (Date.now() < deadline) {
            const clip = await this.page.evaluate(() => navigator.clipboard.readText());
            const url = clip !== sentinel && workbuddyShareUrl(clip);
            if (url) return url;
            await this.page.waitForTimeout(250);
          }
          throw new Error('点复制链接后未获得本次分享 URL');
        } catch (e) { this._warn(`   对话分享链接第 ${attempt + 1} 次失败: ${(e.message || '').split('\n')[0]}`); }
      }
      return '';
    } catch (e) { this._warn(`   抓对话分享链接失败: ${(e.message || '').split('\n')[0]}`); return ''; }
    finally { await this._dismissShareUi(); }  // 成功、点击失败、复制超时均退出分享模式
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
      executionConfig: testCase.executionConfig || null,
      errorCode: meta.errorMsg?.startsWith('[CONFIG_ERROR]') ? 'CONFIG_ERROR' : null,
      errorMessage: meta.errorMsg || null,
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
      const rawMessage = done.completed ? await this._captureRawMessage() : '';
      if (done.completed) this.trace.setShareLink(await this._captureShareLink());
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
        const rawMessage = done.completed ? await this._captureRawMessage() : '';
        if (done.completed) this.trace.setShareLink(await this._captureShareLink());
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now(), rawMessage });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
        if (!done.completed) { aborted = true; abortMsg = '上一轮未完成，后续轮跳过，避免在反问或生成中追加任务'; }
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
