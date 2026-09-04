// 桌面客户端「单窗口多对话并发」编排器。
//
// 为什么需要它：Web 版并发是「一个账号开多个标签页(page)真并行」，每个 DialogRunner 独占一个 page。
// 但纳米 Work 桌面版是 Electron，CDP 无法新建标签页（探针实测 Target.createTarget 不支持），
// 全程只有「一个主窗口 page」。所以桌面版的并发只能用平台自身能力实现：
//   在同一个窗口里「新建任务→发一条(不等完成)→再新建任务→发下一条…」连发出 N 个 Agent 任务，
//   它们在后端并行推进；再靠「点左侧任务列表切换」轮流查看/诊断/抓取各任务。
// 这恰是真实桌面用户的并发用法，也正对「验证并发时对话是否串台」这一核心诉求。
//
// 复用策略（桌面特有代码只有「编排」，页面原子操作全部复用现有实现）：
//   · 复用【一个】DialogRunner（attachToPage 绑定主窗口）作「操作当前可见对话」的工具箱：
//     设置对话选项 / 输入 / 确认开始 / 处理弹窗反问 / 抓取分享链接·耗时·算力豆·正文。
//   · 复用【一个】TaskWatcher（attach 绑定主窗口）的串台诊断算法（A/A1/A2/A4/B/C）。
// 任意时刻窗口只显示一个对话，故一个绑定到主窗口的 DialogRunner/TaskWatcher 就够用——
// 切到哪个任务，就用它操作/诊断哪个任务。

const workFrame = require('./work-frame');
const { typeQuestionVerified } = require('./type-question-verified');

class DesktopRunner {
  constructor(context, page, platformConfig, executionConfig, logger, diag) {
    this.context = context;
    this.page = page;
    this.platform = platformConfig || {};
    this.execution = executionConfig || {};
    this.logger = logger || null;
    this.diag = diag || null;

    const DialogRunner = require('./dialog-runner');
    const TaskWatcher = require('./task-watcher');
    // 工具箱：绑定主窗口，不 newPage/goto。sendMessage 里的 init 因 this.page 已存在而自动跳过。
    this.dr = new DialogRunner(context, platformConfig, executionConfig, logger).attachToPage(page);
    // 诊断器：绑定主窗口，复用串台检测算法。account 传空（桌面单账号=客户端登录账号）。
    this.watcher = new TaskWatcher(context, platformConfig, executionConfig, logger, '', diag);
    this.watcher.attach(page);
    this._ctxKind = null;   // 'iframe' | 'main';首次 _ensureCtx 判定后缓存
  }

  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }
  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  _fl() {
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    return workFrame.pickCtx(this.page, sel, this._ctxKind === 'iframe');
  }
  // 判定当前 page 的对话 UI 形态并缓存。每条 run 开始/切设备重建 runner 后首次调用。
  async _ensureCtx() {
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    this._ctxKind = (await workFrame.hasWorkIframe(this.page, sel)) ? 'iframe' : 'main';
    // 平台模式 watcher 不 start,但诊断读取(_readFirstQuery 等)用 this.watcher.frame:同步成当前 ctx。
    if (this.watcher) { this.watcher.frame = this._fl(); this.watcher._frameReady = true; }
    return this._ctxKind;
  }
  // 导航/重载后判定形态:轮询等"某形态的输入框可见"才定形,避免 iframe 设备真 iframe 尚未重挂回就误判 main。
  async _ensureCtxReady(timeout = 15000) {
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const inputSel = this.platform.inputSelector;
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const hasIframe = await workFrame.hasWorkIframe(this.page, sel);
      const ctx = workFrame.pickCtx(this.page, sel, hasIframe);
      try {
        await ctx.locator(inputSel).first().waitFor({ state: 'visible', timeout: 2000 });
        this._ctxKind = hasIframe ? 'iframe' : 'main';
        if (this.watcher) { this.watcher.frame = this._fl(); this.watcher._frameReady = true; }
        return this._ctxKind;
      } catch { await this._sleep(500); }
    }
    return this._ensureCtx();   // 超时兜底:按当前判定(不阻塞)
  }
  // Electron 窗口未在前台时，webview 内的自动化点击会静默失效（实测：切换/新建/发送会"点了没反应"，
  // 导致切不动对话、卡在某个会话）。故所有基于点击的交互前都先把主窗口带到前台，保证点击落地。
  async _focus() { try { await this.page.bringToFront(); } catch (_) {} }

  // 归一化文本用于「提问 ↔ 用例 query」匹配（去空白，取前若干字比较，容忍平台对 query 的包装/截断）
  _norm(s) { return ('' + (s || '')).replace(/\s+/g, ''); }
  _matchQuery(shown, want) {
    const a = this._norm(shown), b = this._norm(want);
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.length >= 6 && b.startsWith(a)) return true;   // 列表/气泡可能截断
    if (b.length >= 6 && a.startsWith(b)) return true;
    if (b.length >= 8 && a.includes(b)) return true;      // 平台把 query 包进「用户问题：…」
    if (a.length >= 8 && b.includes(a)) return true;
    return false;
  }

  // 读当前显示对话的「首个用户提问」文本（复用观察器实现）。桌面并发用它做任务定位与串台判断。
  async _readFirstQuery() { return this.watcher._readFirstQuery(); }

  // 读当前对话「本次是否已完成」的快照：是否生成中 / footer(完成信号)数 / 最后一组是否有真实答案气泡+其文本。
  async _probeState() {
    const P = this.platform;
    const fl = this._fl();
    let generating = false, footerN = 0, hasBubble = false, txt = '';
    try { generating = await fl.locator(P.stopSignalSelector).first().isVisible(); } catch {}
    if (P.costSelector) { try { footerN = await fl.locator(P.costSelector).count(); } catch {} }
    try {
      const groups = fl.locator(P.answerGroupSelector || '.chat-group.assistant');
      const n = await groups.count();
      if (n > 0) {
        const g = groups.nth(n - 1);
        if (P.answerSelector) hasBubble = (await g.locator(P.answerSelector).count()) > 0;
        txt = (await g.innerText().catch(() => '')).trim();
      }
    } catch {}
    return { generating, footerN, hasBubble, txt };
  }

  // 打开一个「干净的新对话」：点「新建任务」，确认无历史用户气泡且输入框就绪。
  // 桌面版新建任务时序不稳（实测偶发没开出干净对话），故带校验+重试。
  async _openCleanConversation() {
    const P = this.platform;
    await this._ensureCtx();   // 每条 run 开始先判定当前设备对话 UI 形态(iframe/主文档)
    const newSel = P.newTaskSelector || '.aside-panel__chat-button';
    const inputSel = P.inputSelector;
    const userSel = P.userGroupSelector || '.chat-group.user';
    await this._focus(); // 前台化，确保「新建任务」点击生效
    for (let attempt = 0; attempt < 4; attempt++) {
      const fl = this._fl();
      try {
        const btn = fl.locator(newSel).first();
        if (await btn.count() > 0) { await btn.click({ timeout: 5000 }).catch(() => {}); }
      } catch {}
      await this._sleep(1800);
      // 校验：输入框可见 且 无历史用户气泡 = 干净新对话
      try {
        await this._fl().locator(inputSel).first().waitFor({ state: 'visible', timeout: 8000 });
        const users = await this._fl().locator(userSel).count().catch(() => 0);
        if (users === 0) { this.dr.frame = this._fl(); return true; }
      } catch {}
      this._warn(`   新建对话第 ${attempt + 1} 次未干净（仍有历史气泡/输入框未就绪），重试...`);
    }
    // 兜底：回 launcher 强制干净（整页重载较重，仅在按钮反复失败时用）
    try {
      await this.page.goto(this.platform.chatUrl || 'https://work.n.cn/launcher', { waitUntil: 'domcontentloaded', timeout: this.execution.timeout || 60000 });
      await this._ensureCtxReady();   // 回 launcher 后轮询等输入框可见再定形(iframe 设备等真 iframe 重挂,不误判 main)
      await this._openCleanConversationOnce();
      this.dr.frame = this._fl();
      return true;
    } catch (e) {
      this._warn(`   回 launcher 兜底也失败：${(e.message || '').split('\n')[0]}`);
      return false;
    }
  }
  async _openCleanConversationOnce() {
    const fl = this._fl();
    const btn = fl.locator(this.platform.newTaskSelector || '.aside-panel__chat-button').first();
    if (await btn.count() > 0) { await btn.click({ timeout: 5000 }).catch(() => {}); await this._sleep(1500); }
    await this._fl().locator(this.platform.inputSelector).first().waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
  }

  // 发送一条 query 到「当前干净对话」，确认已开始执行后返回（不等完成）。复用 DialogRunner 的原子能力。
  async _sendOne(testCase) {
    const dr = this.dr;
    dr.label = testCase.caseId;
    dr.frame = this._fl();
    dr.multiTurn = false;
    await this._focus(); // 前台化，确保输入/发送点击生效
    // 基线：干净对话里回答组/footer 均为 0；供 waitForGenerationStart 判定「本轮新增」。
    dr._turnBaseline = await dr._captureBaseline();

    const ctx = dr._ctx();
    // 附件（如有）：附件是测评题目的一部分，必须确认「附件卡片真的挂上」才继续；否则抛错让本条判失败，
    // 绝不「无附件裸发 query」——那样测评无意义，且发出去还会因内容对不上而定位不到、空耗到超时。
    const atts = testCase.attachmentPaths || [];
    if (atts.length > 0) {
      if (!this.platform.fileInputSelector) throw new Error('用例带附件但未配置 fileInputSelector，无法上传');
      const fileInput = ctx.locator(this.platform.fileInputSelector).first();
      await fileInput.waitFor({ state: 'attached', timeout: 10000 }).catch(() => {});
      if (await fileInput.count() === 0) throw new Error('未找到文件上传 input，附件无法上传');
      await fileInput.setInputFiles(atts);         // 50MB 限制已由 desktop-pool 设 collocated 标志解除
      await this._waitAttachmentsReady(ctx, atts.length); // 等附件卡片出现，未挂上即抛错（本条判失败）
    }
    // 对话选项（模型/模式/思考深度）——复用 DialogRunner 实现（留空则不动）
    await dr._applyDialogOptions();

    const input = ctx.locator(this.platform.inputSelector).first();
    await input.waitFor({ timeout: 10000 });
    // 输入 query 并读回校验:附件 setInputFiles 后 ProseMirror 常失焦致 pressSequentially 静默不进字符,
    // 校验为空则重聚焦重试,仍空显式抛错(不发空 query)。type() 每次先聚焦+清空,保证重试不堆字。
    await typeQuestionVerified({
      type: async () => {
        await input.click();
        await input.evaluate((el) => {                 // 清空 ProseMirror,避免重试把字符叠加
          el.focus();
          const sel = window.getSelection();
          const range = document.createRange();
          range.selectNodeContents(el);
          sel.removeAllRanges(); sel.addRange(range);
        }).catch(() => {});
        await this.page.keyboard.press('Delete').catch(() => {});
        await dr._typeQuestion(input, testCase.question);
      },
      readText: async () => (await input.innerText().catch(() => '')) || '',
      question: testCase.question,
      sleep: (ms) => this._sleep(ms),
      warn: (m) => this._warn(`   [${testCase.caseId}] ${m}`),
    });
    await this._sleep(300);
    await ctx.locator(this.platform.sendBtnSelector).first().click();
    await dr._dismissConfirmDialogs();        // 发送刚点下可能弹「消耗算力/是否继续」确认
    await dr.waitForGenerationStart();          // 确认已进入生成，再返回（下一条才去新建）
  }

  // 等附件真正「挂上输入区」：轮询草稿附件卡片出现（数量≥期望）。卡片在 shadow DOM，
  // Playwright locator 会自动穿透 open shadow root，故直接 count 即可。超时未出现视为上传失败（抛错）。
  async _waitAttachmentsReady(ctx, expectN) {
    const sel = this.platform.attachmentCardSelector;
    if (!sel) { await this._sleep(2000); return; } // 未配校验选择器：退回固定等待（不阻断，保持旧行为）
    const timeoutMs = this.execution.attachmentUploadTimeout || 60000;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const n = await ctx.locator(sel).count().catch(() => 0);
      if (n >= expectN) return;
      await this._sleep(500);
    }
    const got = await ctx.locator(sel).count().catch(() => 0);
    throw new Error(`附件上传未完成（等待 ${(timeoutMs / 1000).toFixed(0)}s 后仅 ${got}/${expectN} 个附件卡片出现）`);
  }

  // 切换到某任务对话：优先用缓存 index 直达并校验，失配则全表扫描按「首个提问≈用例 query」定位。
  async _switchToTask(task, maxItems = 12) {
    const P = this.platform;
    await this._focus(); // 前台化，否则列表点击可能不生效（切不动对话）
    const items = this._fl().locator(P.taskListItemSelector || '.aside-panel-task-list__item');
    const total = await items.count().catch(() => 0);
    if (total === 0) return false;
    const tryIndex = async (i) => {
      if (i < 0 || i >= total) return false;
      const prev = await this._readFirstQuery();
      await items.nth(i).click({ timeout: 4000 }).catch(() => {});
      await this.watcher._waitSwitchSettled(prev);      // 等内容区切到位（复用观察器）
      const q = await this._readFirstQuery();
      if (this._matchQuery(q, task.question)) { task.listIndex = i; return true; }
      return false;
    };
    if (task.listIndex != null && await tryIndex(task.listIndex)) return true; // 缓存命中
    const cap = Math.min(total, maxItems);
    for (let i = 0; i < cap; i++) {
      if (i === task.listIndex) continue;
      if (await tryIndex(i)) return true;
    }
    return false;
  }

  // 读当前任务条目的 agent 名（供诊断 B）：从左侧「已选中」条目里读 taskListAgentSelector。
  async _currentAgentName() {
    const P = this.platform;
    if (!P.taskListAgentSelector) return '';
    try {
      const sel = this._fl().locator(`${P.taskListItemSelector || '.aside-panel-task-list__item'}.is-selected ${P.taskListAgentSelector}`).first();
      if (await sel.count().catch(() => 0) > 0) return (await sel.innerText().catch(() => '')).trim();
      // 退回：读第一个 agent 名
      const any = this._fl().locator(P.taskListAgentSelector).first();
      return (await any.innerText().catch(() => '')).trim();
    } catch { return ''; }
  }

  // 读左侧列表第 i 个条目是否有「执行中」标志（不切对话，纯读列表 DOM）。
  // 返回 true=执行中、false=疑似完成、null=条目不存在/selector 未配（定位失效，交上层重定位）。
  async _readItemRunning(i) {
    const P = this.platform;
    if (!P.taskListRunningSelector || i == null) return null;
    try {
      const items = this._fl().locator(P.taskListItemSelector || '.aside-panel-task-list__item');
      const total = await items.count();
      if (i < 0 || i >= total) return null;
      return await items.nth(i).locator(P.taskListRunningSelector).first().isVisible();
    } catch { return null; }
  }

  // 读当前「选中」条目的 index（连发后窗口停在该任务，其列表条目 .is-selected）。
  async _currentSelectedIndex() {
    const P = this.platform;
    try {
      const items = this._fl().locator(P.taskListItemSelector || '.aside-panel-task-list__item');
      const total = await items.count();
      for (let i = 0; i < total; i++) {
        const cls = await items.nth(i).getAttribute('class') || '';
        if (/\bis-selected\b/.test(cls)) return i;
      }
    } catch {}
    return null;
  }

  // 抓取当前显示对话的回填字段（复用 DialogRunner 抓取方法 + 补填）。answerOnly 时只抓正文。
  // opts.skipPanels=true（多轮中间轮）：跳过「开面板」类抓取（产物分享/对话分享/算力豆——开顶栏面板/
  // 明细弹窗会改变对话视图、污染下一轮发送），只留纯 DOM 的正文/tokens/耗时；末轮再抓全字段。
  async _extractCurrent(testCase, opts = {}) {
    const skipPanels = !!opts.skipPanels;
    const dr = this.dr;
    dr.frame = this._fl();
    dr._skipPanelFields = skipPanels; // 供 dr._missingFields:中间轮不把开面板字段算作「应有却为空」
    // 桌面版单 DialogRunner 复用：抓取前按「当前用例」重设带附件标记，供 extractBeanCost 精确匹配消耗来源。
    dr._hasAttachment = (testCase.attachmentPaths || []).length > 0;
    const out = {
      answer: '', shareLink: '', artifactShareLink: '', hasArtifact: false,
      reportedDuration: '', reportedDurationRaw: '', beanCost: '', cost: '', costRaw: '',
      reloadRecoveredFields: []
    };
    const ans = await dr.extractLastAnswer();
    out.answer = ans.text;
    // 正文优先用「点复制按钮」的规范文本（同 Web 版）：仅答案取自真实气泡时点，拿不到沿用 DOM 文本。
    if (this.execution.copyAnswer !== false && ans.fromBubble && ans.text) {
      try { const copied = await dr._withCritical(() => dr.extractAnswerViaCopy()); if (copied && copied.trim()) out.answer = copied.trim(); } catch {}
    }
    if (!this.execution.answerOnly) {
      if (!skipPanels) { try { const a = await dr._withCritical(() => dr.extractArtifactShareLink()); out.artifactShareLink = a.link; out.hasArtifact = a.hasCard; } catch {} }
      try { const c = await dr.extractCost(); out.cost = c.cost; out.costRaw = c.raw; } catch {}
      try { const d = await dr._withCritical(() => dr.extractReportedDuration()); out.reportedDuration = d.value; out.reportedDurationRaw = d.raw; } catch {}
      if (!skipPanels) {
        try { out.shareLink = await dr._withCritical(() => dr.extractConversationShareLink()); } catch {}
        try { out.beanCost = (await dr._withCritical(() => dr.extractBeanCost(testCase.question))).value; } catch {}
        // 就地补填（桌面版不做 reload 补填：会打乱其他后台任务的显示定位）
        try { await dr._refillEmptyFields(out, testCase.question); } catch {}
      }
    }
    return out;
  }

  // 把抓取结果 out + 完成信息组装成标准 result（与 Web 版一致，供回填/汇总/诊断复用）。
  _buildResult(testCase, out, meta) {
    const answerText = out.answer || '';
    const incompleteText = dr_looksIncomplete(answerText, this.execution);
    const completed = !!meta.completed;
    const errorMsg = meta.errorMsg || null;
    const incomplete = !errorMsg && (!completed || incompleteText);
    const success = !errorMsg && !incomplete && answerText.trim().length > 0;
    return {
      caseId: testCase.caseId, row: testCase.row, account: testCase.account || 'desktop',
      conversationId: testCase.conversationId, turnIndex: testCase.turnIndex,
      question: testCase.question,
      answer: errorMsg ? `[执行失败] ${errorMsg}`
            : success ? answerText
            : `[未完成:${meta.completeReason}${incompleteText ? '/中间态' : ''}]`,
      shareLink: out.shareLink, artifactShareLink: out.artifactShareLink, hasArtifact: out.hasArtifact,
      reportedDuration: out.reportedDuration, reportedDurationRaw: out.reportedDurationRaw,
      beanCost: out.beanCost, cost: out.cost, costRaw: out.costRaw,
      durationMs: (meta.endTime || Date.now()) - (meta.startTime || Date.now()),
      startTime: meta.startTime, endTime: meta.endTime,
      success, incomplete, completeReason: meta.completeReason || 'unknown',
      missingFields: success ? dr_missingFields(out, this.execution) : [],
      reloadRecoveredFields: []
    };
  }

  // 平台模式薄包装:执行「单条」对话并返回其 result(与 Web 版 result 同形状)。
  // 复用 runConcurrent 的完整生命周期(开干净对话→发送→扫列表判完成→抓取→组装 result),
  // 只传一条、取第一条结果。无 runConcurrent 之外的新执行逻辑,行为与 desktop 命令跑单条完全一致。
  async runOne(testCase) {
    const results = await this.runConcurrent([testCase]);
    if (results && results.length) return results[0];
    // 理论不会走到(runConcurrent 每条都会收尾出 result);兜底给一个失败态,避免上层拿到 undefined。
    return { caseId: testCase.caseId, success: false, incomplete: true,
             completeReason: 'no_result', answer: '[未产出结果]' };
  }

  // 平台/桌面「多轮会话」入口:同一 conversation_group 的各轮,在【同一对话】里按 turnIndex 升序【顺序连发】。
  // 轮次0 开一个干净新对话;后续轮【不新建对话】、复用当前对话追加提问,形成多轮上下文
  // (修正原平台模式「每轮各自 runOne→新建对话」导致轮次1 接不上轮次0 的问题)。
  // 每轮:发送(基线只认本轮新增的回答组/footer)→ 等本轮完成 → 抓字段(中间轮跳过开面板抓取,末轮抓全字段)
  //      → 组装 result → onTurnDone 回调(供逐轮回写各自的 run)。整段顺序执行,不并发、不切别的对话。
  // 复用 DialogRunner 的基线感知能力(_sendOne 发送前捕获基线、waitForResponseComplete/extract* 只认基线之后
  // 的新增),故在多轮同一对话里能正确识别「本轮」回答,不误取上一轮旧气泡/旧 footer。
  async runConversationTurns(turns, onTurnDone) {
    const sorted = turns.slice().sort((a, b) => (a.turnIndex || 0) - (b.turnIndex || 0));
    const results = [];
    const emptyOut = () => ({
      answer: '', shareLink: '', artifactShareLink: '', hasArtifact: false,
      reportedDuration: '', reportedDurationRaw: '', beanCost: '', cost: '', costRaw: '', reloadRecoveredFields: []
    });
    const emit = async (result, testCase) => {
      results.push(result);
      if (onTurnDone) { try { await onTurnDone(result, testCase); } catch (e) { this._warn(`   多轮回调失败 ${testCase.caseId}: ${(e.message || '').split('\n')[0]}`); } }
    };
    this._log(`🔁 多轮会话:${sorted.length} 轮将在同一对话内顺序连发(轮次0新建对话,后续轮复用当前对话)`);
    await this._focus();
    let aborted = false, abortMsg = '';
    for (let i = 0; i < sorted.length; i++) {
      const testCase = sorted[i];
      const isLast = i === sorted.length - 1;
      // 首轮失败=对话未建立,后续轮无所依附:直接按「跳过」收尾,不再裸发(避免落到错误对话)。
      if (aborted) {
        await emit(this._buildResult(testCase, emptyOut(),
          { completed: false, completeReason: 'skipped', errorMsg: abortMsg, startTime: Date.now(), endTime: Date.now() }), testCase);
        continue;
      }
      const startTime = Date.now();
      try {
        if (i === 0) {
          const clean = await this._openCleanConversation(); // 仅轮次0开干净新对话
          if (!clean) throw new Error('无法打开干净的新对话(多轮首轮)');
        }
        // _sendOne 内先捕获发送前基线(prior turns 的回答组/footer 数),再发送本轮、确认已开始生成。
        // 首轮基线为 0;后续轮不新建对话、在当前对话追加提问,基线=已有轮数 → 后续等待/抓取只认「本轮新增」。
        await this._sendOne(testCase);
        const done = await this.dr.waitForResponseComplete();          // 基线感知:等「本轮」回答完成
        const out = await this._extractCurrent(testCase, { skipPanels: !isLast }); // 中间轮跳过开面板抓取
        const result = this._buildResult(testCase, out,
          { completed: done.completed, completeReason: done.reason, errorMsg: null, startTime, endTime: Date.now() });
        const st = result.success ? '✅' : '❌';
        this._log(`   ${st} 多轮 ${testCase.caseId}（第 ${i + 1}/${sorted.length} 轮，${(result.durationMs / 1000).toFixed(1)}s）${result.success ? '' : ' ' + result.answer}`);
        await emit(result, testCase);
      } catch (e) {
        const msg = (e && e.message) ? e.message.split('\n')[0] : String(e);
        this._warn(`   多轮第 ${i + 1}/${sorted.length} 轮失败:${testCase.caseId} - ${msg}`);
        await emit(this._buildResult(testCase, emptyOut(),
          { completed: false, completeReason: 'exception', errorMsg: msg, startTime, endTime: Date.now() }), testCase);
        // 首轮失败则整组后续轮跳过;中间轮失败不中止(上下文可能仍在),继续尝试后续轮。
        if (i === 0) { aborted = true; abortMsg = '首轮失败,多轮上下文未建立,后续轮跳过'; }
      }
    }
    return results;
  }

  // 主入口：把一批用例作为「并发对话」在单窗口内跑起来，逐条完成即回调 onResult（用于实时回填）。
  // 返回全部 result 数组。sendIntervalMs 控制连发节奏；responseTimeout 为每条上限；总轮询到全部完成。
  async runConcurrent(cases, onResult) {
    const P = this.platform;
    const sendInterval = this.execution.sendIntervalMs != null ? this.execution.sendIntervalMs : 3000;
    const patrolInterval = this.execution.desktopPatrolMs != null ? this.execution.desktopPatrolMs : 8000;
    const perTaskTimeout = this.execution.responseTimeout || 1800000;
    // 扫列表判完成：执行中任务不切对话，只读左侧条目「执行中」标志；标志消失=疑似完成才切过去二次确认+抓取。
    // fallback：某 task 距上次复查超此时长仍未被列表判完成（列表漏判/标志一直 true），切过去 probeState 探查，避免干等超时。
    const fallbackMs = this.execution.desktopProbeFallbackMs != null ? this.execution.desktopProbeFallbackMs : 180000;

    const tasks = cases.map(c => ({
      case: c, question: c.question, status: 'pending',
      listIndex: null, startTime: null, endTime: null,
      completed: false, completeReason: 'unknown', errorMsg: null,
      lastText: null, stable: 0, result: null
    }));

    // —— 阶段 1：连发（串行新建对话并发送，确认开始后再发下一条）——
    this._log(`🖥️ 桌面并发：连发 ${tasks.length} 条对话（单窗口内新建任务连发）...`);
    for (let i = 0; i < tasks.length; i++) {
      const t = tasks[i];
      try {
        const clean = await this._openCleanConversation();
        if (!clean) throw new Error('无法打开干净的新对话');
        t.startTime = Date.now();
        await this._sendOne(t.case);
        t.listIndex = await this._currentSelectedIndex(); // 记录选中条目 index，供阶段2扫列表判完成（不切对话）
        t.status = 'running';
        this._log(`   [${i + 1}/${tasks.length}] 已发送并开始执行：${t.case.caseId}  「${t.question.slice(0, 24)}」`);
      } catch (e) {
        t.errorMsg = (e.message || '').split('\n')[0]; t.endTime = Date.now();
        this._warn(`   [${i + 1}/${tasks.length}] 发送失败：${t.case.caseId} - ${t.errorMsg}`);
        await this._finishTask(t, onResult); // 立即收尾为失败结果（否则该条不进 results、不回填）
      }
      if (i < tasks.length - 1 && sendInterval > 0) await this._sleep(sendInterval);
    }

    const running = () => tasks.filter(t => t.status === 'running');
    this._log(`🔎 全部连发完成，开始扫列表判完成 ${running().length} 个在跑任务（执行中不切对话，疑似完成才切过去复查+抓取）...`);

    // —— 阶段 2：扫列表判完成（不每轮切对话；只对疑似完成/到 fallback 的任务切过去复查+抓取）——
    const globalDeadline = Date.now() + perTaskTimeout + tasks.length * 60000;
    while (running().length > 0 && Date.now() < globalDeadline) {
      for (const t of running()) {
        // 超时保护：单条超过 perTaskTimeout 仍未完成，判 timeout 收尾（先切到它，抓取才对得上该对话）
        if (Date.now() - t.startTime > perTaskTimeout) {
          await this._switchToTask(t).catch(() => {});
          t.completed = false; t.completeReason = 'timeout';
          await this._finishTask(t, onResult);
          continue;
        }
        // 扫左侧列表条目「执行中」标志（不切对话内容区）：false=疑似完成；true=执行中(跳过)；null=index失效
        const flag = await this._readItemRunning(t.listIndex);
        if (flag === null) t.listIndex = null;
        const byFlag = (flag === false);
        const byFallback = Date.now() - (t.lastProbeAt || t.startTime) > fallbackMs;
        if (!byFlag && !byFallback) continue; // 执行中且未到 fallback：不切，跳过（核心：不来回点）
        // 疑似完成 或 到 fallback：切过去 probeState 复查（顺带处理反问/确认弹窗让任务继续）
        const ok = await this._switchToTask(t);
        if (!ok) { this._log(`   ⏳ 暂未定位到任务 ${t.case.caseId}（列表未出齐/标题变动），下轮重试`); t.listIndex = null; continue; }
        t.lastProbeAt = Date.now();
        try { await this.dr._dismissConfirmDialogs(); } catch {}
        const st = await this._probeState();
        let done = false;
        if (st.footerN > 0 && !st.generating) done = true;
        else if (!st.generating && st.hasBubble && st.txt && st.txt === t.lastText) {
          t.stable++; if (t.stable >= 2) done = true;
        } else t.stable = 0;
        t.lastText = st.txt;
        const mark = st.generating ? '执行中' : (done ? '完成' : '…');
        const why = byFlag ? 'flag' : 'fallback';
        this._log(`   🔎 复查 ${t.case.caseId}[${why}] → ${mark}${st.txt ? '：' + st.txt.slice(0, 30).replace(/\s+/g, ' ') : ''}`);
        if (done) { t.completed = true; t.completeReason = st.footerN > 0 ? 'footer' : 'stable'; await this._finishTask(t, onResult); }
      }
      if (running().length > 0) await this._sleep(patrolInterval);
    }

    // 超全局时限仍未完成的，收尾为 timeout（各自先切过去，尽量抓到已有的部分答案）
    for (const t of running()) {
      await this._switchToTask(t).catch(() => {});
      t.completed = false; t.completeReason = 'timeout'; await this._finishTask(t, onResult);
    }

    return tasks.map(t => t.result).filter(Boolean);
  }

  // 单任务收尾：（已切到该任务/或发送即失败）抓取字段 → 组装 result → 回调回填。
  async _finishTask(t, onResult) {
    if (t.status === 'done' && t.result) return; // 幂等
    t.endTime = t.endTime || Date.now();
    let out = { answer: '', shareLink: '', artifactShareLink: '', hasArtifact: false,
      reportedDuration: '', reportedDurationRaw: '', beanCost: '', cost: '', costRaw: '', reloadRecoveredFields: [] };
    if (!t.errorMsg) {
      try { out = await this._extractCurrent(t.case); }
      catch (e) { t.errorMsg = t.errorMsg || (e.message || '').split('\n')[0]; }
    }
    // 完成信号已出但没抓到正文 → 诊断 D
    if (this.diag && t.completed && !t.errorMsg && !(out.answer && out.answer.trim())) {
      try { await this.diag.record(t.case.caseId, 'D-完成无正文', `完成信号(${t.completeReason})已出现但未抓到有效正文`, this.page); } catch {}
    }
    const result = this._buildResult(t.case, out, {
      completed: t.completed, completeReason: t.completeReason, errorMsg: t.errorMsg,
      startTime: t.startTime, endTime: t.endTime
    });
    t.result = result;
    t.status = 'done';
    const status = result.success ? '✅' : '❌';
    const dur = (result.durationMs / 1000).toFixed(1);
    this._log(`   ${status} 完成 ${t.case.caseId}（${dur}s）${result.success ? '' : ' ' + result.answer}`);
    if (onResult) { try { await onResult(result, t.case); } catch (e) { this._warn(`   回调失败 ${t.case.caseId}: ${(e.message || '').split('\n')[0]}`); } }
  }
}

// —— 从 DialogRunner 借用两个纯判定逻辑（不依赖实例状态），避免重复实现且与 Web 版口径一致 ——
function dr_looksIncomplete(text, execution) {
  const t = (text || '').trim();
  if (!t) return true;
  const markers = (execution && execution.incompleteMarkers) || ['思考中'];
  return markers.some(m => m && t.startsWith(m));
}
function dr_missingFields(out, execution) {
  if (execution && execution.answerOnly) return [];
  const required = (execution && execution.requiredFields) || ['conversationShareLink', 'artifactShareLink', 'duration', 'beanCost'];
  const missing = [];
  for (const f of required) {
    if (f === 'conversationShareLink') { if (!out.shareLink) missing.push(f); }
    else if (f === 'artifactShareLink') { if (out.hasArtifact && !out.artifactShareLink) missing.push(f); }
    else if (f === 'duration') { if (!out.reportedDuration) missing.push(f); }
    else if (f === 'beanCost') { if (!out.beanCost) missing.push(f); }
  }
  return missing;
}

module.exports = DesktopRunner;
