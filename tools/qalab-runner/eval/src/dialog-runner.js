const fs = require('fs');
const path = require('path');
const workFrame = require('./work-frame');
const { ensureAllSelected } = require('./share-select');

// 分享链接经系统剪贴板传递，并发下多个标签会互相覆盖剪贴板，故这一步需串行化
let _shareLock = Promise.resolve();

// 「抓取临界区」占用标记：抓分享链接依赖系统剪贴板（全机共享，非页面级）、抓算力豆要开明细弹窗、
// 抓耗时要 hover tooltip——这些都会被「并发观察器」点击左侧列表切换对话的动作打断/污染。
// 进入这些抓取时 >0，观察器读到就暂停本轮切换（等抓完再切），避免把回填字段抓空。
// 用计数而非布尔：同账号多任务的抓取经 _shareLock 串行，但耗时/算力豆不在该锁内、可能并发进入。
let _extractingCritical = 0;
function isExtractingCritical() { return _extractingCritical > 0; }
function _enterCritical() { _extractingCritical++; }
function _leaveCritical() { _extractingCritical = Math.max(0, _extractingCritical - 1); }

class DialogRunner {
  constructor(context, platformConfig, executionConfig, logger) {
    this.context = context;
    this.platform = platformConfig;
    this.execution = executionConfig || {};
    this.logger = logger || null;
    this.page = null;
    this.frame = null;
    this.label = '';         // 巡检日志用的任务标识（外部设置，如 caseId）
    this.generating = false; // 是否处于“正在生成”态（供外部巡检查询）
    // 账号内“发送有序”串行器：外部注入 (fn)=>Promise，把「打开页面→新对话→输入→点发送→确认已开始生成」
    // 串行化，确保上一条确认进入生成后才发下一条；不注入则直接执行（退化为无序/单条）。
    this.sendGate = (fn) => fn();
  }

  // 【桌面场景】绑定一个「外部已就绪的 page」（如 Electron 主窗口），不自己 newPage/goto。
  // Web 版靠 init() 新开标签页并 goto launcher；桌面版全程只有一个主窗口 page，
  // 由 DesktopRunner 负责「新建任务/切到目标对话」，DialogRunner 只在这个 page 上做
  // 输入/发送/等待/抓取。绑定后 this.page 已存在，sendMessage 里的 init 会自动跳过。
  // ownsPage=false 记号：close() 时不关这个共享主窗口（关了整个客户端窗口就没了）。
  attachToPage(page) {
    this.page = page;
    this.ownsPage = false;
    // ctx 由 desktop-runner._fl() 每次注入(desktop 模式)或 _waitForFrame 设(Web 模式);
    // 这里给主文档安全默认,避免主文档形态下写死 frameLocator 失效。
    this.frame = page;
    return this;
  }

  async init() {
    const timeout = this.execution.timeout || 60000;
    this.page = await this.context.newPage();
    // 执行过程中平台可能弹浏览器原生确认框（alert/confirm/beforeunload），一律放行以免卡住
    this.page.on('dialog', async (dialog) => {
      try {
        if (this.logger) this.logger.info(`       ↳ [${this.label}] 自动确认原生弹窗(${dialog.type()})`);
        await dialog.accept();
      } catch (_) { try { await dialog.dismiss(); } catch (_) {} }
    });
    await this.page.goto(this.platform.chatUrl, { waitUntil: 'domcontentloaded', timeout });
    this.frame = await this._waitForFrame(timeout);
    await this.page.waitForTimeout(1500); // 等 SPA 就绪
    await this._startNewConversation(); // 每条用例独立新对话（账号有历史时 /launcher 会停在首页）
    await this.page.waitForTimeout(1500);
  }

  // 点“新建任务”开启干净对话，避免落在首页/复用上一条会话导致抓不到回答
  async _startNewConversation() {
    const sel = this.platform.newTaskSelector;
    if (!sel) return;
    try {
      const btn = this._ctx().locator(sel).first();
      if (await btn.count() > 0) {
        await btn.click({ timeout: 4000 });
        this.frame = await this._waitForFrame(this.execution.timeout || 60000);
      }
    } catch (_) {}
  }

  // 页面分两阶段：/launcher 先挂临时 about:srcdoc 占位 iframe，再跳到 /claw?vm_id=xxx
  // 挂载真正的对话 iframe（src=https://<vmid>.work.n.cn）。占位 frame 稍后会被 detach，
  // 故用 frameLocator（每次操作自动重解析 iframe），只匹配含 .work.n.cn 的真实 VM iframe。
  async _waitForFrame(timeout) {
    const iframeSel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const inputSel = this.platform.inputSelector;
    const deadline = Date.now() + timeout;
    let lastErr = null;
    while (Date.now() < deadline) {
      try {
        // 自适应:有 work.n.cn iframe 走 frameLocator,否则主文档 page。
        const hasIframe = await workFrame.hasWorkIframe(this.page, iframeSel);
        const ctx = workFrame.pickCtx(this.page, iframeSel, hasIframe);
        const input = ctx.locator(inputSel).first();
        await input.waitFor({ state: 'visible', timeout: 3000 });
        await input.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
        return ctx;
      } catch (e) {
        lastErr = e;
        await this.page.waitForTimeout(1000);
      }
    }
    throw new Error(
      `页面未加载完成，找不到对话输入框（input: ${inputSel}，iframe/主文档均已尝试）：` +
      `${lastErr ? lastErr.message.split('\n')[0] : 'timeout'}。` +
      `若页面显示“刷新重试”，多为后端 VM 未就绪，可增大 execution.timeout 后重试。`
    );
  }

  _ctx() {
    return this.frame || this.page;
  }

  // 多轮基线：抓「发送本轮前」页面已有的 AI 回答组数与完成信号(footer)数。
  // 单轮/首轮时页面本无回答，基线为 0，行为与原先完全一致。
  async _captureBaseline() {
    const ctx = this._ctx();
    let groupCount = 0, footerCount = 0;
    try {
      groupCount = await ctx.locator(this.platform.answerGroupSelector || '.chat-group.assistant').count();
    } catch {}
    if (this.platform.costSelector) {
      try { footerCount = await ctx.locator(this.platform.costSelector).count(); } catch {}
    }
    return { groupCount, footerCount };
  }

  // 本轮的基线组数（发送前已有的回答组数）；只认「第 baseGroups 组之后新增的」为本轮回答。
  _baseGroups() { return (this._turnBaseline && this._turnBaseline.groupCount) || 0; }
  _baseFooters() { return (this._turnBaseline && this._turnBaseline.footerCount) || 0; }

  // 用于 evaluate 的活跃 VM frame（FrameLocator 没有 evaluate）
  _liveFrame() {
    return workFrame.liveFrame(this.page);
  }

  // opts.isLastTurn：多轮会话里本轮是否为末轮。中间轮(非末轮)禁用「开面板」类抓取
  // （分享链接/算力豆/产物分享——会打开顶栏面板/明细弹窗，改变对话视图，污染下一轮发送），
  // 只抓答案+tokens+耗时；末轮抓全字段。单轮会话恒为末轮，抓全字段（行为不变）。
  async sendMessage(question, attachmentPaths = [], opts = {}) {
    const isLastTurn = opts.isLastTurn !== false; // 默认 true（单轮/末轮都抓全字段）
    const skipPanelExtracts = this.multiTurn && !isLastTurn; // 多轮中间轮：跳过开面板抓取
    // 供 _missingFields 判定：中间轮不把开面板字段(分享链接/算力豆/产物)算作「应有却为空」，
    // 否则 _refillEmptyFields 会因它们空而反复开面板重抓（违背跳过初衷）。
    this._skipPanelFields = skipPanelExtracts;
    // 供 extractBeanCost 判定：本条用例是否带附件（带附件时对「消耗来源」行做更精确匹配）。
    this._hasAttachment = (attachmentPaths || []).length > 0;
    const startTime = Date.now();
    let endTime = null;
    let errorMsg = null;
    // 完成判定结果（供 success 判定与重跑决策用）：是否真正完成、完成原因、正文是否来自真实答案气泡
    let completed = false, completeReason = 'unknown', answerFromBubble = false;
    // 所有回填字段累积到一个对象，便于补填阶段就地重抓
    const out = {
      answer: '',
      shareLink: '',            // C 对话分享链接
      artifactShareLink: '',    // D 产物分享链接
      hasArtifact: false,       // 是否检测到产物卡片（决定 D 为空是否算“缺失”）
      reportedDuration: '',     // E 耗时（平台上报）
      reportedDurationRaw: '',  // E 原始 tooltip 文本（排查用）
      beanCost: '',             // F 算力豆变动值
      cost: '',                 // 附加：本次 tokens（footer，仅记录）
      costRaw: '',
      reloadRecoveredFields: [] // 靠「刷新页面重抓」才补上的字段（诊断用：如耗时需刷新才拿到）
    };

    try {
      // 页面准备（打开标签、加载对话 iframe、开新对话）不占发送门，各任务并行进行，
      // 让较慢的 VM 就绪等待相互重叠。若已由重跑分支准备好页面则跳过。
      if (!this.page) await this.init();

      // —— 发送段（在账号内 sendGate 中串行执行）——
      // 上传附件 → 输入 → 点发送 → 确认已进入“生成中”。确认进入生成后才释放 gate，
      // 从而保证“上一条顺利发出去执行后，再发后续任务”。
      await this.sendGate(async () => {
        const ctx = this._ctx();

        // 多轮基线：记录发送「本轮」前页面已有的 AI 回答组数 / 完成信号(footer)数。
        // 之后「启动确认 / 完成判定 / 正文抓取」只认「超出基线的新增」，
        // 避免 follow-up 轮把上一轮遗留的旧气泡/旧 footer 误判为本轮已启动或已完成（多轮核心正确性）。
        this._turnBaseline = await this._captureBaseline();

        if (attachmentPaths.length > 0 && this.platform.fileInputSelector) {
          const fileInput = ctx.locator(this.platform.fileInputSelector);
          if (await fileInput.count() > 0) {
            await fileInput.setInputFiles(attachmentPaths);
            await this.page.waitForTimeout(2000);
          }
        }

        // 发送前设置对话选项（模型 / 对话模式 / 深度思考）；留空的项跳过、失败不阻断发送。
        await this._applyDialogOptions();

        const input = ctx.locator(this.platform.inputSelector).first();
        await input.waitFor({ timeout: 10000 });
        await input.click();
        await this._typeQuestion(input, question); // 短文逐字输入(ProseMirror 更稳)，超长文改粘贴(避免逐字超时)
        await this.page.waitForTimeout(300);
        await ctx.locator(this.platform.sendBtnSelector).first().click();
        // 发送刚点下就可能弹确认框（如“本次将消耗算力/是否继续”），先处理一次再等启动
        await this._dismissConfirmDialogs();
        await this.waitForGenerationStart(); // 确认任务已开始执行，再让下一条进入发送
        if (this.logger) this.logger.info(`       ↳ [${this.label}] 已发送，任务开始执行`);
      });

      // —— 等待 + 抓取段（gate 外，各任务并行）——
      const done = await this.waitForResponseComplete();
      completed = done.completed; completeReason = done.reason;

      // 抓取顺序：正文 → 产物分享(D，趁预览刚打开最新时立刻抓，晚了预览会自动收起) →
      //           tokens → 耗时 → 对话分享(C) → 算力豆(F)
      const ans = await this.extractLastAnswer();
      out.answer = ans.text; answerFromBubble = ans.fromBubble;

      // 正文优先用「点回答下方复制按钮」取到的规范文本（带 Markdown 格式，比 DOM innerText 准）；
      // 仅在「答案取自真实气泡」时才点复制（思考中/未完成态不点，避免复制到无意义中间态），
      // 拿到就覆盖，拿不到沿用 DOM 文本（不倒退）。answer-only 也走此增强（H 列即最终交付）。
      if (this.execution.copyAnswer !== false && answerFromBubble && ans.text) {
        const copied = await this._withCritical(() => this.extractAnswerViaCopy());
        if (copied && copied.trim()) {
          out.answer = copied.trim();
          if (this.logger) this.logger.info(`       ↳ [${this.label}] 正文改用「复制按钮」规范文本(${out.answer.length}字)`);
        }
      }

      if (this.execution.answerOnly) {
        // --answer-only：纯跑对话、不抓回填字段。仅 extractLastAnswer 是「完成判定」必需（否则无法判断
        // 成功/未完成、无法回填 H 列答案），不算「抓取回填数据」；其余分享链接/产物/耗时/算力豆/tokens
        // 抓取及补填/刷新重抓全部跳过。配合 --skip-writeback 即「只跑多轮对话，零抓取零回填」。
        if (this.logger) this.logger.info(`       ↳ [${this.label}] answer-only：仅取答案正文(完成判定必需)，跳过全部字段抓取`);
      } else {
        // 产物分享/耗时/对话分享/算力豆这几步依赖系统剪贴板 / 明细弹窗 / hover tooltip，
        // 会被并发观察器切换动作打断，故整体置于抓取临界区（观察器读到即暂停切换）。
        // 正文、tokens 是纯 DOM 读取，不怕切换，放在临界区外。
        // 多轮中间轮：跳过「开面板」类抓取（产物分享/对话分享/算力豆——开顶栏面板/明细弹窗会
        // 改变对话视图、污染下一轮发送），只留纯 DOM 的 tokens/耗时；末轮再抓全字段。
        if (!skipPanelExtracts) {
          const art = await this._withCritical(() => this.extractArtifactShareLink());
          out.artifactShareLink = art.link; out.hasArtifact = art.hasCard;
        }
        const costInfo = await this.extractCost();
        out.cost = costInfo.cost; out.costRaw = costInfo.raw;
        const durInfo = await this._withCritical(() => this.extractReportedDuration());
        out.reportedDuration = durInfo.value; out.reportedDurationRaw = durInfo.raw;
        if (!skipPanelExtracts) {
          out.shareLink = await this._withCritical(() => this.extractConversationShareLink());
          out.beanCost = (await this._withCritical(() => this.extractBeanCost(question))).value;
        } else if (this.logger) {
          this.logger.info(`       ↳ [${this.label}] 多轮中间轮：已跳过分享链接/算力豆/产物抓取（末轮再抓）`);
        }

        // 补填：对话还开着时，对“应有却为空”的字段就地重抓（代价小，无需翻历史会话）
        await this._refillEmptyFields(out, question);
        // 就地补填仍缺关键字段时，刷新页面再抓一次（应对「耗时等字段答完后需刷新才渲染」）。
        out.reloadRecoveredFields = await this._reloadAndRefill(out, question);
      }

      endTime = Date.now();
    } catch (e) {
      endTime = Date.now();
      errorMsg = e.message;
    } finally {
      this.generating = false;
    }

    const answerText = out.answer || '';
    const incompleteText = this._looksIncomplete(answerText);
    // 未完成 = 超时/停滞/未启动（!completed），或正文是“思考中…”中间态（即便没超时，抓到的也不是最终答案）。
    // 这类失败与“抛异常失败(errorMsg)”分开标注，供重跑策略区别对待。
    const incomplete = !errorMsg && (!completed || incompleteText);
    const success = !errorMsg && !incomplete && answerText.trim().length > 0;
    return {
      question,
      // 失败/未完成时不回填“思考中…”长文垃圾，写简短状态标识（如 [未完成:timeout]），便于表格如实排查
      answer: errorMsg ? `[执行失败] ${errorMsg}`
            : success ? answerText
            : `[未完成:${completeReason}${incompleteText ? '/中间态' : ''}]`,
      shareLink: out.shareLink,              // C 对话分享链接
      artifactShareLink: out.artifactShareLink, // D 产物分享链接
      hasArtifact: out.hasArtifact,          // 是否检测到产物（诊断/补填判定用）
      reportedDuration: out.reportedDuration,   // E 耗时（平台上报）
      reportedDurationRaw: out.reportedDurationRaw, // E 原始 tooltip 文本（排查用）
      beanCost: out.beanCost,                // F 算力豆变动值
      cost: out.cost,                        // 附加：本次 tokens（footer，仅记录）
      costRaw: out.costRaw,
      durationMs: endTime - startTime, // 附加：墙钟耗时（仅记录，不回填）
      startTime,
      endTime,
      success,
      incomplete,                    // 是否属于“超时/中间态未完成”（供 runWithRetry 决定是否重跑）
      completeReason,                // 完成/未完成原因：footer/stable/timeout/stall/nostart
      answerFromBubble,              // 诊断：正文是否来自真实答案气泡（false=可能抓到了思考态）
      reloadRecoveredFields: out.reloadRecoveredFields || [], // 诊断：靠刷新才补上的字段（如耗时需刷新）
      missingFields: success ? this._missingFields(out) : [] // “应有却为空”的字段（成功后才有意义）
    };
  }

  // 输入 query 到 ProseMirror 输入框：短文逐字输入（对 contenteditable 最稳）；
  // 超长文（>阈值）改用剪贴板粘贴——逐字 delay 累加会超时（CASE-28：几百行 prompt 模板逐字输入超 30s 卡死）。
  // ⚠️ 含换行的 query 绝不能走裸逐字：pressSequentially 会把 \n 当成 Enter 键发出，而聊天框
  //    Enter=发送，会导致「一条含换行的 query 在每个换行处被提前发送、拆成多条」。故只要含换行
  //    就走粘贴（原生插入，不产生 Enter）；逐字兜底也把换行改成 Shift+Enter 软换行。
  async _typeQuestion(input, question) {
    const q = question || '';
    const threshold = this.execution.pasteThreshold || 800; // 超过此长度改粘贴
    const hasNewline = /[\r\n]/.test(q);
    // 仅「短文且不含换行」才逐字；含换行（无论长短）一律走粘贴，避免 \n 触发 Enter 提前发送。
    if (q.length <= threshold && !hasNewline) {
      await input.pressSequentially(q, { delay: 12 }); // 短文逐字更可靠
      return;
    }
    // 长文或含换行：写系统剪贴板 → 聚焦输入框 → Ctrl+V 粘贴。粘贴对 ProseMirror 是原生插入，
    // 一次到位、不触发 Enter 发送、也不超时。
    try {
      await this.page.evaluate((text) => navigator.clipboard.writeText(text), q);
      await input.click();
      await this.page.keyboard.press('Control+V');
      await this.page.waitForTimeout(500);
      // 校验粘贴是否进去了（ProseMirror 文本非空）；没进去则回退到逐字（限一次，避免超长再次卡死时也有兜底）
      const got = ((await input.innerText().catch(() => '')) || '').trim();
      if (got.length < Math.min(20, q.length)) {
        if (this.logger) this.logger.warn(`       ↳ [${this.label}] 粘贴疑似未生效，回退逐字输入（换行转软换行，可能较慢）`);
        await this._typeCharsSafe(input, q);
      }
    } catch (e) {
      if (this.logger) this.logger.warn(`       ↳ [${this.label}] 粘贴失败，回退逐字输入（换行转软换行）: ${(e.message || '').split('\n')[0]}`);
      await this._typeCharsSafe(input, q);
    }
  }

  // 逐字输入兜底：按行拆分，行内逐字 pressSequentially（项目验证最稳），行间用 Shift+Enter 软换行，
  // 而非裸 \n——裸 \n 会被当成 Enter 触发聊天框「发送」，把一条 query 拆成多条。
  async _typeCharsSafe(input, question) {
    const lines = String(question || '').split(/\r\n|\r|\n/);
    for (let i = 0; i < lines.length; i++) {
      if (i > 0) await this.page.keyboard.press('Shift+Enter'); // 软换行，不发送
      if (lines[i]) await input.pressSequentially(lines[i], { delay: 2 });
    }
  }

  // 发送前设置对话选项：模型 / 对话模式 / 深度思考。留空的项跳过；任一步失败仅告警、不阻断发送。
  async _applyDialogOptions() {
    const opt = this.execution.dialogOptions || {};
    if (!opt.model && !opt.chatMode && !opt.thinkingDepth) return; // 三项都留空 → 完全不动
    const P = this.platform;
    if (opt.model) {
      await this._pickDropdownOption('模型', P.modelDropdownSelector, P.modelOptionSelector, opt.model);
    }
    if (opt.chatMode) {
      await this._pickDropdownOption('对话模式', P.chatModeTriggerSelector, P.chatModeOptionSelector, opt.chatMode);
    }
    if (opt.thinkingDepth) {
      await this._pickDropdownOption('深度思考', P.thinkingDepthTriggerSelector, P.thinkingDepthOptionSelector, opt.thinkingDepth);
    }
  }

  // 通用：展开某下拉(点 triggerSel) → 在展开项里按文本精确匹配点选 wanted。
  //  optionSel 为空则纯文本匹配可见元素(用于选项 class 未知的深度下拉)。失败仅告警不抛。
  async _pickDropdownOption(name, triggerSel, optionSel, wanted) {
    if (!triggerSel || !wanted) return;
    const ctx = this._ctx();
    try {
      const trigger = ctx.locator(triggerSel).first();
      if (await trigger.count().catch(() => 0) === 0) {
        if (this.logger) this.logger.warn(`       ↳ [${this.label}] 设置${name}失败：找不到触发控件(${triggerSel})`);
        return;
      }
      await trigger.click({ timeout: 4000 }).catch(() => {});
      await this.page.waitForTimeout(300);
      // 对话模式/深度思考的触发是 lit Web Component 里 shadow 内的 button，Playwright 坐标点击常不触发展开；
      // 用原生 el.click() 兜底（探测确认对这两个 button 有效）。展开状态用「选项是否出现」来判定。
      let opened = false;
      if (optionSel) opened = (await this._findOptionByText(optionSel, wanted)) != null;
      if (!opened) opened = (await ctx.getByText(wanted, { exact: true }).count().catch(() => 0)) > 0;
      if (!opened) {
        await trigger.evaluate((el) => el.click()).catch(() => {});
        await this.page.waitForTimeout(600);
      }

      // 用配置的选项选择器 + 宽松文本匹配（容忍全半角括号/空格/大小写差异，见 _findOptionByText）；
      // optionSel 为空（如深度思考）或没命中时，退回纯文本匹配（getByText exact）。
      let option = null;
      if (optionSel) option = await this._findOptionByText(optionSel, wanted);
      if (!option) option = ctx.getByText(wanted, { exact: true }).first();
      if (await option.count().catch(() => 0) === 0) {
        if (this.logger) this.logger.warn(`       ↳ [${this.label}] 设置${name}失败：下拉里找不到「${wanted}」`);
        await this.page.keyboard.press('Escape').catch(() => {});
        return;
      }
      await option.click({ timeout: 4000 }).catch(() => {});
      await this.page.waitForTimeout(400);
      if (this.logger) this.logger.info(`       ↳ [${this.label}] 已设置${name} = ${wanted}`);
    } catch (e) {
      if (this.logger) this.logger.warn(`       ↳ [${this.label}] 设置${name}异常（忽略）: ${(e.message || '').split('\n')[0]}`);
      await this.page.keyboard.press('Escape').catch(() => {});
    }
  }

  // 归一化选项文本用于宽松匹配：去所有空白、全角括号→半角、大小写统一。
  // 让 --model "豆包(seed-2.1)"、"豆包（seed-2.1）"、"豆包 (seed-2.1)" 都能命中页面上的
  // 「豆包（seed-2.1）」（全角括号无空格）。仅用于匹配比较，点击的仍是页面原始选项。
  _normOptionText(s) {
    return ('' + (s || ''))
      .replace(/[\s　 ]/g, '') // 半角/全角/不断行空格全去掉
      .replace(/[（【〔]/g, '(').replace(/[）】〕]/g, ')') // 常见全角括号→半角
      .toLowerCase();
  }

  // 在 optionSel 命中的可见选项里，按归一化文本找与 wanted 相等的那个，返回其 locator（未命中返回 null）。
  // 先归一化相等，再退化到「包含」（应对选项文本尾部带小标签等杂串）。
  async _findOptionByText(optionSel, wanted) {
    const ctx = this._ctx();
    const want = this._normOptionText(wanted);
    if (!want) return null;
    const opts = ctx.locator(optionSel);
    const n = await opts.count().catch(() => 0);
    let contains = null;
    for (let i = 0; i < n; i++) {
      const raw = (await opts.nth(i).innerText().catch(() => '')) || '';
      const t = this._normOptionText(raw);
      if (!t) continue;
      if (t === want) return opts.nth(i);          // 精确（归一化后）优先
      if (contains == null && (t.includes(want) || want.includes(t))) contains = opts.nth(i);
    }
    return contains; // 没有归一化精确项时，用包含匹配兜底
  }

  // 正文是否是“未完成中间态”（思考过程而非最终答案）：以配置的中间态标记词开头即算；空也算未完成。
  _looksIncomplete(text) {
    const t = (text || '').trim();
    if (!t) return true;
    const markers = this.execution.incompleteMarkers || ['思考中'];
    return markers.some(m => m && t.startsWith(m));
  }

  // 需要“非空”的回填字段清单（可配置）。artifactShareLink 仅在确有产物时才要求。
  _requiredFields() {
    return this.execution.requiredFields ||
      ['conversationShareLink', 'artifactShareLink', 'duration', 'beanCost'];
  }

  // 在给定抓取结果 out 上，判断哪些必填字段“应有却为空”
  _missingFields(out) {
    // --answer-only：只抓答案、不抓字段，没有任何“应有却为空”的字段，返回空（也不因缺失触发重跑）
    if (this.execution.answerOnly) return [];
    // 多轮中间轮：跳过了开面板抓取(分享链接/算力豆/产物)，这些字段不算缺失，避免补填时开面板
    const panelFields = this._skipPanelFields ? ['conversationShareLink', 'artifactShareLink', 'beanCost'] : null;
    const missing = [];
    for (const f of this._requiredFields()) {
      if (panelFields && panelFields.includes(f)) continue;
      if (f === 'conversationShareLink') { if (!out.shareLink) missing.push(f); }
      else if (f === 'artifactShareLink') { if (out.hasArtifact && !out.artifactShareLink) missing.push(f); }
      else if (f === 'duration') { if (!out.reportedDuration) missing.push(f); }
      else if (f === 'beanCost') { if (!out.beanCost) missing.push(f); }
    }
    return missing;
  }

  // 补填：对话仍开着时，对“应有却为空”的字段就地重抓若干轮，直到抓齐或轮次用尽。
  // 整个补填过程都在抓关键字段（分享链接/耗时/算力豆），故整体置于抓取临界区，避免被观察器切换打断。
  async _refillEmptyFields(out, question) {
    const rounds = this.execution.refillRounds != null ? this.execution.refillRounds : 2;
    for (let r = 0; r < rounds; r++) {
      const missing = this._missingFields(out);
      if (missing.length === 0) return;
      if (this.logger) this.logger.info(`       ↳ 补填缺失字段(第 ${r + 1} 轮): ${missing.join(', ')}`);
      await this._withCritical(async () => {
        for (const f of missing) {
          try {
            if (f === 'conversationShareLink') {
              out.shareLink = await this.extractConversationShareLink();
            } else if (f === 'artifactShareLink') {
              const a = await this.extractArtifactShareLink();
              out.artifactShareLink = a.link; out.hasArtifact = a.hasCard;
            } else if (f === 'duration') {
              const d = await this.extractReportedDuration();
              out.reportedDuration = d.value; out.reportedDurationRaw = d.raw;
            } else if (f === 'beanCost') {
              out.beanCost = (await this.extractBeanCost(question)).value;
            }
          } catch (_) { /* 单字段补填失败不影响其他字段，留待下一轮或最终判缺失 */ }
        }
      });
    }
  }

  // 刷新重抓：就地补填后仍有关键字段为空时，reload 当前对话页（保持在本对话，不新建）再抓一次。
  // 针对实测现象：回答完毕后「耗时(E)」等字段第一时间抓不到，刷新页面后才正常渲染。
  // 返回本次靠刷新才补上的字段名数组（供诊断记录「XX 需刷新才拿到」）。
  async _reloadAndRefill(out, question) {
    if (!this.execution.refillReloadOnce) return [];        // 默认关闭；开启才刷新重抓
    if (this.multiTurn) return [];                          // 多轮会话禁用 reload：会打乱对话 iframe 状态，连累后续轮发送失败
    const before = this._missingFields(out);
    if (before.length === 0) return [];                      // 没缺失，不必刷新
    if (this.logger) this.logger.info(`       ↳ 补填仍缺 ${before.join(', ')}，刷新页面重抓一次...`);
    try {
      await this.page.reload({ waitUntil: 'domcontentloaded', timeout: this.execution.timeout || 60000 });
      this.frame = await this._waitForFrame(this.execution.timeout || 60000);
      await this.page.waitForTimeout(1500); // 等 SPA 恢复该对话内容
    } catch (e) {
      if (this.logger) this.logger.warn(`       ↳ 刷新重抓失败（忽略）: ${e.message}`);
      return [];
    }
    await this._refillEmptyFields(out, question);           // 刷新后再补填
    const after = this._missingFields(out);
    const recovered = before.filter(f => !after.includes(f)); // 刷新前缺、刷新后补上的
    if (recovered.length && this.logger) {
      this.logger.info(`       ↳ 刷新后补上: ${recovered.join(', ')}`);
    }
    return recovered;
  }

  // 供外部巡检：当前任务是否“正在生成/执行中”（读缓存标记，不触发页面操作，避免与抓取抢占）
  isGenerating() {
    return this.generating;
  }

  // 实时探测“生成中”信号（停止/生成按钮可见 = 正在生成）。供巡检与启动确认共用。
  async _probeGenerating() {
    try {
      const stop = this._ctx().locator(this.platform.stopSignalSelector).first();
      return await stop.isVisible();
    } catch { return false; }
  }

  // 抓取临界区包装：进入期间置占用标记，让并发观察器暂停切换（避免打断剪贴板/明细弹窗/tooltip）。
  // 无论成功失败都释放标记。供 extractConversationShareLink/extractArtifactShareLink/
  // extractBeanCost/extractReportedDuration 等“怕被切换打断”的抓取共用。
  async _withCritical(fn) {
    _enterCritical();
    try { return await fn(); }
    finally { _leaveCritical(); }
  }

  // 确认任务“已开始执行”：点发送后等到出现“生成中”信号，或答案气泡开始出现。
  // 期间持续处理可能弹出的确认框。到点仍未启动则抛错，交由重试/失败处理。
  async waitForGenerationStart() {
    const startTimeout = this.execution.generationStartTimeout || 120000;
    const ctx = this._ctx();
    const groups = ctx.locator(this.platform.answerGroupSelector || '.chat-group.assistant');
    const bubbleSel = this.platform.answerSelector;
    const deadline = Date.now() + startTimeout;

    const base = this._baseGroups(); // 多轮：只认「基线之后新增的回答组」为本轮已启动，忽略上一轮旧气泡
    while (Date.now() < deadline) {
      await this._dismissConfirmDialogs(); // 确认框可能拦住任务启动，先点掉
      if (await this._probeGenerating()) { this.generating = true; return; }
      // 兜底：极快返回的任务可能没捕捉到“生成中”，看是否已出现「本轮新增」的真实答案气泡
      try {
        const n = await groups.count();
        if (n > base && bubbleSel && (await groups.nth(n - 1).locator(bubbleSel).count()) > 0) {
          this.generating = true; return;
        }
      } catch {}
      await this.page.waitForTimeout(1000);
    }
    throw new Error('任务发送后未在预期时间内开始执行（未检测到“生成中”信号）');
  }

  // 自动处理执行过程中的弹窗/反问，让任务顺利继续：
  //  · 专家「反问选择题」卡片（work.n.cn 内联 chat-ask-form-card，非 modal）→ 逐题各选一个，点“提交”；
  //  · 「工作流/工具确认」卡片（tool-confirm-modal，Shadow DOM 内）→ 选一个选项，点“提交”；
  //  · 选择题弹窗（modal，需先勾选再提交）→ 逐题各勾一个，点“提交”；
  //  · 纯确认框（反问/授权/风险确认）→ 点“确认/继续/允许…”类按钮。
  // 返回是否对弹窗/反问做了处理（true 表示“有交互待处理”，调用方据此避免误判任务完成）。
  async _dismissConfirmDialogs() {
    // 先处理内联的「专家反问」选择题卡片（最常见，且不是 modal）
    try { if (await this._handleAskForms()) return true; } catch (_) {}
    // 再处理「工作流/工具确认」卡片（tool-confirm-modal，复杂任务规划 Workflow 时弹出）
    try { if (await this._handleToolConfirm()) return true; } catch (_) {}

    const dialogSel = this.platform.confirmDialogSelector;
    if (!dialogSel) return false;
    const ctx = this._ctx();
    try {
      let visible = false;
      try { visible = await ctx.locator(dialogSel).first().isVisible(); } catch {}
      if (!visible) return false;

      const scope = ctx.locator(dialogSel).first();

      // 选择题弹窗：先逐题勾选（每题一个），全部勾完再点“提交”让任务继续
      const choice = await this._selectChoicesInDialog(dialogSel);
      if (choice.hasChoices) {
        if (this.logger && choice.clicked > 0) {
          this.logger.info(`       ↳ [${this.label}] 选择题弹窗：本轮勾选 ${choice.clicked} 项（共 ${choice.groups} 题）`);
        }
        await this.page.waitForTimeout(500); // 勾选后“提交”常从禁用变可用，稍等
        const submitTexts = this.platform.submitButtonTexts ||
          ['提交', '确定', '确认', '完成', '下一步', '继续', '发送', '开始', '执行'];
        if (await this._clickDialogButton(scope, this.platform.submitButtonSelectors, submitTexts, '选择题提交')) {
          return true;
        }
        // 提交按钮没命中，退回下面的确认按钮逻辑
      }

      // 纯确认框：点“确认/继续/允许…”类按钮
      return await this._clickDialogButton(scope,
        this.platform.confirmButtonSelectors, this.platform.confirmButtonTexts, '确认弹窗');
    } catch (_) { /* 弹窗探测/点击失败不影响主流程 */ return false; }
  }

  // 处理「专家反问」内联选择题卡片：逐题各选一个选项（每题选第一个，已选则跳过），全部选完点“提交”。
  // 只要卡片可见就返回 true（表示有反问待处理），让等待循环持续重试直到卡片消失（提交成功）。
  async _handleAskForms() {
    const cardSel = this.platform.askFormSelector || '.chat-ask-form-card, chat-question-form-card';
    const ctx = this._ctx();
    let visible = false;
    try { visible = await ctx.locator(cardSel).last().isVisible(); } catch {}
    if (!visible) return false;

    // 逐题选一个（分组容器 .ask-form__options = 一道题）
    const picked = await this._selectChoicesInDialog(cardSel, {
      optionSel: this.platform.askFormOptionSelector || '.ask-form__option',
      groupSel: this.platform.askFormOptionsGroupSelector || '.ask-form__options',
      selectedHints: this.platform.askFormSelectedHints || ['is-selected']
    });
    if (this.logger && picked.clicked > 0) {
      this.logger.info(`       ↳ [${this.label}] 专家反问：本轮已选 ${picked.clicked} 题（共 ${picked.groups} 题），准备提交`);
    }
    await this.page.waitForTimeout(400); // 选完后“提交”可能才由禁用变可用

    const card = ctx.locator(cardSel).last();
    const submitSel = this.platform.askFormSubmitSelector || '.ask-form__btn--ok';
    const submitSelectors = [submitSel, ...(this.platform.submitButtonSelectors || [])];
    const submitTexts = this.platform.submitButtonTexts || ['提交', '确定', '发送', '完成', '下一步'];
    const ok = await this._clickDialogButton(card, submitSelectors, submitTexts, '专家反问提交');
    if (ok && this.logger) this.logger.info(`       ↳ [${this.label}] 已提交专家反问，任务将继续执行`);
    return true; // 卡片仍在=有反问待处理；提交成功后卡片消失，下一轮自然返回 false
  }

  // 处理「工作流/工具确认」卡片（tool-confirm-modal，如“确认要运行工作流完成该任务吗?”）：
  // 选一个选项（默认首个/已预选，通常是“是，运行工作流”）后点“提交”。该卡片渲染在 open Shadow DOM 内，
  // Playwright 的 CSS locator 可穿透命中，故与普通元素同样处理。单题（卡片本身即选项容器）。
  async _handleToolConfirm() {
    const cardSel = this.platform.toolConfirmSelector || 'tool-confirm-modal, .panel[role="dialog"]';
    const ctx = this._ctx();
    let visible = false;
    try { visible = await ctx.locator(cardSel).last().isVisible(); } catch {}
    if (!visible) return false;

    const optSel = this.platform.toolConfirmOptionSelector || '.opt';
    const hints = this.platform.toolConfirmSelectedHints || ['selected'];
    const card = ctx.locator(cardSel).last();

    // 选一个选项：优先保留已选中的（含 selected 提示 class），否则点第一个
    try {
      const opts = card.locator(optSel);
      const n = await opts.count().catch(() => 0);
      if (n > 0) {
        let already = false;
        for (let i = 0; i < n; i++) {
          const c = (await opts.nth(i).getAttribute('class').catch(() => '')) || '';
          if (hints.some(h => c.includes(h))) { already = true; break; }
        }
        if (!already) {
          await opts.first().click({ timeout: 2000 }).catch(() => {});
          if (this.logger) this.logger.info(`       ↳ [${this.label}] 工作流确认：已选第 1 个选项（共 ${n} 项）`);
        }
        await this.page.waitForTimeout(300);
      }
    } catch (_) {}

    const submitSel = this.platform.toolConfirmSubmitSelector || '.btn-submit';
    const submitSelectors = [submitSel, ...(this.platform.submitButtonSelectors || [])];
    const submitTexts = this.platform.submitButtonTexts || ['提交', '确定', '发送', '完成', '下一步'];
    const ok = await this._clickDialogButton(card, submitSelectors, submitTexts, '工作流确认提交');
    if (ok && this.logger) this.logger.info(`       ↳ [${this.label}] 已提交工作流确认，任务将继续执行`);
    return true; // 卡片仍在=有确认待处理；提交成功后卡片消失，下一轮自然返回 false
  }

  // 在弹窗范围内点一个按钮：先按配置选择器（最稳），再按文案匹配；仅点弹窗内的按钮避免误点页面其他按钮。
  async _clickDialogButton(scope, selectors, texts, tag) {
    for (const sel of (selectors || [])) {
      const btn = scope.locator(sel).first();
      if (await btn.count() > 0 && await btn.isVisible().catch(() => false)) {
        await btn.click({ timeout: 2000 }).catch(() => {});
        if (this.logger) this.logger.info(`       ↳ [${this.label}] 已点击${tag}按钮(${sel})`);
        await this.page.waitForTimeout(400);
        return true;
      }
    }
    for (const text of (texts || [])) {
      const btn = scope.locator(`button:has-text("${text}"), [role="button"]:has-text("${text}")`).first();
      if (await btn.count() > 0 && await btn.isVisible().catch(() => false)) {
        await btn.click({ timeout: 2000 }).catch(() => {});
        if (this.logger) this.logger.info(`       ↳ [${this.label}] 已点击${tag}“${text}”`);
        await this.page.waitForTimeout(400);
        return true;
      }
    }
    return false;
  }

  // 选择题弹窗/反问卡片：在容器内逐题勾选一个选项（每题选第一个未选中的）。返回 {hasChoices, clicked, groups}。
  // 通用兼容：原生 radio/checkbox 优先，无原生时兜底常见自定义控件；分组不依赖 class，用“最近的、
  // 含≥2个选项的祖先”把选项自动聚成一道题（原生 radio 按 name 分组）。
  // opts 可覆盖内置规则：{ optionSel, groupSel, selectedHints }（用于已知结构的反问卡片，最稳）。
  async _selectChoicesInDialog(dialogSel, opts = {}) {
    const nativeSel = opts.optionSel || this.platform.choiceOptionSelector || 'input[type=radio], input[type=checkbox]';
    const customSel = opts.optionSel || this.platform.choiceOptionSelector ||
      '[role=radio],[role=checkbox],[role=option],[role=menuitemradio],[role=menuitemcheckbox],' +
      '.el-radio,.el-checkbox,.ant-radio-wrapper,.ant-checkbox-wrapper,.n-radio,.n-checkbox,' +
      '[class*=option],[class*=choice]';
    const groupSel = opts.groupSel || this.platform.choiceGroupSelector || '';
    const selectedHints = opts.selectedHints ||
      ((this.platform.choiceSelectedHints && this.platform.choiceSelectedHints.length)
        ? this.platform.choiceSelectedHints : ['selected', 'active', 'checked']);
    try {
      return await this._liveFrame().evaluate(
        ({ dialogSel, nativeSel, customSel, groupSel, selectedHints }) => {
          const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return false;
            const st = getComputedStyle(el);
            return st.visibility !== 'hidden' && st.display !== 'none';
          };
          const classStr = (el) => {
            const c = el.className;
            return (typeof c === 'string' ? c : (c && c.baseVal) || '').toLowerCase();
          };
          const isChecked = (el) => {
            if (!el) return false;
            if (el.matches('input')) return !!el.checked;
            const ac = el.getAttribute('aria-checked') || el.getAttribute('aria-selected');
            if (ac === 'true') return true;
            if (selectedHints.some(h => classStr(el).includes(h))) return true;
            const inp = el.querySelector('input[type=radio],input[type=checkbox]');
            return !!(inp && inp.checked);
          };

          const dialogs = [...document.querySelectorAll(dialogSel)].filter(isVisible);
          const dialog = dialogs[dialogs.length - 1]; // 取最上层可见弹窗
          if (!dialog) return { hasChoices: false, clicked: 0, groups: 0 };

          // 原生 radio/checkbox 优先；无原生再用自定义控件
          let raw = [...dialog.querySelectorAll(nativeSel)].filter(isVisible);
          const usingNative = raw.length > 0;
          if (!usingNative) raw = [...dialog.querySelectorAll(customSel)].filter(isVisible);
          // 只留“叶子”选项：排除本身还包含其他候选的容器（如 radio-group、含 input 的 label）
          const leaves = raw.filter(op => !raw.some(o => o !== op && op.contains(o)));
          // 判定是否为“选择题”：有原生控件即是；纯自定义则要求≥2个选项，避免把确认框里的装饰元素误当选项
          if (leaves.length === 0 || (!usingNative && leaves.length < 2)) {
            return { hasChoices: false, clicked: 0, groups: 0 };
          }

          // 分组：radio 按 name；否则取“最近的、含≥2个选项的祖先”（或匹配 groupSel 的祖先）为一题
          const groupKey = (el) => {
            const inp = el.matches('input') ? el : el.querySelector('input[type=radio]');
            if (inp && inp.type === 'radio' && inp.name) return 'name:' + inp.name;
            let cur = el.parentElement;
            while (cur && cur !== dialog.parentElement) {
              if (groupSel) { if (cur.matches(groupSel)) return cur; }
              else if (leaves.filter(l => cur.contains(l)).length >= 2) return cur;
              cur = cur.parentElement;
            }
            return 'dialog';
          };

          const groups = new Map();
          for (const op of leaves) {
            const key = groupKey(op);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(op);
          }

          let clicked = 0;
          for (const ops of groups.values()) {
            if (ops.some(isChecked)) continue; // 该题已选，跳过
            const leaf = ops[0];
            let clickEl = leaf; // 原生 input 常被样式隐藏，点其外层 label 更能触发切换
            if (leaf.matches('input')) { const lbl = leaf.closest('label'); if (lbl) clickEl = lbl; }
            try { clickEl.scrollIntoView({ block: 'center' }); } catch (e) {}
            try { clickEl.click(); clicked++; } catch (e) {}
          }
          return { hasChoices: true, clicked, groups: groups.size };
        },
        { dialogSel, nativeSel, customSel, groupSel, selectedHints }
      );
    } catch (_) {
      return { hasChoices: false, clicked: 0, groups: 0 };
    }
  }

  // 等待回答完成。这些是多阶段 Agent 任务（思考/工具/产物），可能跑很久。
  // 完成的“强信号”是本次消耗 footer 出现（仅在回答彻底结束后才渲染）；
  // 辅以停止态(生成中) + 真实答案气泡文本稳定，避免在“思考中”阶段过早提取。
  // 等待回答完成，返回 { completed, reason }。多阶段 Agent 任务（思考/工具/产物）可能跑很久。
  // 完成强信号=消耗 footer 出现（仅回答彻底结束才渲染），辅以真实答案气泡文本稳定。
  // 未完成会区分原因：timeout（等满上限）/ stall（长时间无进展，多为卡在无法处理的弹窗）/ nostart（迟迟不启动）。
  async waitForResponseComplete() {
    const timeout = this.execution.responseTimeout || 1200000;
    const startTimeout = this.execution.generationStartTimeout || 120000;
    const stallTimeout = this.execution.stallTimeout || 0;
    const ctx = this._ctx();
    const stop = ctx.locator(this.platform.stopSignalSelector).first();
    const footer = this.platform.costSelector ? ctx.locator(this.platform.costSelector).last() : null;
    const groups = ctx.locator(this.platform.answerGroupSelector || '.chat-group.assistant');
    const bubbleSel = this.platform.answerSelector;

    const baseGroups = this._baseGroups();   // 多轮：本轮回答是「第 baseGroups 组之后新增的组」
    const baseFooters = this._baseFooters();  // 多轮：本轮完成信号是「footer 数超出基线」才算
    const deadline = Date.now() + timeout;
    const startDeadline = Date.now() + startTimeout;
    // 进入本方法前已由 waitForGenerationStart 确认任务已启动，故 sawGenerating 起始即为 true，
    // 避免“抓取瞬间恰好没看到生成中信号”被误判为“迟迟不启动”而提前退出。
    let sawGenerating = true, lastText = null, stable = 0;
    let lastProgressAt = Date.now(); // 最近一次“有进展”（生成中 / 处理了弹窗 / 答案文本变化）的时刻
    const finish = (completed, reason) => { this.generating = false; return { completed, reason }; };

    while (Date.now() < deadline) {
      // 执行过程中可能弹确认框 / 专家反问选择题，发现即处理（勾选并提交）让任务继续。
      // 只要本轮处理了交互，就重置稳定计数并跳过“完成判定”，避免把反问前的引导文本误判为最终答案。
      if (await this._dismissConfirmDialogs()) {
        stable = 0; lastText = null; this.generating = true; lastProgressAt = Date.now();
        await this.page.waitForTimeout(1500);
        continue;
      }

      let generating = false;
      try { generating = await stop.isVisible(); } catch {}
      this.generating = generating; // 维护巡检可见的“正在执行”标记
      if (generating) { sawGenerating = true; stable = 0; lastText = null; lastProgressAt = Date.now(); await this.page.waitForTimeout(1500); continue; }

      // 完成强信号：消耗 footer 出现（“本次回答消耗…”仅在结束后才有）。
      // 多轮：要求 footer 数超出基线，才是「本轮」新增的完成信号（否则命中的是上一轮旧 footer）。
      if (footer) {
        let footerN = 0;
        try { footerN = await ctx.locator(this.platform.costSelector).count(); } catch {}
        if (footerN > baseFooters) { await this.page.waitForTimeout(1000); return finish(true, 'footer'); }
      }

      // 兜底：真实答案气泡出现且文本连续两次稳定（“思考中”不是答案气泡，不会误判）。
      // 多轮：必须是「基线之后新增的组」（n > baseGroups），否则读到的是上一轮旧气泡的稳定文本。
      let txt = '', hasBubble = false;
      try {
        const n = await groups.count();
        if (n > baseGroups) {
          const g = groups.nth(n - 1);
          if (bubbleSel) hasBubble = (await g.locator(bubbleSel).count()) > 0;
          txt = (await g.innerText().catch(() => '')).trim();
        }
      } catch {}

      if (txt && txt !== lastText) lastProgressAt = Date.now(); // 答案文本在变=有进展

      if (!sawGenerating && !txt) {
        if (Date.now() > startDeadline) return finish(false, 'nostart'); // 迟迟不启动
        await this.page.waitForTimeout(1000);
        continue;
      }
      if (hasBubble && txt && txt === lastText) { stable++; if (stable >= 2) return finish(true, 'stable'); }
      else stable = 0;
      lastText = txt;

      // 停滞快速失败：长时间既非生成中、又无 footer、又无真实答案气泡（多为卡在无法自动处理的弹窗/异常），
      // 不必干等到 responseTimeout。流式输出中 has-copy 气泡的 txt 会变、lastProgressAt 持续刷新，不会误判。
      if (stallTimeout && !hasBubble && (Date.now() - lastProgressAt > stallTimeout)) {
        return finish(false, 'stall');
      }

      await this.page.waitForTimeout(1500);
    }
    return finish(false, 'timeout'); // 等满 responseTimeout 仍无完成信号
  }

  // 返回 { text, fromBubble }。fromBubble=true 表示取自真实答案气泡（.chat-bubble.has-copy）；
  // false=回退到整组文本（可能抓到“思考中…”思考态），供上层判定是否为有效最终答案。
  async extractLastAnswer() {
    const ctx = this._ctx();
    const groups = ctx.locator(this.platform.answerGroupSelector || '.chat-group.assistant');
    const count = await groups.count();
    // 多轮：本轮回答是「基线之后新增的组」；若组数没超过基线，说明本轮回答还没渲染出来，返回空
    // （避免误抓上一轮的旧答案）。最后一组即本轮回答（本轮已完成，新组排在末尾）。
    if (count === 0 || count <= this._baseGroups()) return { text: '', fromBubble: false };
    const lastGroup = groups.nth(count - 1);
    const bubble = lastGroup.locator(this.platform.answerSelector);
    if (await bubble.count() > 0) {
      return { text: (await bubble.last().innerText()).trim(), fromBubble: true };
    }
    return { text: (await lastGroup.innerText()).trim(), fromBubble: false };
  }

  // 正文（规范文本）：点「本轮回答组」下方操作区的「复制」按钮，从系统剪贴板取平台规范化的正文
  // （带 Markdown 格式/代码块/表格，比 DOM innerText 准，也不夹带 UI 文字）。取不到返回 ''（调用方沿用 DOM 文本）。
  // 复制经系统剪贴板（全机共享），故走 _shareLock 串行化，避免并发多任务互相覆盖剪贴板（与分享链接同理）。
  async extractAnswerViaCopy() {
    const btnSel = this.platform.answerCopyBtnSelector;
    if (!btnSel) return '';
    const ctx = this._ctx();
    const groupSel = this.platform.answerGroupSelector || '.chat-group.assistant';
    const run = async () => {
      try {
        const groups = ctx.locator(groupSel);
        const n = await groups.count().catch(() => 0);
        // 只在「基线之后新增的本轮回答组」里点复制，避免多轮抓到上一轮旧答案（与 extractLastAnswer 同口径）。
        if (n === 0 || n <= this._baseGroups()) return '';
        const lastGroup = groups.nth(n - 1);
        const btn = lastGroup.locator(btnSel).first();
        if (await btn.count().catch(() => 0) === 0) return '';

        // 复制按钮多为 hover 才显形：先 hover 该回答组再点。写哨兵到剪贴板，点后轮询剪贴板变化。
        for (let round = 0; round < 3; round++) {
          try { await lastGroup.hover({ timeout: 2000 }); } catch {}
          await this._clipSentinel();
          await btn.click({ timeout: 4000, force: true }).catch(() => {});
          const deadline = Date.now() + 4000;
          while (Date.now() < deadline) {
            await this.page.waitForTimeout(400);
            let clip = '';
            try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch {}
            if (clip && clip !== '__WAIT_SHARE__') return String(clip).trim(); // 正文可含链接/多行，整体取用
          }
        }
        return '';
      } catch { return ''; }
    };
    const p = _shareLock.then(run, run);
    _shareLock = p.catch(() => {});
    return p;
  }

  // 附加：本次回答 tokens（footer 文本“本次回答消耗：8.5K tokens”），仅记录到 JSON
  async extractCost() {
    const sel = this.platform.costSelector;
    if (!sel) return { cost: '', raw: '' };
    const ctx = this._ctx();
    try {
      const el = ctx.locator(sel).last();
      if (await el.count() === 0) return { cost: '', raw: '' };
      const raw = (await el.innerText()).trim();
      let cost = '';
      if (this.execution.costRegex) {
        const m = raw.match(new RegExp(this.execution.costRegex));
        if (m && m[1]) cost = m[1].trim();
      }
      return { cost, raw };
    } catch {
      return { cost: '', raw: '' };
    }
  }

  // E 耗时（统一入口，对外接口不变，Web/桌面两端共用）：优先读回答上方「思考折叠条」的状态文本
  // （如“已完成 2分43秒”）——任务一完成即渲染成 DOM，最稳、无需 hover、也无需刷新页面；
  // 拿不到再退回原 tooltip 方式（hover 小问号→轮询 ui-tooltip 的 content）。返回 { value, raw }。
  async extractReportedDuration() {
    // 统一把抓到的人类可读时长（“2分43秒”/“耗时10秒”）换算成「秒」再回填；换算不出则原样保留、不丢数据。
    const toSec = (r) => {
      const s = this._durationToSeconds(r.value);
      return s != null ? { value: String(s), raw: r.raw } : r;
    };
    const fromBar = await this._extractDurationFromStatusBar();
    if (fromBar.value) return toSec(fromBar);
    const fromTip = await this._extractDurationFromTooltip();
    if (fromTip.value) return toSec(fromTip);
    // 两路都没取到耗时值：优先返回状态条原文（多为仍“思考中”/本条无折叠条），便于排查它当时是什么。
    return fromBar.raw ? fromBar : fromTip;
  }

  // 把「X小时Y分Z秒」中文 / 「5m 2s」「1h5m2s」英文 / hh:mm:ss / 纯秒数 等时长统一换算成总秒数；
  // 解析不出返回 null。例：“2分43秒”→163、“5m 2s”→302、tooltip“10秒”→10，保证 E 列口径一致（纯秒数值）。
  _durationToSeconds(text) {
    const s = ('' + (text || '')).trim();
    if (!s) return null;
    // ① hh:mm:ss / mm:ss 冒号格式（兜底）
    if (/^\d{1,2}(?::\d{1,2}){1,2}$/.test(s)) {
      return s.split(':').reduce((acc, n) => acc * 60 + parseInt(n, 10), 0);
    }
    // ② 时/分/秒任意组合（各段可缺省）：中文 小时/时·分·秒，英文 h·m·s（如“5m 2s”“1h5m2s”）。
    //    英文单位用「后不接字母」lookahead 收尾——避免把 500ms(毫秒) 的 m 误当分钟、s 误当秒。
    let total = 0, matched = false;
    const h = s.match(/(\d+(?:\.\d+)?)\s*(?:小时|小時|時|时|h(?![a-z]))/i);
    if (h) { total += parseFloat(h[1]) * 3600; matched = true; }
    const m = s.match(/(\d+(?:\.\d+)?)\s*(?:分钟|分|m(?![a-z]))/i);
    if (m) { total += parseFloat(m[1]) * 60; matched = true; }
    const c = s.match(/(\d+(?:\.\d+)?)\s*(?:秒|s(?![a-z]))/i);
    if (c) { total += parseFloat(c[1]); matched = true; }
    if (matched) return Math.round(total);
    // ③ 纯数字：视为已是秒
    if (/^\d+(?:\.\d+)?$/.test(s)) return Math.round(parseFloat(s));
    return null;
  }

  // E-新入口：回答上方「思考折叠条」label（.chat-thinking-toggle__label，文本如“已完成 2分43秒”）。
  // 只在「本轮回答组」（最后一组 assistant）内找，避免多轮时抓到上一轮的旧耗时；一组内有多个折叠时
  // 取“已完成”态那个。用一次 evaluate 直接读 textContent（lit 渲染的 <!--?lit$…--> 注释节点会被自动忽略）。
  async _extractDurationFromStatusBar() {
    const sel = this.platform.statusDurationSelector;
    if (!sel) return { value: '', raw: '' };
    const groupSel = this.platform.answerGroupSelector || '.chat-group.assistant';
    const doneMarker = this.execution.statusDoneMarker || '已完成';
    const deadline = Date.now() + (this.execution.statusDurationTimeout || 8000);
    let raw = '';
    try {
      // 状态条答完即在，但文本可能有一两秒从“思考中”过渡到“已完成 X”；短轮询等“已完成”出现。
      while (Date.now() < deadline) {
        raw = await this._liveFrame().evaluate(({ groupSel, sel, doneMarker }) => {
          const groups = document.querySelectorAll(groupSel);
          if (!groups.length) return '';
          const g = groups[groups.length - 1]; // 本轮回答组=最新一组（排在末尾）
          const labels = [...g.querySelectorAll(sel)];
          if (!labels.length) return '';
          const done = labels.find(el => (el.textContent || '').includes(doneMarker));
          return ((done || labels[labels.length - 1]).textContent || '').trim();
        }, { groupSel, sel, doneMarker });
        if (raw && raw.includes(doneMarker)) break;
        await this.page.waitForTimeout(800);
      }
    } catch { return { value: '', raw: '' }; }
    if (!raw || !raw.includes(doneMarker)) return { value: '', raw }; // 没到“已完成”态：交给 tooltip 兜底
    let value = raw;
    if (this.execution.statusDurationRegex) {
      const m = raw.match(new RegExp(this.execution.statusDurationRegex));
      if (m && m[1]) value = m[1].trim();
    }
    return { value, raw };
  }

  // E-兜底：原 tooltip 方式。耗时是答完后几秒才追加进 ui-tooltip 的 content，故 hover 触发后轮询到出现“耗时”。
  async _extractDurationFromTooltip() {
    const sel = this.platform.durationAttrSelector;
    const attr = this.platform.durationAttrName || 'content';
    if (!sel) return { value: '', raw: '' };
    const ctx = this._ctx();
    try {
      const el = ctx.locator(sel).last();
      if (await el.count() === 0) return { value: '', raw: '' };
      if (this.platform.durationHoverSelector) {
        try { await ctx.locator(this.platform.durationHoverSelector).last().hover({ timeout: 2000 }); } catch {}
      }
      let raw = '';
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        raw = ((await el.getAttribute(attr).catch(() => '')) || '').trim();
        if (/耗时/.test(raw)) break;
        await this.page.waitForTimeout(1000);
      }
      if (!/耗时/.test(raw)) return { value: '', raw }; // 没等到就留空，不回退墙钟
      let value = raw;
      if (this.execution.durationRegex) {
        const m = raw.match(new RegExp(this.execution.durationRegex));
        if (m && m[1]) value = m[1].trim();
      }
      return { value, raw };
    } catch {
      return { value: '', raw: '' };
    }
  }

  // ---- 剪贴板小工具（分享链接经系统剪贴板；iframe 内读被策略拦截，故用主 frame 读写）----
  async _clipSentinel() {
    await this.page.evaluate(() => navigator.clipboard.writeText('__WAIT_SHARE__')).catch(() => {});
  }
  async _pollClipboardUrl(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await this.page.waitForTimeout(600);
      let clip = '';
      try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch {}
      if (clip && clip !== '__WAIT_SHARE__') {
        const m = String(clip).match(/https?:\/\/[^\s]+/);
        if (m) return m[0];
      }
    }
    return '';
  }

  // C 对话分享链接：点“分享”→“全选”→“生成链接”，链接写入剪贴板，主 frame 读取。
  async extractConversationShareLink() {
    const btnSel = this.platform.shareBtnSelector;
    const genSel = this.platform.shareGenerateSelector;
    if (!btnSel || !genSel) return '';
    const ctx = this._ctx();
    const run = async () => {
      let lastReason = '未知';
      try {
        // 先清理可能开着的产物预览/弹窗，避免挡住顶栏分享按钮
        await this.page.keyboard.press('Escape').catch(() => {});
        await this.page.waitForTimeout(300);

        const boxSel = this.platform.shareCheckboxSelector || '.chat-share-panel__checkbox';
        const allChecked = () => this._liveFrame().evaluate((s) => {
          const boxes = [...document.querySelectorAll(s)];
          return boxes.length > 0 && boxes.every(b => !!b.querySelector('path'));
        }, boxSel);

        // 打开面板→确保全选→生成链接→读剪贴板；面板偶发打不开/不复制，失败就重开重试
        for (let round = 0; round < 3; round++) {
          const btn = ctx.locator(btnSel).first();
          if (await btn.count() === 0) {
            lastReason = `分享按钮未找到(${btnSel})`;
            this._warnShare(lastReason); return '';   // 按钮都没有,重试无益,直接返回
          }
          await btn.click({ timeout: 5000 }).catch(() => {});

          const gen = ctx.locator(genSel).first();
          try { await gen.waitFor({ state: 'visible', timeout: 5000 }); }
          catch { lastReason = `分享面板未打开(${genSel} 不可见)`; await this.page.waitForTimeout(500); continue; } // 面板没开，重试

          // 勾选全部内容再生成链接（用户明确流程）：主动点「全选」，点到全部勾选为止——
          // 多轮对话一次全选常只到部分选中，需再点一次（ensureAllSelected 自适应单轮 1 次/多轮 2 次）。
          const selectAllSel = this.platform.shareSelectAllSelector;
          if (selectAllSel) {
            await ensureAllSelected({
              isAllChecked: allChecked,
              clickSelectAll: () => ctx.locator(selectAllSel).first().click({ timeout: 3000 }).catch(() => {}),
              sleep: (ms) => this.page.waitForTimeout(ms),
            });
          }
          // 兜底：全选后仍有未勾的（无全选按钮/选择器差异），逐个补勾（选中项含 <path> 勾）
          if (!(await allChecked())) {
            const boxes = ctx.locator(boxSel);
            const nb = await boxes.count().catch(() => 0);
            for (let i = 0; i < nb; i++) {
              const box = boxes.nth(i);
              let checked = true;
              try { checked = await box.evaluate(el => !!el.querySelector('path')); } catch {}
              if (!checked) { await box.click({ timeout: 2000 }).catch(() => {}); await this.page.waitForTimeout(150); }
            }
          }

          await this._clipSentinel();
          await gen.click({ timeout: 5000 }).catch(() => {});      // 生成链接
          const link = await this._pollClipboardUrl(10000);        // 读剪贴板回填
          if (link) { await this.page.keyboard.press('Escape').catch(() => {}); return link; }
          lastReason = '生成链接后剪贴板未出现 URL(生成失败或剪贴板读取受限)';
          await this.page.keyboard.press('Escape').catch(() => {});
          await this.page.waitForTimeout(500);
        }
        this._warnShare(`${lastReason}(重试 3 轮仍空)`);   // 失败不再静默:记原因供真机定位
        return '';
      } catch (e) {
        this._warnShare(`抓取异常: ${(e.message || '').split('\n')[0]}`);
        await this.page.keyboard.press('Escape').catch(() => {});
        return '';
      }
    };
    const p = _shareLock.then(run, run);
    _shareLock = p.catch(() => {});
    return p;
  }

  // 会话分享链接抓取失败时打一条 warn(带任务标识)。原实现双重静默吞错,真机无从定位「有些没链接」。
  _warnShare(msg) {
    if (this.logger) this.logger.warn(`       ↳ [${this.label}] 会话分享链接未抓到:${msg}`);
  }

  // D 产物分享链接：打开预览（产物卡片 或 顶栏「预览」按钮）→点「分享」→在分享弹窗「分享文件」抓永久链接。
  // 新版弹窗（section.share-link__modal）：链接异步生成在只读输入框 input.share-link__url，顶部
  // 「通过链接可访问内容」开关默认开启；优先直读 input 的 value（不碰剪贴板、不受并发污染），直读不到
  // 再点「复制链接」按钮读剪贴板兜底。旧版弹窗（创建链接+有效期）在新弹窗未出现时作为回退兜底。
  // 返回 { link, hasCard }：hasCard 以「预览分享入口是否出现」为准，区分「本来无产物」与「有产物却没抓到」。
  async extractArtifactShareLink() {
    const cardSel = this.platform.artifactCardSelector;
    const shareSel = this.platform.artifactShareActionSelector;
    const previewBtnSel = this.platform.artifactPreviewBtnSelector; // 顶栏「预览」按钮（备选打开途径）
    const modalSel = this.platform.artifactShareModalSelector || '.share-link__modal';    // 分享弹窗容器
    const urlSel = this.platform.artifactShareUrlSelector || 'input.share-link__url';     // 只读链接输入框
    const copySel = this.platform.artifactCopyLinkSelector || '.share-link__action--primary'; // 「复制链接」按钮
    const switchSel = this.platform.artifactAccessSwitchSelector || '.share-link__switch';     // 访问开关
    const onHint = this.platform.artifactAccessOnHint || '#icon-share-link-switch-on-track';   // 开关 on 态信号(svg use href)
    const createSel = this.platform.artifactCreateLinkSelector;     // 旧版「创建链接」（回退兜底）
    if (!cardSel || !shareSel) return { link: '', hasCard: false };
    const ctx = this._ctx();
    const run = async () => {
      let hasCard = false;
      try {
        // 确保预览已打开（分享入口出现）。打开途径优先产物卡片，其次顶栏「预览」按钮。
        let share = ctx.locator(shareSel).first();
        if ((await share.count()) > 0) {
          hasCard = true; // 预览已开着
        } else {
          const card = ctx.locator(cardSel).first();
          const previewBtn = previewBtnSel ? ctx.locator(previewBtnSel).first() : null;
          const cardN = await card.count().catch(() => 0);
          const btnN = previewBtn ? await previewBtn.count().catch(() => 0) : 0;
          if (cardN === 0 && btnN === 0) return { link: '', hasCard: false }; // 无任何产物入口 = 无产物
          // 有卡片/预览按钮：轮询点击直到分享入口出现（预览完成后会自动收起，故必要时重点）
          const openDeadline = Date.now() + 12000;
          while (Date.now() < openDeadline && (await share.count()) === 0) {
            if (cardN > 0) await card.click({ timeout: 4000, force: true }).catch(() => {});
            else if (previewBtn) await previewBtn.click({ timeout: 4000, force: true }).catch(() => {});
            await this.page.waitForTimeout(1500);
            share = ctx.locator(shareSel).first();
          }
          hasCard = (await share.count()) > 0; // 以分享入口是否出现为准：出现=确有产物
        }
        if (await share.count() === 0) return { link: '', hasCard };

        // ② 打开分享弹窗 → 抓链接。弹窗没开才点「分享」（弹窗已开时再点会把它关掉）。
        for (let round = 0; round < 3; round++) {
          const modal = ctx.locator(modalSel).first();
          const modalOpen = (await modal.count().catch(() => 0)) > 0 && (await modal.isVisible().catch(() => false));
          if (!modalOpen) await share.click({ timeout: 5000, force: true }).catch(() => {});
          try { await modal.waitFor({ state: 'visible', timeout: 6000 }); }
          catch {
            // 新弹窗没出现：可能平台回退到旧 UI，退回旧「创建链接+永久有效」流程试一次
            const legacy = await this._grabArtifactLinkLegacy(ctx, createSel);
            if (legacy) { await this.page.keyboard.press('Escape').catch(() => {}); return { link: legacy, hasCard: true }; }
            await this.page.waitForTimeout(500); continue;
          }

          // 抓链接前先确认「通过链接可访问内容」开关处于 on 态（track 的 svg use href 含 on-track）——
          // 该平台开关/选中态的可靠信号在 svg use 上，class/aria 不稳（见 nami-share-dialog-selection-signal）；
          // 未开则点开、轮询等 on 态。开关开启后链接才生成，故直读与点复制前都要 ensureOn。
          const ensureOn = () => this._ensureShareAccessOn(modal, switchSel, onHint);
          await ensureOn();

          // 抓链接：优先直读 input.share-link__url 的 value，直读不到再点「复制链接」（点前再确认 on 态）读剪贴板。
          const link = await this._grabModalShareUrl(modal, urlSel, copySel, ensureOn);
          if (link) { await this.page.keyboard.press('Escape').catch(() => {}); return { link, hasCard: true }; }

          await this.page.keyboard.press('Escape').catch(() => {});
          await this.page.waitForTimeout(800);
        }
        await this.page.keyboard.press('Escape').catch(() => {});
        return { link: '', hasCard };
      } catch {
        await this.page.keyboard.press('Escape').catch(() => {});
        return { link: '', hasCard };
      }
    };
    const p = _shareLock.then(run, run);
    _shareLock = p.catch(() => {});
    return p;
  }

  // 新分享弹窗抓链接：链接异步生成在 input.share-link__url（生成中 aria-busy=true / value 空）。
  // 优先轮询直读 input 的 value（不碰剪贴板、不受并发污染，最稳）；直读不到再点「复制链接」按钮，
  // 点后继续「直读 input + 读剪贴板」双路取链接。全程不 Esc（关弹窗会中断生成）。
  // ensureOn（可选）：点「复制链接」前调用，确认访问开关处于 on 态（否则链接不可访问/不生成）。
  async _grabModalShareUrl(modal, urlSel, copySel, ensureOn) {
    const readInput = async () => {
      if (!urlSel) return '';
      try {
        const inp = modal.locator(urlSel).first();
        if ((await inp.count().catch(() => 0)) === 0) return '';
        if ((await inp.getAttribute('aria-busy').catch(() => '')) === 'true') return ''; // 生成中，还没出链接
        const val = ((await inp.inputValue().catch(() => '')) || '').trim();
        const m = val.match(/https?:\/\/\S+/);
        return m ? m[0] : '';
      } catch { return ''; }
    };
    // 1) 先直读若干秒（链接打开弹窗后多为自动异步生成）
    const directDeadline = Date.now() + 15000;
    while (Date.now() < directDeadline) {
      const v = await readInput();
      if (v) return v;
      await this.page.waitForTimeout(800);
    }
    // 2) 直读拿不到 → 点「复制链接」触发/复制，再「直读 input + 读剪贴板」双路长轮询
    if (copySel) {
      const copyBtn = modal.locator(copySel).first();
      if ((await copyBtn.count().catch(() => 0)) > 0) {
        // 点「复制链接」前再确认开关处于 on 态（svg use href 含 on-track），未开则先开——按需求严格前置。
        if (ensureOn) await ensureOn();
        await this._clipSentinel();
        await copyBtn.click({ timeout: 5000, force: true }).catch(() => {});
        const deadline = Date.now() + 20000;
        while (Date.now() < deadline) {
          const v = await readInput();
          if (v) return v;
          let clip = '';
          try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch {}
          if (clip && clip !== '__WAIT_SHARE__') { const m = String(clip).match(/https?:\/\/\S+/); if (m) return m[0]; }
          await this.page.waitForTimeout(700);
        }
      }
    }
    return '';
  }

  // 确认/开启「通过链接可访问内容」开关，判据是 track 的 svg <use> href 是否含 on 态标识（onHint，
  // 默认 #icon-share-link-switch-on-track）——该平台开关状态的可靠信号在 svg use 上，class/aria 不稳
  // （见 nami-share-dialog-selection-signal）。已是 on 态直接返回；未开则点开并轮询等 on 态（最多点 3 次）。
  async _ensureShareAccessOn(modal, switchSel, onHint) {
    if (!switchSel) return true;                              // 未配开关选择器 → 视为无需处理
    const sw = modal.locator(switchSel).first();
    if ((await sw.count().catch(() => 0)) === 0) return true; // 弹窗无此开关 → 跳过
    const hint = onHint || '#icon-share-link-switch-on-track';
    // 读开关内所有 svg use 的 href（含 xlink:href 兼容），任一含 onHint = 已开启（on 态）。
    const isOn = async () => {
      try {
        return await sw.evaluate((el, h) => {
          for (const u of el.querySelectorAll('use')) {
            const href = u.getAttribute('href') || u.getAttribute('xlink:href') || '';
            if (href.includes(h)) return true;
          }
          return false;
        }, hint);
      } catch { return false; }
    };
    if (await isOn()) return true;
    // 未开：点开，轮询等 on 态出现（点击切换有动画/异步）
    for (let i = 0; i < 3; i++) {
      await sw.click({ timeout: 3000 }).catch(() => {});
      const deadline = Date.now() + 3000;
      while (Date.now() < deadline) {
        await this.page.waitForTimeout(400);
        if (await isOn()) {
          if (this.logger) this.logger.info(`       ↳ [${this.label}] 分享访问开关已确认为 on 态`);
          return true;
        }
      }
    }
    if (this.logger) this.logger.warn(`       ↳ [${this.label}] 分享访问开关未能确认为 on 态（链接可能不可访问）`);
    return false;
  }

  // 旧版分享弹窗兜底：点「创建链接」（先切「永久有效」）→ 轮询剪贴板。仅当新弹窗未出现时调用一次。
  async _grabArtifactLinkLegacy(ctx, createSel) {
    if (!createSel) return '';
    const create = ctx.locator(createSel).first();
    if ((await create.count().catch(() => 0)) === 0) return '';
    const permanentSel = this.platform.artifactPermanentSelector;
    const permanentText = this.platform.artifactPermanentText || '永久有效';
    try {
      // 切「永久有效」（旧弹窗默认「7天内有效」，不切拿到的是 7 天限时链接）
      if (permanentSel) {
        const title = ctx.locator(permanentSel).filter({ hasText: permanentText }).first();
        if ((await title.count().catch(() => 0)) > 0) {
          let clickTarget = title.locator('xpath=ancestor-or-self::*[contains(@class,"option") or @role="radio"][1]').first();
          if ((await clickTarget.count().catch(() => 0)) === 0) clickTarget = title;
          await clickTarget.click({ timeout: 3000, force: true }).catch(() => {});
          await this.page.waitForTimeout(400);
        }
      }
      await create.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      await this._clipSentinel();
      await create.click({ timeout: 5000, force: true }).catch(() => {});
      // 确认创建已启动（is-loading / 弹窗关闭 / 剪贴板已出链接），再长轮询（期间不要 Esc）
      let started = false, early = '';
      for (let k = 0; k < 4 && !started; k++) {
        await this.page.waitForTimeout(1000);
        try {
          const cls = (await create.getAttribute('class').catch(() => '')) || '';
          if (/is-loading/.test(cls) || (await create.count()) === 0) started = true;
        } catch { started = true; }
        let clip = '';
        try { clip = await this.page.evaluate(() => navigator.clipboard.readText()); } catch {}
        if (clip && clip !== '__WAIT_SHARE__') { const m = String(clip).match(/https?:\/\/\S+/); if (m) { early = m[0]; break; } }
      }
      if (early) return early;
      if (!started) return '';
      return await this._pollClipboardUrl(90000);
    } catch { return ''; }
  }

  // 关闭「算力豆明细」面板（config-modal 设置弹窗）。实测：该弹窗 Escape 关不掉，
  // 必须点右上角关闭按钮 .config-modal__close；关不掉会遮住左侧任务列表，导致后续任务定位失败。
  // 返回是否已关闭（面板已不在）。selector 可经 platform.ledgerCloseSelector 覆盖。
  async _closeLedgerPanel(ctx) {
    const closeSel = this.platform.ledgerCloseSelector || '.config-modal__close';
    const rowSel = this.platform.ledgerRowSelector || '.coin-info__row';
    const isOpen = async () =>
      (await ctx.locator('config-modal[open]').count().catch(() => 0)) > 0 ||
      (await ctx.locator(rowSel).count().catch(() => 0)) > 0;
    if (!(await isOpen())) return true;
    // 点关闭按钮（最多 3 次，兼容动画/首次点击被吞）；每次点后校验是否真关掉
    for (let i = 0; i < 3; i++) {
      const btn = ctx.locator(closeSel).first();
      if (await btn.count().catch(() => 0) > 0) {
        await btn.click({ timeout: 3000, force: i > 0 }).catch(() => {});
        await this.page.waitForTimeout(400);
        if (!(await isOpen())) return true;
      } else break;
    }
    // 兜底：Escape（对个别其它弹窗有效；对 config-modal 无效但无害）
    await this.page.keyboard.press('Escape').catch(() => {});
    await this.page.waitForTimeout(300);
    return !(await isOpen());
  }

  // 打开「头像→明细」算力豆账本面板，并等明细行渲染出来。返回是否成功打开（行已出现）。
  // 稳健化要点（针对回填失败）：①先关掉残留弹窗/上次的明细面板，保证读到的是刷新后的最新记账、
  // 且头像不被遮住；②点头像后「等『明细』入口真正可见」再点（不再用固定等待），点不出就再点一次
  // （兼容 toggle/慢动画/首次坐标点击被吞）；③点「明细」后轮询等账本行渲染，不在空列表上直接读。
  async _openLedgerPanel(ctx, avSel, entrySel, rowSel) {
    // 先关掉可能残留的弹窗/明细面板（上次抓取或分享弹窗没关干净会遮住头像、或让你读到旧数据）。
    // config-modal 明细面板 Escape 关不掉，须走 _closeLedgerPanel 点关闭按钮。
    if ((await ctx.locator(rowSel).count().catch(() => 0)) > 0) await this._closeLedgerPanel(ctx);
    // 点头像展开菜单 → 等「明细」入口可见；不可见就再点一次（首次坐标点击可能没触发/被动画吞掉/被遮）
    const entry = ctx.locator(entrySel).first();
    let entryReady = false;
    for (let i = 0; i < 3 && !entryReady; i++) {
      await ctx.locator(avSel).first().click({ timeout: 4000, force: i > 0 }).catch(() => {});
      try { await entry.waitFor({ state: 'visible', timeout: 2500 }); entryReady = true; }
      catch { await this.page.keyboard.press('Escape').catch(() => {}); await this.page.waitForTimeout(300); }
    }
    if (!entryReady) return false;
    await entry.click({ timeout: 4000 }).catch(() => {});
    // 轮询等账本行渲染（记账/网络延迟下行会晚出现），出现即成功；到点仍无行则视为失败（交由外层重开重试）。
    const deadline = Date.now() + (this.execution.ledgerRowsTimeoutMs || 8000);
    while (Date.now() < deadline) {
      if ((await ctx.locator(rowSel).count().catch(() => 0)) > 0) return true;
      await this.page.waitForTimeout(400);
    }
    return (await ctx.locator(rowSel).count().catch(() => 0)) > 0;
  }

  // F 算力豆：点头像→“明细”，在列表中按 query 匹配“消耗来源”行，读取该行“变动”值。
  // 带附件用例更精确：来源须含“用户上传了以下图片/文件”字样，且其“用户问题：”后的文本≈本用例 query
  // （平台把带附件来源包成“用户上传了以下文件…用户问题：<query>”，仅凭 query 子串易与别的行混淆/误配）。
  // 严格匹配无命中时退回原有宽松匹配（不比改前更差）；不带附件时仅走宽松匹配，行为完全不变。
  // 回填失败稳健化：①面板打开+行渲染都做等待/重试（见 _openLedgerPanel）；②标题键每轮补抓——复杂任务的
  // 「消耗来源」是答完后才由 AI 生成的会话标题，首轮常还没出来，一次抓不到就永久失配；③匹配到但「变动值」
  // 暂空不算成功（继续找有值的行或重试，而非提前返回空）；④全部尝试仍空时打印 keys 与账本前几行来源样本，
  // 便于定位失败到底是「面板没打开 / 没匹配上 / 记账还没落库」。
  async extractBeanCost(question, opts = {}) {
    const avSel = this.platform.avatarSelector;
    const entrySel = this.platform.ledgerEntrySelector || 'text=/算力豆|明细/';  // 头像→算力豆(旧 UI 明细),正则兼容
    if (!avSel || !question) return { value: '', raw: '' };
    const ctx = this._ctx();
    const rowSel = this.platform.ledgerRowSelector || '.coin-info__row';
    const qSel = this.platform.ledgerRowQuerySelector || '.coin-info__row-query';
    const cSel = this.platform.ledgerRowChangeSelector || '.coin-info__change-amount';
    // 是否带附件：优先显式入参，否则读实例字段（sendMessage / 桌面抓取入口按当前用例设置）。
    const hasAttachment = opts.hasAttachment != null ? !!opts.hasAttachment : !!this._hasAttachment;
    const uploadMarkers = this.platform.ledgerAttachmentMarkers || ['用户上传了以下图片', '用户上传了以下文件'];
    const questionMarker = this.platform.ledgerUserQuestionMarker || '用户问题';

    // 匹配键：当前会话侧栏标题 + 原始 query（“消耗来源”通常等于会话名，复杂任务是 AI 生成标题）。
    const norm = (s) => ('' + (s || '')).replace(/\s+/g, '');
    const keys = [norm(question)];
    let haveTitle = false;
    // 复杂任务的会话标题（= 账本「消耗来源」）是答完后才由 AI 生成的，首次可能还没出来 → 每轮补抓一次。
    const captureTitleKey = async () => {
      if (haveTitle) return;
      try {
        const title = await this._liveFrame().evaluate(() => {
          const el = document.querySelector('.aside-panel-task-list__item.is-selected .aside-panel-task-list__title-text')
            || document.querySelector('.aside-panel-task-list__item.is-selected');
          return el ? el.textContent : '';
        });
        const t = norm(title);
        if (t) { keys.unshift(t); haveTitle = true; }
      } catch (_) {}
    };
    await captureTitleKey();

    const attempts = this.execution.beanCostAttempts || 4;      // 记账延迟/标题晚生成时多重开几次
    const retryGap = this.execution.beanCostRetryGapMs || 2500;  // 每次重开前的等待（给记账落库留时间）
    let lastRows = -1, lastSamples = [];                         // 诊断：末次账本行数与来源样本
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        await captureTitleKey(); // 标题可能本轮才生成，补进匹配键
        const opened = await this._openLedgerPanel(ctx, avSel, entrySel, rowSel);
        if (!opened) {
          await this._closeLedgerPanel(ctx);
          if (attempt < attempts - 1) await this.page.waitForTimeout(retryGap);
          continue; // 面板没打开/无行，重开重试
        }

        const res = await this._liveFrame().evaluate(({ keys, question, rowSel, qSel, cSel, hasAttachment, uploadMarkers, questionMarker }) => {
          const norm = s => ('' + (s || '')).replace(/\s+/g, '').replace(/[…]+/g, '').replace(/\.{3,}/g, '');
          // 公共前缀长度：应对「消耗来源」被省略号截断——来源与 query 前若干字一致即视为同一条。
          const commonPrefix = (x, y) => { let i = 0; const n = Math.min(x.length, y.length); while (i < n && x[i] === y[i]) i++; return i; };
          // 宽松匹配（原逻辑）：头一致外再双向 includes——带附件来源里 query 只被内含而非打头，
          // 仅靠 startsWith 会漏掉（CASE-3 算力豆一直为空的根因）。阈值≥8 防短文误配。
          const matchPlain = (rk) => keys.some(k => {
            if (!k) return false;
            if (rk.length >= 4 && k.startsWith(rk)) return true;
            if (k.length >= 4 && rk.startsWith(k)) return true;
            if (k.length >= 8 && rk.includes(k)) return true;
            if (rk.length >= 8 && k.includes(rk)) return true;
            if (commonPrefix(rk, k) >= 12) return true; // 来源被截断/带省略号时，前 12 字一致即认定
            return false;
          });
          // 带附件严格匹配：① 来源含“用户上传了以下图片/文件”字样 ② “用户问题：”后文本≈本用例 query。
          const normMarkers = (uploadMarkers || []).map(norm).filter(Boolean);
          const qMarker = norm(questionMarker);
          const qKey = norm(question); // 原始 query 归一化（带附件时以它为准，不掺会话标题，避免 AI 标题干扰）
          const matchAttachment = (rk) => {
            if (!normMarkers.some(mk => rk.includes(mk))) return false;       // 条件①：含上传字样
            if (!qKey || qKey.length < 2) return false;
            let rkq = rk;                                                     // 条件②：取“用户问题”后的文本作为该行 query
            const idx = qMarker ? rk.indexOf(qMarker) : -1;
            if (idx >= 0) rkq = rk.slice(idx + qMarker.length).replace(/^[:：]+/, '');
            if (rkq.includes(qKey) || qKey.includes(rkq)) return true;
            return commonPrefix(rkq, qKey) >= Math.min(12, qKey.length);
          };
          const readChange = (r) => { const c = r.querySelector(cSel); return c ? norm(c.textContent).replace(/[^\d.]/g, '') : ''; }; // 只留数字，去“-”等符号
          // 返回首个「匹配且变动值非空」的行的值；仅匹配到但变动值暂空 → 返回 ''（触发重试/回退，不误当成功）；无任何匹配 → null。
          const pick = (matchFn) => {
            const rows = document.querySelectorAll(rowSel);
            let matchedButEmpty = false;
            for (const r of rows) {
              const q = r.querySelector(qSel);
              if (!q) continue;
              if (matchFn(norm(q.textContent))) {
                const change = readChange(r);
                if (change) return change;   // 匹配且拿到变动值 → 直接用
                matchedButEmpty = true;      // 匹配到但变动值暂空 → 记住，继续找有值的行
              }
            }
            return matchedButEmpty ? '' : null;
          };
          // 带附件：先严格匹配（拿到非空值才用）；无有值命中再退回宽松匹配。不带附件：仅宽松匹配（行为不变）。
          let value = null;
          if (hasAttachment) { const s = pick(matchAttachment); if (s) value = s; }
          if (!value) { const p = pick(matchPlain); if (p) value = p; }
          // 诊断：账本行数 + 前几行「消耗来源」样本（截断），失败时用于判断是没匹配上还是记账没出来。
          const allRows = document.querySelectorAll(rowSel);
          const samples = [];
          for (let i = 0; i < allRows.length && samples.length < 5; i++) {
            const qc = allRows[i].querySelector(qSel);
            if (qc) samples.push(norm(qc.textContent).slice(0, 24));
          }
          return { value: value || '', rows: allRows.length, samples };
        }, { keys, question, rowSel, qSel, cSel, hasAttachment, uploadMarkers, questionMarker });

        await this._closeLedgerPanel(ctx);
        if (res && res.value) return { value: res.value, raw: res.value };
        if (res) { lastRows = res.rows; lastSamples = res.samples || []; }
      } catch {
        await this._closeLedgerPanel(ctx);
      }
      if (attempt < attempts - 1) await this.page.waitForTimeout(retryGap); // 明细可能有记账延迟，稍等重开重试
    }
    await this._closeLedgerPanel(ctx);
    if (this.logger) {
      const ks = keys.filter(Boolean).map(k => k.slice(0, 14)).join(' | ');
      const smp = lastRows < 0 ? '面板始终未打开(点头像/明细失败)' : `账本${lastRows}行[${lastSamples.join(' / ')}]`;
      this.logger.warn(`       ↳ [${this.label}] 算力豆未抓到(尝试${attempts}次) keys=[${ks}] ${smp}`);
    }
    return { value: '', raw: '' };
  }

  // 多轮对话：在「同一对话」里按顺序连发 turns（已按 turnIndex 排好的同会话用例数组）。
  // 首轮走正常 init（新建对话）；后续轮因 this.page 已存在，sendMessage 里的 init 自动跳过，
  // 于是不新建对话、直接在当前对话追加提问，形成多轮上下文。每轮各自抓字段、返回一条 result。
  // 逐轮回调 onTurnDone(result, testCase) 便于「每轮完成即回填对应行」。整段多轮跑完由调用方 close。
  async runConversation(turns, onTurnDone) {
    // 多轮标记：turns>1 即多轮会话。会话期内禁用 _reloadAndRefill（reload 会重置对话 iframe/VM 状态，
    // 下一轮 follow-up 发送时 iframe 处于重挂中间态，input.waitFor/click 命中失败 30s 超时——
    // 实测 CASE-4/6 根因）。多轮上下文连续性远比补一个耗时字段重要。
    this.multiTurn = turns.length > 1;
    const results = [];
    for (const testCase of turns) {
      const isFollowUp = (testCase.turnIndex || 0) > 0;
      const isLastTurn = (testCase.turnIndex || 0) === turns.length - 1; // 末轮才抓分享链接/算力豆/产物
      let result;
      try {
        result = await this.runWithRetry(testCase, { isFollowUp, isLastTurn });
      } catch (e) {
        // 单轮异常不得中断整个会话：记为失败结果，继续后续轮（上下文可能已断，但仍如实记录）
        const msg = (e && e.message) ? e.message.split('\n')[0] : String(e);
        result = {
          caseId: testCase.caseId, row: testCase.row, account: testCase.account,
          question: testCase.question, success: false, incomplete: true,
          completeReason: 'exception', answer: `[执行异常] ${msg}`,
          shareLink: '', artifactShareLink: '', reportedDuration: '', beanCost: '',
          cost: '', durationMs: 0, missingFields: [], reloadRecoveredFields: []
        };
      }
      result.conversationId = testCase.conversationId;
      result.turnIndex = testCase.turnIndex;
      results.push(result);
      if (onTurnDone) { try { await onTurnDone(result, testCase); } catch (_) {} }
    }
    return results;
  }

  async runWithRetry(testCase, opts = {}) {
    const isFollowUp = !!opts.isFollowUp; // 多轮对话的后续轮：不新建对话、重试也不 reload，保住上下文
    const isLastTurn = opts.isLastTurn !== false; // 末轮抓全字段；中间轮跳过开面板抓取
    const retryTimes = this.execution.retryTimes || 0;
    const retryDelay = this.execution.retryDelay || 3000;
    // 补填后仍为空、且属于这些“关键字段”时，才整条重跑（新对话可稳定重生成分享链接）。
    // 耗时/算力豆多为抓取时序问题，重跑数分钟的 Agent 任务收益低，默认不因它们重跑。
    const rerunFields = this.execution.rerunOnMissingFields || ['conversationShareLink', 'artifactShareLink'];
    let lastResult = null;

    for (let attempt = 0; attempt <= retryTimes; attempt++) {
      // 首次执行的 init（打开页面+新对话）已并入 sendMessage 的发送段，在账号内串行化，
      // 保证“上一条发出去后再发下一条”。故此处不再单独 init。

      const result = await this.sendMessage(testCase.question, testCase.attachmentPaths || [], { isLastTurn });
      result.caseId = testCase.caseId;
      result.row = testCase.row;
      result.account = testCase.account;
      result.attempt = attempt + 1;
      lastResult = result;

      // 完整 = 成功产出正文 且 无“关键字段”缺失。缺失但非关键（如耗时/算力豆）不强制重跑。
      const missingCritical = (result.missingFields || []).filter(f => rerunFields.includes(f));
      if (result.success && missingCritical.length === 0) return result;

      // follow-up 轮（多轮后续轮）不再整条重跑：重跑需 reload 当前对话页面，而平台 reload 后
      // 不一定保持在当前对话——重发 query 可能落到新对话/重复提问，丢掉多轮上下文（实测 CASE-3/4
      // follow-up 轮因分享链接空触发重跑，reload 后产生多余对话）。多轮场景下「对话状态连续」远比
      // 「补一个分享链接」重要，缺字段交给 _refillEmptyFields 就地补，补不上就留空。
      if (isFollowUp) return result;

      // 超时/中间态未完成：重跑一条超长任务大概率再次超时（空耗数小时），默认不重跑，直接如实返回，
      // 让用户针对性处理（专家反问已能自动应答，走到这里的多为平台侧异常/真超长任务）。
      if (result.incomplete && !this.execution.rerunOnIncomplete) {
        if (this.logger) this.logger.warn(`       ↳ ${testCase.caseId} 未完成(${result.completeReason})，按配置不整条重跑（rerunOnIncomplete=false）`);
        return result;
      }

      if (attempt < retryTimes) {
        if (this.logger && result.success && missingCritical.length > 0) {
          this.logger.warn(`       ↳ ${testCase.caseId} 关键字段仍空(${missingCritical.join(', ')})，整条重跑第 ${attempt + 2} 次`);
        }
        if (this.execution.takeScreenshotOnError && this.page) {
          try {
            const dir = path.resolve('./output/screenshots');
            fs.mkdirSync(dir, { recursive: true });
            await this.page.screenshot({ path: path.join(dir, `${testCase.caseId}_attempt${attempt + 1}.png`) });
          } catch (_) {}
        }
        await new Promise(r => setTimeout(r, retryDelay));
        try {
          await this.page.reload({ waitUntil: 'domcontentloaded' });
          this.frame = await this._waitForFrame(this.execution.timeout || 60000);
          // 多轮后续轮重试：reload 只刷新当前对话（保住已有多轮上下文），绝不能新建对话——
          // 新建会丢掉前几轮的对话历史，重发的这一轮就不再是「多轮」。故仅首轮/单轮才新建对话。
          if (!isFollowUp) await this._startNewConversation();
        } catch (e) {
          // 重试准备失败（多为账号登录态波动/VM 未就绪，reload 后找不到对话界面）：
          // 绝不向上抛——否则单条会连累整个批次崩溃退出(exit 1)。放弃本条重试，返回已有结果。
          const msg = (e.message || '').split('\n')[0];
          const hint = /找不到对话输入框|页面未加载完成/.test(msg) ? '（可能账号登录态失效，请重新录制该账号）' : '';
          if (this.logger) this.logger.warn(`       ↳ ${testCase.caseId} 重试准备失败，放弃重试${hint}: ${msg}`);
          return lastResult;
        }
      }
    }

    return lastResult;
  }

  async close() {
    // 绑定的外部主窗口（ownsPage=false，桌面场景）不关闭——那是客户端唯一的窗口，关了就没了。
    if (this.page && this.ownsPage !== false) await this.page.close();
    this.page = null;
    this.frame = null;
  }
}

module.exports = DialogRunner;
module.exports.isExtractingCritical = isExtractingCritical;
