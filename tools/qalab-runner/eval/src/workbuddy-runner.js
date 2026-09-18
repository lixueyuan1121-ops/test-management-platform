const { readSelection, verifySelection, configError, normalize } = require('./dialog-config');
// src/workbuddy-runner.js
// WorkBuddy 对话驱动器：CDP 已连的 page 上开新对话、输入(contenteditable)、选模型档、发送、等完成、抓答案+trace。
// 对外接口对齐 DesktopRunner（runOne/runConversationTurns/result 形状），使 bin/ai-eval.js 的 reportRun 无缝复用。
const { WorkbuddyDomTrace } = require('./workbuddy-dom-trace');
const { setClipboardFiles } = require('./clipboard-file');
const { randomUUID } = require('crypto');
const { parseWorkbuddyMessage } = require('./workbuddy-message');

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

// 按已适配的权限类型选单次授权；InterceptCard 的同名“允许”在不同权限中含义不同。
const INTERCEPT_APPROVALS = [
  {
    name: '批量删除确认',
    title: /^(检测到批量删除操作|檢測到批次刪除操作|Detected bulk delete operation)/i,
    allow: /^(允许本次删除|允許本次刪除|Allow this delete)$/i,
    deny: /^(取消删除|取消刪除|Cancel delete)$/i,
  },
  {
    name: '受保护文件修改确认',
    title: /^(检测到受保护文件修改|檢測到受保護檔案修改|Detected modification to a protected file)$/i,
    allow: /^(允许|允許|Allow)$/i,
    deny: /^(拒绝|拒絕|Deny, keep running in the sandbox)$/i,
  },
];

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
    const timeout = this.wb.newTaskTimeout || 5000;
    let failure;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (!(await this._dismissShareUi())) throw new Error('分享底栏未关闭');
        await this.page.keyboard.press('Escape'); // 收起上轮复制菜单，不点击授权/确认弹窗。
        const stable = this.page.locator('button[data-track-id="agent_new_task_button_clicked"]:visible');
        let button = stable.first();
        // 保留旧客户端/自定义选择器兼容，但绝不选择隐藏的第一个匹配项。
        if (!(await stable.count()) && this.wb.newTaskSelector) {
          const candidates = this.page.locator(this.wb.newTaskSelector);
          for (let i = 0; i < await candidates.count(); i++) {
            if (await candidates.nth(i).isVisible()) { button = candidates.nth(i); break; }
          }
        }
        await button.click({ timeout });
        await this.page.waitForFunction(({ input, home, footer, messages }) => {
          const visible = el => el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
          const editor = [...document.querySelectorAll(input)].find(visible);
          if (!editor || ![...document.querySelectorAll(home)].some(visible)) return false;
          if ([...document.querySelectorAll(`${footer}, ${messages}`)].some(visible)) return false;
          const copy = editor.cloneNode(true);
          copy.querySelectorAll('[data-slate-placeholder]').forEach(el => el.remove());
          return !copy.textContent.replace(/[\s\u200b\ufeff]/g, '') && !copy.querySelector('[data-content-block-meta-type="file"]');
        }, { input: this.wb.inputSelector, home: this.wb.newTaskReadySelector || '.wb-home-page',
          footer: this.wb.footerSelector, messages: '[data-message-request-id], [id^="user-message-"]' }, { timeout });
        return true;
      } catch (error) {
        failure = error;
        this._warn(`   WorkBuddy 新建任务第 ${attempt + 1} 次失败：${error.message}`);
      }
    }
    // 只在发送前重试导航；失败不能继续输入，更不能把题目发进上一条任务。
    throw new Error(`[WORKBUDDY_NEW_TASK] 无法确认空白会话：${failure.message}`);
  }

  // WorkBuddy 5.5.6 的整行文本还含优惠标签和积分倍率，只比较名称节点。
  // 列表中的屏外项已挂载；定位后由 Playwright 滚入列表视区，无需逐屏猜测位置。
  async _findModelOption(wanted) {
    const target = normalize(wanted);
    if (!target) throw configError('模型名称不能为空');
    const options = this.page.locator(this.wb.modelOptionSelector);
    const deadline = Date.now() + 4000;
    let available = [];
    do {
      available = await options.evaluateAll((items, nameSelector) => items.flatMap((item, index) => {
        if (!item.getClientRects().length || getComputedStyle(item).visibility === 'hidden'
          || item.getAttribute('role') === 'menuitem') return [];
        // 兼容旧配置仍指向 item-info（其中包含徽标），优先读取真实名称叶节点。
        const label = item.querySelector('.cr-model-selector__item-name')
          || (nameSelector && item.querySelector(nameSelector)) || item;
        return [{ index, name: label.innerText.trim(),
          disabled: item.matches(':disabled, [aria-disabled="true"], .cr-model-selector__item--disabled') }];
      }), this.wb.modelOptionNameSelector);
      const matches = available.filter(item => normalize(item.name) === target);
      if (matches.length > 1) throw configError(`模型列表存在多个同名模型「${wanted}」，无法确认选择`);
      if (matches.length === 1) {
        if (matches[0].disabled) throw configError(`模型「${matches[0].name}」在当前列表中不可选，请检查账号权限或客户端状态`);
        return options.nth(matches[0].index);
      }
      // 等待下拉动画及异步加载完成，避免固定等待后只查一次造成误报。
      await this.page.waitForTimeout(100);
    } while (Date.now() < deadline);
    const names = [...new Set(available.map(item => item.name).filter(Boolean))];
    throw configError(names.length
      ? `找不到模型「${wanted}」。当前模型列表：${names.slice(0, 30).join('、')}${names.length > 30 ? '…' : ''}（名称匹配不区分大小写）`
      : `找不到模型「${wanted}」：模型列表未加载或没有可见选项，请检查客户端状态`);
  }

  async _modelTriggers() {
    // 首页/历史任务切换时旧输入区可能仍挂载。与真实编辑器同属的可见 input-container 优先，
    // 兼容旧客户端及自定义选择器没有该容器的情况；不再对全页直接取第一个隐藏按钮。
    const editor = this.wb.inputSelector || '[contenteditable="true"][role="textbox"]';
    const containers = this.page.locator('.cr-input-container:visible')
      .filter({ has: this.page.locator(`:is(${editor}):visible`) });
    const scope = await containers.count() ? containers : this.page;
    const primary = scope.locator(`:is(${this.wb.modelTriggerSelector || 'button.cr-model-selector__trigger'}):visible`);
    if (await primary.count()) return primary;
    // 5.5.6 实测的语义属性，不依赖样式前缀；只匹配模型 combobox，不点普通“确认”按钮。
    return scope.locator('button[aria-label="Select model"][aria-haspopup="listbox"]:visible');
  }

  async _modelControlDiagnostic() {
    const snapshot = { matches: 0, visible: 0, enabled: 0, visibleEditors: 0, homeVisible: false };
    try {
      snapshot.matches = await this.page.locator(this.wb.modelTriggerSelector || 'button.cr-model-selector__trigger').count();
      const candidates = await this._modelTriggers();
      snapshot.visible = await candidates.count();
      snapshot.enabled = await candidates.evaluateAll(es => es.filter(el => !el.matches(':disabled, [aria-disabled="true"]')).length);
      snapshot.visibleEditors = await this.page.locator(`:is(${this.wb.inputSelector || '[contenteditable="true"][role="textbox"]'}):visible`).count();
      snapshot.homeVisible = await this.page.locator(`:is(${this.wb.newTaskReadySelector || '.wb-home-page'}):visible`).count() > 0;
    } catch (_) { /* 页面销毁时也保留已读到的诊断，不输出对话正文。 */ }
    return snapshot;
  }

  async _waitForModelTrigger({ open = false, deadline } = {}) {
    const started = Date.now();
    deadline ??= started + (this.wb.modelReadyTimeoutMs ?? 30000);
    let waitingLogged = false, lastError = '';
    do {
      try {
        const candidates = await this._modelTriggers();
        // 同时出现多个可见入口时等旧区域卸载，不能猜哪一个属于本条任务。
        if (await candidates.count() === 1 && await candidates.isEnabled() &&
            await candidates.getAttribute('aria-disabled') !== 'true') {
          if (open && await candidates.getAttribute('aria-expanded') !== 'true') {
            // 短 actionability 尝试 + 重新定位，兼容 React 卸载/替换按钮；共用总预算，且只重试打开列表。
            await candidates.click({ timeout: Math.max(1, Math.min(1500, deadline - Date.now())) });
          }
          if (waitingLogged) this._log(`   WorkBuddy 模型入口已就绪（等待 ${((Date.now() - started) / 1000).toFixed(1)}s）`);
          return candidates;
        }
      } catch (error) {
        if (this.page.isClosed()) break;
        lastError = (error.message || '').split('\n')[0];
      }
      if (!waitingLogged && Date.now() - started >= 1000) {
        this._log('   WorkBuddy 正在等待当前输入区的模型入口加载并可用，尚未发送题目');
        waitingLogged = true;
      }
      if (Date.now() < deadline) await this.page.waitForTimeout(Math.min(100, deadline - Date.now()));
    } while (Date.now() < deadline);
    const diagnostic = await this._modelControlDiagnostic();
    if (this.executionConfig) this.executionConfig.modelControl = diagnostic;
    throw new Error(`[WORKBUDDY_MODEL_NOT_READY] 模型入口未就绪（可见 ${diagnostic.visible}，可用 ${diagnostic.enabled}，` +
      `输入框 ${diagnostic.visibleEditors}，首页 ${diagnostic.homeVisible ? '已显示' : '未显示'}）；` +
      `可能仍在加载模型数据、控件被禁用或页面尚未切换完成，未发送题目${lastError ? `；${lastError}` : ''}`);
  }

  // 选模型档并回读验证，英文大小写/空白归一化，但不同版本或后缀不互相替代。
  async _applyDialogOptions() {
    const options = this.execution.dialogOptions || {};
    this.executionConfig = { schema_version: 1, requested: { ...options }, observed: {}, status: 'checking' };
    try {
      if (options.chatMode || options.thinkingDepth) throw configError('WorkBuddy 暂不支持指定对话模式或思考深度');
      if (options.model) {
        const deadline = Date.now() + (this.wb.modelReadyTimeoutMs ?? 30000);
        await this._waitForModelTrigger({ open: true, deadline });
        const selected = await this._findModelOption(options.model);
        await selected.scrollIntoViewIfNeeded({ timeout: 4000 });
        await selected.click({ timeout: 4000 });
        await this.page.keyboard.press('Escape').catch(() => {});
        const trigger = await this._waitForModelTrigger({ deadline });
        this.executionConfig.observed.model = await verifySelection(this.page, trigger, options.model);
      } else {
        const triggers = await this._modelTriggers();
        this.executionConfig.observed.model = await triggers.count() === 1 ? await readSelection(triggers).catch(() => []) : [];
      }
      this.executionConfig.status = options.model ? 'verified' : 'observed';
    } catch (error) {
      const uiNotReady = error.message?.startsWith('[WORKBUDDY_MODEL_NOT_READY]');
      const failure = uiNotReady || error.message?.startsWith('[CONFIG_ERROR]') ? error : configError(error.message);
      this.executionConfig.status = uiNotReady ? 'ui_not_ready' : 'config_error';
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
    const input = this.page.locator(`:is(${this.wb.inputSelector}):visible`).first();
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

  async _clearInterceptApproval() {
    await this._interceptApproval?.element?.dispose().catch(() => {});
    this._interceptApproval = null;
  }

  async _clearVideoApproval() {
    await this._videoApproval?.element?.dispose().catch(() => {});
    this._videoApproval = null;
  }

  // 5.5.6 的视频积分确认有两套实现：div[role=button] 用 tabindex=-1 表示提交中，
  // conversation 面板用原生 disabled。选中样式仅表示 hover，不能据此跳过“确认”。
  async _handleVideoApproval() {
    const selector = this.wb.videoApprovalSelector || '.high-credit-approval-floating, .conversation-high-credit-approval';
    const resolved = '.high-credit-approval-floating__decision, .conversation-high-credit-approval__decision';
    const card = this.page.locator(`:is(${selector}):visible:not(:has(${resolved}))`).first();
    if (!await card.count()) { await this._clearVideoApproval(); return false; }
    const element = await card.elementHandle({ timeout: 300 }).catch(() => null);
    if (!element) return true;
    const optionSelector = '.high-credit-approval-floating__option, .conversation-high-credit-approval__option';
    const labelSelector = '.high-credit-approval-floating__option-text, .conversation-high-credit-approval__option-text';
    let options = [];
    try {
      const snapshot = await element.evaluate((el, { optionSelector, labelSelector }) => ({
        connected: el.isConnected,
        title: (el.querySelector('.high-credit-approval-floating__title, .conversation-high-credit-approval__title')?.textContent || '').trim(),
        labels: [...el.querySelectorAll(optionSelector)].map(option => option.getClientRects().length
          ? (option.querySelector(labelSelector)?.textContent || '').trim() : ''),
      }), { optionSelector, labelSelector });
      if (!snapshot.connected || !/^(确认开始生成视频|確認開始生成影片|Confirm video generation)[?？]?$/i.test(snapshot.title)) return true;
      const confirmIndex = snapshot.labels.findIndex(label => /^(确认|確認|Confirm)$/i.test(label));
      if (confirmIndex < 0 || !snapshot.labels.some(label => /^(拒绝|拒絕|Reject)$/i.test(label))) return true;
      const signature = JSON.stringify([snapshot.title, snapshot.labels]);
      const previous = this._videoApproval;
      const sameCard = previous && previous.signature === signature
        && await element.evaluate((el, old) => el === old, previous.element);
      if (!sameCard) {
        await this._clearVideoApproval();
        this._videoApproval = { element, signature, attempts: 0, clickedAt: 0 };
      }
      const state = this._videoApproval;
      options = await element.$$(optionSelector);
      const confirm = options[confirmIndex];
      // 固定当前按钮并一次读就绪状态；卡片随时可能卸载，不用会跳到下一张卡片的 locator。
      const ready = confirm && await confirm.evaluate((el, labelSelector) => ({
        enabled: el.isConnected && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'
          && !el.matches(':disabled, [aria-disabled="true"], [aria-busy="true"], [tabindex="-1"]'),
        label: (el.querySelector(labelSelector)?.textContent || '').trim(),
      }), labelSelector);
      if (!ready?.enabled || ready.label !== snapshot.labels[confirmIndex]) return true;
      // 失败时两种组件都会恢复可操作，仍显示 pending；成功则替换为 decision 或移除面板。
      if (state.attempts >= (this.wb.videoApprovalMaxAttempts ?? 3)) {
        throw new Error('[WORKBUDDY_CONFIRM_FAILED] 视频生成确认提交多次失败，任务未继续');
      }
      if (state.attempts && Date.now() - state.clickedAt < (this.wb.videoApprovalRetryMs ?? 1000)) return true;
      state.attempts++; state.clickedAt = Date.now();
      try { await confirm.click({ timeout: 2000 }); }
      catch (error) {
        if (!await element.evaluate(el => el.isConnected)) return true;
        throw new Error(`[WORKBUDDY_CONFIRM_FAILED] 视频生成确认点击未确认：${error.message.split('\n')[0]}`);
      }
      this._log(`   WorkBuddy 视频生成确认：已点击“${snapshot.labels[confirmIndex]}”（第 ${state.attempts} 次提交），等待视频生成`);
      return true;
    } finally {
      for (const option of options) await option.dispose().catch(() => {});
      if (this._videoApproval?.element !== element) await element.dispose().catch(() => {});
    }
  }

  // WorkBuddy InterceptCard：批量删除、受保护文件修改。按卡片标题和明确的单次授权项匹配，
  // 不复用反问的“默认第一项”规则；同组件也承载其他权限类型。
  async _handleIntercept() {
    const selector = this.wb.interceptCardSelector || '[class*="_container_"]:has(> [class*="_optionList_"] > button[class*="_optionItem_"])';
    const card = this.page.locator(`:is(${selector}):visible`).first();
    if (!await card.count()) { await this._clearInterceptApproval(); return false; }
    // 固定当前节点并一次读取快照。提交后卡片可能随时卸载，不能让 locator 等旧节点重现或跳到下一张。
    const element = await card.elementHandle({ timeout: 300 }).catch(() => null);
    if (!element) return true;
    let buttons = [];
    try {
      const optionSelector = this.wb.interceptOptionSelector || '[class*="_optionList_"] > button[class*="_optionItem_"]';
      const snapshot = await element.evaluate((el, sel) => ({
        connected: el.isConnected,
        title: el.querySelector('[class*="_header_"] [class*="_title_"]')?.textContent.trim() || '',
        command: el.querySelector('[class*="_commandInline_"]')?.textContent || '',
        labels: [...el.querySelectorAll(sel)].map(button => button.getClientRects().length
          ? button.querySelector('[class*="_optionLabel_"]')?.textContent.trim() || '' : ''),
      }), optionSelector);
      const approval = INTERCEPT_APPROVALS.find(rule => rule.title.test(snapshot.title));
      if (!snapshot.connected || !approval) return true;
      const allowIndex = snapshot.labels.findIndex(label => approval.allow.test(label));
      if (allowIndex < 0 || !snapshot.labels.some(label => approval.deny.test(label))) return true;
      const allowLabel = snapshot.labels[allowIndex];
      const signature = JSON.stringify([snapshot.title, snapshot.command, snapshot.labels]);
      const previous = this._interceptApproval;
      const sameCard = previous && previous.signature === signature
        && await element.evaluate((el, old) => el === old, previous.element);
      if (!sameCard) {
        await this._clearInterceptApproval();
        this._interceptApproval = { element, signature, attempts: 0, clickedAt: 0 };
      }
      const state = this._interceptApproval;
      buttons = await element.$$(optionSelector);
      const allow = buttons[allowIndex];
      if (!allow || !await allow.isVisible() || !await allow.isEnabled() || await allow.getAttribute('aria-busy') === 'true') return true;
      // 客户端提交期间禁用按钮；提交失败时清掉 optionSelected。只有明确恢复失败态才有限重试。
      if (state.attempts && /(?:^|\s)_optionSelected_/.test(await allow.getAttribute('class') || '')) return true;
      if (state.attempts >= (this.wb.interceptMaxAttempts ?? 3)) {
        throw new Error(`[WORKBUDDY_CONFIRM_FAILED] ${approval.name}提交多次失败，任务未继续`);
      }
      if (state.attempts && Date.now() - state.clickedAt < (this.wb.interceptRetryMs ?? 1000)) return true;
      state.attempts++; state.clickedAt = Date.now();
      try { await allow.click({ timeout: 2000 }); }
      catch (e) {
        if (!await element.evaluate(el => el.isConnected)) return true; // 已被父组件移除，下轮检查任务状态。
        throw new Error(`[WORKBUDDY_CONFIRM_FAILED] ${allowLabel}点击未确认：${e.message.split('\n')[0]}`);
      }
      this._log(`   WorkBuddy ${approval.name}：已点击“${allowLabel}”（第 ${state.attempts} 次提交），等待任务继续`);
      return true;
    } finally {
      for (const button of buttons) await button.dispose().catch(() => {});
      if (this._interceptApproval?.element !== element) await element.dispose().catch(() => {});
    }
  }

  // 持续处理反问、工作流与权限确认；无待处理交互且生成结束后才收口。
  async _waitComplete(baselineFooterCount) {
    const timeout = this.execution.responseTimeout || 180000;
    const deadline = Date.now() + timeout;
    let stableSince = 0;
    await this._clearInterceptApproval();
    await this._clearVideoApproval();
    try {
      while (Date.now() < deadline) {
        if (await this._handleVideoApproval() || await this._handleIntercept() || await this._handleQuestion() || await this._handleWorkflow()) stableSince = 0;
        else if (await this.page.locator(`:is(${this.wb.generatingSelector || 'button.cr-send-button--sending, button.cr-send-button--stop'}):visible`).count()) stableSince = 0;
        else if (await this.page.locator(this.wb.footerSelector).nth(baselineFooterCount).isVisible()) {
          if (!stableSince) stableSince = Date.now();
          if (Date.now() - stableSince >= 1500) return { completed: true, reason: 'footer' };
        } else stableSince = 0;
        await this.page.waitForTimeout(Math.min(500, Math.max(0, deadline - Date.now())));
      }
      return { completed: false, reason: 'timeout' };
    } finally {
      await this._clearInterceptApproval();
      await this._clearVideoApproval();
    }
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
      const more = this.page.locator(`:is(${moreSel}):visible`).last();
      if (!(await more.count())) return '';
      const sentinel = `__WB_MSG_${randomUUID()}__`;
      await this.page.evaluate(value => navigator.clipboard.writeText(value), sentinel);
      const timeout = this.wb.messageClickTimeout || 3000;
      await more.hover({ timeout });
      await more.click({ timeout });
      const item = this.page.locator(`:is(${itemSel}):visible`).filter({ hasText: itemText }).first();
      await item.click({ timeout });
      const deadline = Date.now() + (this.wb.messageCopyTimeout || 5000);
      while (Date.now() < deadline) {
        const clip = await this.page.evaluate(() => navigator.clipboard.readText());
        if (clip !== sentinel && parseWorkbuddyMessage(clip)) return String(clip);
        await this.page.waitForTimeout(100);
      }
      this._warn('   复制 message：未获得本轮有效结构化消息');
      return '';
    } catch (e) { this._warn(`   抓复制 message 失败: ${(e.message || '').split('\n')[0]}`); return ''; }
    finally { await this.page.keyboard.press('Escape').catch(() => {}); }
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
      errorCode: meta.errorMsg?.match(/^\[([A-Z_]+)\]/)?.[1] || null,
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
    let stage = '新建任务';
    try {
      await this._openCleanConversation();
      const baseline = await this._footerCount();
      stage = '配置模型与发送';
      await this._sendOne(testCase);
      stage = '等待回答';
      const done = await this._waitComplete(baseline);
      stage = '采集结果';
      await this.trace.captureTurn(this.page);
      const rawMessage = done.completed ? await this._captureRawMessage() : '';
      this.trace.mergeRawMessage(rawMessage);
      if (done.completed) this.trace.setShareLink(await this._captureShareLink());
      const trace = this.trace.buildTrace(testCase.run_id);
      return this._buildResult(testCase, trace, { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now(), rawMessage });
    } catch (e) {
      const msg = this._executionError(e, stage);
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
        this.trace.reset(); // 跳过的轮次不能回传上一轮的工具、回答和分享链接。
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'skipped', errorMsg: abortMsg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {}); continue;
      }
      let stage = '新建任务';
      try {
        this.trace.reset();
        if (i === 0) { await this._openCleanConversation(); }
        const baseline = await this._footerCount();
        stage = '配置模型与发送';
        await this._sendOne(testCase);                 // 后续轮不新建对话，在同一对话追加
        stage = '等待回答';
        const done = await this._waitComplete(baseline);
        stage = '采集结果';
        await this.trace.captureTurn(this.page);
        const rawMessage = done.completed ? await this._captureRawMessage() : '';
        this.trace.mergeRawMessage(rawMessage);
        if (done.completed) this.trace.setShareLink(await this._captureShareLink());
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now(), rawMessage });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
        if (!done.completed) { aborted = true; abortMsg = '上一轮未完成，后续轮跳过，避免在反问或生成中追加任务'; }
      } catch (e) {
        const msg = this._executionError(e, stage);
        const r = this._buildResult(testCase, this.trace.buildTrace(testCase.run_id), { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() });
        results.push(r); if (onTurnDone) await onTurnDone(r, testCase).catch(() => {});
        aborted = true; abortMsg = `上一轮执行失败（${stage}），后续轮跳过，避免上下文错位`;
      }
    }
    return results;
  }

  _executionError(error, stage) {
    // 保留 Playwright actionability 的 Call log，否则“10 秒超时”无法区分缺按钮、遮挡和禁用。
    const detail = String(error.message || error).replace(/\u001b\[[0-9;]*m/g, '').slice(0, 6000);
    this._warn(`   WorkBuddy ${stage}失败：${detail}`);
    this.trace.setExecutionError({ stage, detail });
    const code = detail.match(/^\[([A-Z_]+)\]/)?.[1];
    return `${code ? `[${code}] ` : ''}${stage}失败：${detail.replace(/^\[[A-Z_]+\]\s*/, '')}`;
  }
}
module.exports = { WorkbuddyRunner };
