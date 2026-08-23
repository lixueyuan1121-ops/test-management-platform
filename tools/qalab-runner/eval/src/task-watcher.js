// 并发观察器（有头模式）：开一个独立的「观察」标签页，定时轮流点击左侧任务列表中【正在执行中】的
// 任务条目，让内容区切换显示各任务对话，供肉眼确认多个并发任务的内容是否会串；同时对切到的对话做
// 一组诊断检查（串台 / agent / 结构），异常写入诊断报告。
//
// 只读浏览：不发消息、不抓取、不“新建任务”。但注意——独立 page ≠ 独立剪贴板：切换点击会污染
// 系统剪贴板（全机共享）、可能打断执行标签正在开的明细弹窗/预览。为此观察器切换前会检查
// DialogRunner.isExtractingCritical()：有任务正在抓分享链接/算力豆/耗时等关键字段时，暂停本轮切换。
// 另外：无活跃任务（都执行完、进入收尾）时自动停止切换，不再空转。
//
// 需求1+3：只在「正在执行中」的条目之间轮询（识别 taskListRunningSelector 标志），已完成的不再参与。
//          若未识别到任何执行中标志（多为选择器没配对），降级为轮询最上面 N 个，保证不罢工。
//
// 对话 UI 在跨域 iframe 内，且首页会经历「占位 iframe → 真实 VM iframe」的重挂载，
// 故统一用 frameLocator（每次操作自动重解析 iframe），只匹配含 .work.n.cn 的真实 iframe。
//
// 兜底刷新：平台若不实时把新建任务推送到左侧列表，观察页面会看不到本次刚建的任务。为此，当列表
// 可见条目数还没达到期望并发数、且距上次刷新超过阈值时，reload（回 launcher）重新拉取列表。
const DialogRunner = require('./dialog-runner');
const workFrame = require('./work-frame');

class TaskWatcher {
  constructor(context, platformConfig, executionConfig, logger, account, diag) {
    this.context = context;
    this.platform = platformConfig;
    this.execution = executionConfig || {};
    this.logger = logger || null;
    this.account = account || '';
    this.diag = diag || null;   // DiagnosticReporter，可空（无则只切换不写诊断）
    this.page = null;
    this.frame = null;
    this.timer = null;
    this.idx = 0;            // 轮流指针：跨多次触发保持，依次点击不同条目
    this.stopped = false;
    this.expectedN = 1;      // 期望的并发任务数（start 时传入），用于判断列表是否已出齐 / 降级轮询范围
    this.lastReloadAt = 0;   // 上次刷新观察页面的时刻
    this.reloadCount = 0;    // 已兜底刷新次数
    this.maxReloads = 5;     // 兜底刷新次数上限（避免任务始终不足时无限刷新打断画面）
    this.getActiveCount = null; // 由 start 注入：返回当前活跃任务数（()=>number），用于“无活跃任务即停切”
    this.seenContent = new Map(); // 串台诊断：提问指纹 -> 首次见到它的任务标题（A 提问跨对话重复）
    this.seenAnswers = new Map();  // 串台诊断：任务标题 -> 其回答代表文本（A2 回答跨对话重复）
    this._degradeWarned = false;  // 降级轮询只提示一次，避免刷屏
    this._agentWarned = new Set(); // agent 诊断：同一标题只报一次
    this._singleRunningNoticed = false; // “只剩一个执行中任务、暂停轮询”只提示一次；回到多任务时复位
  }

  // 打开观察页面并等待对话界面就绪。
  async init() {
    const timeout = this.execution.timeout || 60000;
    this.page = await this.context.newPage();
    // 观察页面不主动操作，遇到原生弹窗直接放行，避免卡住
    this.page.on('dialog', async (d) => { try { await d.accept(); } catch { try { await d.dismiss(); } catch {} } });

    await this._navigateAndWait(timeout);
    this.lastReloadAt = Date.now();

    // 初始把观察页面切到前台，用户即可看到左侧任务列表随后自动切换
    await this.page.bringToFront().catch(() => {});
  }

  // 【桌面场景】绑定外部已就绪的主窗口 page（不自己 newPage/goto）。桌面版全程共用一个主窗口，
  // 观察/切换/诊断都在这个 page 上进行。绑定后即可调用 diagnoseCurrent() 复用串台诊断算法。
  attach(page) {
    this.page = page;
    // 主文档安全默认;由 _ensureFrame(观察/导航前)或 DesktopRunner._ensureCtx 覆盖为正确 ctx。
    this.frame = page;
    this.lastReloadAt = 0;
    return this;
  }

  // 判定当前 page 对话 UI 形态,设 this.frame(frameLocator 或主文档 page)。观察 tick/导航前调用。
  async _ensureFrame() {
    if (!this.page) return;
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const hasIframe = await workFrame.hasWorkIframe(this.page, sel);
    this.frame = workFrame.pickCtx(this.page, sel, hasIframe);
  }

  // 【桌面场景】对「当前显示的对话」跑一遍诊断（A 串台系列 / B agent / C 连续提问无正文），
  // 复用与 Web 观察器完全相同的检测算法。label/agentName 由调用方（DesktopRunner）在切到该对话后传入。
  async diagnoseCurrent(label, agentName) {
    return this._diagnose(label, agentName || '');
  }

  // 打开/刷新 chatUrl 并等对话界面就绪（输入框可见 = 真实 VM iframe 已挂载）。init 与兜底刷新共用。
  async _navigateAndWait(timeout) {
    await this.page.goto(this.platform.chatUrl, { waitUntil: 'domcontentloaded', timeout });
    await this._ensureFrame();   // 自适应:导航后判定 iframe/主文档形态

    const inputSel = this.platform.inputSelector;
    const deadline = Date.now() + timeout;
    let ready = false;
    while (Date.now() < deadline) {
      try {
        await this.frame.locator(inputSel).first().waitFor({ state: 'visible', timeout: 3000 });
        ready = true;
        break;
      } catch {
        await this.page.waitForTimeout(1000);
      }
    }
    if (!ready) throw new Error('观察页面加载超时（对话界面未就绪）');
  }

  // 启动定时切换。n<=1 或间隔<=0 时不启动（无切换意义）。
  // getActiveCount：可选，返回当前活跃任务数的函数；一旦返回 0（任务都执行完、进入收尾）就自动停止切换。
  start(n, intervalMs, getActiveCount) {
    if (this.stopped || !this.frame) return;
    if (!(intervalMs > 0) || !(n > 1)) return;
    this.expectedN = n;
    this.getActiveCount = typeof getActiveCount === 'function' ? getActiveCount : null;
    const tick = async () => {
      if (this.stopped) return;
      if (!this._frameReady) { await this._ensureFrame(); this._frameReady = true; }   // 首轮判定形态(iframe/主文档)
      // 无活跃任务（全部执行完、进入抓取收尾/结束）→ 停止切换，不再空转打扰。
      if (this.getActiveCount && this.getActiveCount() === 0) {
        if (this.logger) this.logger.info(`   👀 观察[${this.account}] 无进行中的任务，停止切换`);
        await this.stop();
        return;
      }
      // 有任务正在抓分享链接/算力豆/耗时等关键字段（依赖剪贴板/弹窗/tooltip）→ 本轮不切，避免打断抓取。
      if (DialogRunner.isExtractingCritical && DialogRunner.isExtractingCritical()) return;
      try { await this._switchOnce(); } catch { /* 单轮失败不影响后续 */ }
    };
    this.timer = setInterval(() => { tick().catch(() => {}); }, intervalMs);
  }

  // 一轮切换：在「执行中」的条目里挑下一个点击，切换显示其对话，并做诊断检查。
  async _switchOnce() {
    if (await this._maybeReload()) return; // 本轮刚刷新了列表，等下一轮再点，避免与新列表错位

    const itemSel = this.platform.taskListItemSelector || '.aside-panel-task-list__item';
    const titleSel = this.platform.taskListTitleSelector || '.aside-panel-task-list__title-text';
    const items = this.frame.locator(itemSel);
    const total = await items.count().catch(() => 0);
    if (total === 0) return; // 任务尚未出现在列表，等下一轮（或由兜底刷新拉取）

    // 需求1+3：只在「执行中」的条目之间轮询
    let pool = await this._collectRunningIndexes(items, total);
    let runningMode = true;
    if (pool.length === 0) {
      // 未识别到执行中标志：可能选择器没配对，也可能确实都完成了。
      // 若已无活跃任务则不切（交给 tick 的停切逻辑）；否则降级为轮询最上面 N 个，保证不罢工。
      if (this.getActiveCount && this.getActiveCount() === 0) return;
      pool = Array.from({ length: Math.min(this.expectedN, total) }, (_, k) => k);
      runningMode = false;
      if (!this._degradeWarned && this.logger) {
        this.logger.info(`   👀 观察[${this.account}] 未识别到「执行中」标志，降级为轮询最上面 ${pool.length} 个（可在 config.taskListRunningSelector 校准）`);
        this._degradeWarned = true;
      }
    }

    // 优化：只剩一个「执行中」任务时，无需再轮询点击（反复点同一个既没意义，还会白白污染剪贴板/
    // 打断抓取）。它已在前台显示，直接跳过本轮；等再次出现多个执行中任务时自动恢复轮询。
    if (runningMode && pool.length === 1) {
      if (!this._singleRunningNoticed && this.logger) {
        this.logger.info(`   👀 观察[${this.account}] 仅剩 1 个任务执行中，暂停轮询切换（多任务时自动恢复）`);
        this._singleRunningNoticed = true;
      }
      return;
    }
    // 回到多任务（或降级轮询）→ 复位提示，下次只剩一个时会再提示一次
    this._singleRunningNoticed = false;

    const i = pool[this.idx % pool.length];
    this.idx++;

    const item = items.nth(i);
    let title = '';
    try { title = (await item.locator(titleSel).first().innerText().catch(() => '')).trim(); } catch {}
    const label = title || `第 ${i + 1} 项`;
    // agent 名就在任务条目内（.aside-panel-task-list__name），点击前就能读到，供诊断 b 使用（可靠、不依赖切换加载）。
    let agentName = '';
    const agentSel = this.platform.taskListAgentSelector;
    if (agentSel) { try { agentName = (await item.locator(agentSel).first().innerText().catch(() => '')).trim(); } catch {} }

    const prevQuery = await this._readFirstQuery(); // 点击前的内容，用于判断是否真的切到了新对话
    await item.click({ timeout: 4000 }).catch(() => {});
    // 等内容区真正切到新对话再读（切任务会重挂 iframe，固定等太短会读到上一个对话的残留，
    // 导致标题/提问张冠李戴的串台误报）。
    await this._waitSwitchSettled(prevQuery);

    const peek = await this._peekAnswer(60);
    if (this.logger) {
      const tag = runningMode ? '执行中' : '轮询';
      this.logger.info(`   👀 观察[${this.account}](${tag}) → 切到「${label}」: ${peek || '(暂无正文)'}`);
    }

    await this._diagnose(label, agentName); // 需求4：a 串台 / b agent / c 连续提问无正文
  }

  // 读当前对话第一个用户提问的文本（去换行、trim）。供切换到位判断与诊断复用。
  async _readFirstQuery() {
    const userSel = this.platform.userGroupSelector || '.chat-group.user';
    const userBubbleSel = this.platform.userBubbleSelector;
    try {
      const users = this.frame.locator(userSel);
      if ((await users.count().catch(() => 0)) === 0) return '';
      const first = users.first();
      const q = userBubbleSel ? first.locator(userBubbleSel).first() : first;
      return (await q.innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
    } catch { return ''; }
  }

  // 点击条目后等内容区切到新对话：轮询 firstQuery，直到它「变化(≠prev) 且连续两次稳定」或超时。
  // 返回稳定后的 firstQuery。避免读到上一个对话残留造成串台误报。
  async _waitSwitchSettled(prevQuery, timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    let last = null, stable = '';
    while (Date.now() < deadline) {
      await this.page.waitForTimeout(400);
      const cur = await this._readFirstQuery();
      if (cur && cur === last) {              // 连续两次相同 = 内容稳定
        stable = cur;
        if (cur !== prevQuery) return cur;    // 稳定且已切换（内容变了）→ 到位
      }
      last = cur;
    }
    return stable || last || '';             // 超时兜底：新旧内容恰同 / 迟迟没切，返回最后所见
  }

  // 返回「执行中」条目的 index 列表（在条目内查找 taskListRunningSelector 命中即执行中）。
  // 只扫前若干个，避免长历史列表遍历过慢。选择器为空/失配则返回空（交由降级处理）。
  async _collectRunningIndexes(items, total) {
    const runSel = this.platform.taskListRunningSelector;
    if (!runSel) return [];
    const cap = Math.min(total, 20);
    const idxs = [];
    for (let k = 0; k < cap; k++) {
      try {
        if ((await items.nth(k).locator(runSel).count().catch(() => 0)) > 0) idxs.push(k);
      } catch { /* 单条判定失败忽略 */ }
    }
    return idxs;
  }

  // 诊断切到的当前对话。核心诉求=监视并发对话「串台」（问A答B / 别的任务串进来）。多路检测：
  //   A4 同对话多提问：一个对话冒出≥2个互异的长提问（正常每用例是独立单轮），最强的串台信号；
  //   A1 问答不匹配：按序配对提问↔回答，回答完全不呼应其提问 → 疑似问A答B（保守，防误报）；
  //   A2 回答跨对话重复：不同任务出现高度相同的回答 → 回答串到了一起；
  //   A  提问跨对话重复：不同任务显示相同的首个提问（保留原有）；
  //   C  连续提问无正文（保留）。选择器失配/读不到 → 静默跳过，不误报。
  async _diagnose(label, agentName) {
    if (!this.diag) return;
    await this._diagAgent(label, agentName);   // B

    const st = await this._readStructure();

    // C：提问数明显多于「有正文的回答数」→ 连着几个 query 中间缺正文
    if (st.userCount >= 2 && st.userCount > st.answeredCount + 1) {
      await this.diag.record(label, 'C-连续提问无正文', `检测到 ${st.userCount} 个提问但仅 ${st.answeredCount} 个有正文回答`, this.page);
    }

    // A4（最强串台信号）：同一对话里出现≥2个「内容差异大」的提问 —— 正常每条用例是独立单轮新对话，
    // 冒出多个互异的长提问，极可能是并发把别的任务的 query 串进了本对话。
    const distinctQs = this._distinctLongQueries(st.userTexts);
    if (distinctQs.length >= 2) {
      await this.diag.record(label, 'A4-串台·同对话多提问',
        `本对话出现 ${distinctQs.length} 个互不相同的提问，疑似其他任务的提问串入：` +
        distinctQs.map(q => `“${q.slice(0, 16)}…”`).join(' / '), this.page);
    }

    // A1（问答不匹配/配对错位，保守）：按顺序配对 提问[i]↔回答[i]，若回答完全不呼应提问
    // （提问的所有片段在回答里一个都不出现）→ 疑似问A答B。仅在两者都够长时判，阈值保守。
    const pairs = Math.min(st.userTexts.length, st.answerTexts.length);
    for (let i = 0; i < pairs; i++) {
      const q = st.userTexts[i], a = st.answerTexts[i];
      if (q.length >= 12 && a.length >= 40 && !this._answerEchoesQuery(q, a)) {
        await this.diag.record(label, 'A1-问答不匹配',
          `第 ${i + 1} 轮回答未呼应其提问，疑似问答错位/串台（问“${q.slice(0, 16)}…” → 答“${a.slice(0, 16)}…”）`, this.page);
      }
    }

    // A2（回答跨对话重复）：本对话回答与「别的任务」的回答高度相同 → 回答串台。
    const rep = this._longest(st.answerTexts);
    if (rep.length >= 40) {
      for (const [prevLabel, prevRep] of this.seenAnswers) {
        if (prevLabel !== label && !this._sameTaskTitle(prevLabel, label) && this._textSimilar(rep, prevRep, 0.6)) {
          await this.diag.record(label, 'A2-回答串台',
            `回答与「${prevLabel}」高度相同，疑似回答串到了一起（“${rep.slice(0, 20)}…”）`, this.page);
          break;
        }
      }
      this.seenAnswers.set(label, rep);
    }

    // A（保留）：不同任务显示相同「首个提问」→ 提问串台。
    if (st.firstQuery && st.firstQuery.length >= 10) {
      const fp = st.firstQuery.slice(0, 32);
      const prev = this.seenContent.get(fp);
      if (!prev) {
        this.seenContent.set(fp, label);
      } else if (prev !== label && !this._sameTaskTitle(prev, label)) {
        await this.diag.record(label, 'A-提问串台', `与「${prev}」显示了相同的提问（“${fp.slice(0, 24)}…”）`, this.page);
      }
    }
  }

  // 从提问列表里挑出「互不相似」的长提问代表：与已收集代表都不相似(jaccard<0.5)的才算新提问。
  // 一个对话只有单个提问(或其流式快照)→ 返回1个；混入了别的任务提问 → 返回≥2个。
  _distinctLongQueries(userTexts) {
    const reps = [];
    for (const t of (userTexts || [])) {
      if (!t || t.length < 12) continue;
      if (reps.some(r => this._textSimilar(r, t, 0.5))) continue; // 与已有代表相似=同一提问，跳过
      reps.push(t);
    }
    return reps;
  }

  // 回答是否「呼应」提问：提问的若干片段(4-gram)只要有一个出现在回答里就算呼应。
  // 极保守——所有片段一个都不命中才判「不呼应」，避免正常问答误报（问A答B时A的片段不会出现在B的回答里）。
  _answerEchoesQuery(q, a) {
    const qs = String(q || '').replace(/[\s\p{P}]/gu, '');
    const as = String(a || '').replace(/[\s\p{P}]/gu, '');
    if (qs.length < 4) return true; // 太短不判 → 视为呼应(不报)
    for (let i = 0; i + 4 <= qs.length; i += 2) { // 步长2采样降开销
      if (as.includes(qs.slice(i, i + 4))) return true; // 命中任一片段=呼应
    }
    // 补采「末尾 4-gram」：步长2从头采样会漏掉末尾窗口——而「用一句话解释什么是X」这类提问的
    // 判别词 X（如“万有引力”“光合作用”）恰在末尾，漏采会把「答案开头就复述了 X」的正确回答误判为
    // 不呼应，触发 A1 串台误报（实测根因）。末尾片段是最关键的呼应信号，必须显式检查。
    if (as.includes(qs.slice(qs.length - 4))) return true;
    return false;
  }

  // 文本相似度：字符 bigram 的 Jaccard >= threshold。中文无需分词，对「是否高度相同/相关」够用。
  _textSimilar(a, b, threshold) {
    const A = this._bigrams(a), B = this._bigrams(b);
    if (!A.size || !B.size) return false;
    let inter = 0; for (const g of A) if (B.has(g)) inter++;
    const uni = A.size + B.size - inter;
    return uni > 0 && inter / uni >= threshold;
  }

  _bigrams(text) {
    const s = String(text || '').replace(/[\s\p{P}]/gu, '');
    const set = new Set();
    for (let i = 0; i + 2 <= s.length; i++) set.add(s.slice(i, i + 2));
    return set;
  }

  _longest(arr) {
    return (arr || []).reduce((m, t) => (t && t.length > m.length ? t : m), '');
  }

  // 判断两个任务标题是否「同一任务」（应对标题变体：流式/省略导致的微小差异）。
  _sameTaskTitle(a, b) {
    const na = String(a || '').replace(/\s+/g, '');
    const nb = String(b || '').replace(/\s+/g, '');
    if (!na || !nb) return false;
    if (na === nb) return true;
    if (na.includes(nb) || nb.includes(na)) return true;        // 一个是另一个子串（省略/补全）
    const d = this._editDistance(na, nb);
    return d <= Math.max(2, Math.floor(Math.min(na.length, nb.length) * 0.2)); // 编辑距离小 = 同一任务
  }

  // 轻量编辑距离（长度差过大直接判不同，避免大矩阵）。
  _editDistance(a, b) {
    const m = a.length, n = b.length;
    if (Math.abs(m - n) > 8) return 99;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = 0; i <= m; i++) dp[i][0] = i;
    for (let j = 0; j <= n; j++) dp[0][j] = j;
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      }
    }
    return dp[m][n];
  }

  // b：用任务条目里的 agent 名（.aside-panel-task-list__name）与期望值比对；不一致报 B。
  // agentName 读不到（选择器失配/空）→ 不报（无法检测）。同一条目只报一次。
  async _diagAgent(label, agentName) {
    const expected = this.platform.expectedAgentName;
    if (!expected || !agentName || this._agentWarned.has(label)) return;
    if (!agentName.includes(expected)) {
      await this.diag.record(label, 'B-agent异常', `该任务 agent 为「${agentName}」，不是期望的「${expected}」`, this.page);
      this._agentWarned.add(label); // 同一条目只报一次
    }
  }

  // 读对话结构：全部用户提问文本、全部回答正文、有正文的回答数（用于 A/A1/A2/A4/C 诊断）。
  async _readStructure() {
    const out = { userCount: 0, answeredCount: 0, firstQuery: '', userTexts: [], answerTexts: [] };
    const userSel = this.platform.userGroupSelector || '.chat-group.user';
    const asstSel = this.platform.answerGroupSelector || '.chat-group.assistant';
    const bubbleSel = this.platform.answerSelector;
    const userBubbleSel = this.platform.userBubbleSelector;
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
    try {
      // —— 用户提问：读每一条的文本 ——
      const users = this.frame.locator(userSel);
      out.userCount = await users.count().catch(() => 0);
      for (let k = 0; k < out.userCount; k++) {
        const g = users.nth(k);
        const q = userBubbleSel ? g.locator(userBubbleSel).first() : g;
        const t = clean(await q.innerText().catch(() => ''));
        if (t) out.userTexts.push(t);
      }
      out.firstQuery = out.userTexts[0] || '';

      // —— AI 回答：读每一条的正文，统计“有正文”的条数 ——
      const asst = this.frame.locator(asstSel);
      const an = await asst.count().catch(() => 0);
      for (let k = 0; k < an; k++) {
        const g = asst.nth(k);
        let txt = '';
        if (bubbleSel && (await g.locator(bubbleSel).count().catch(() => 0)) > 0) {
          txt = clean(await g.locator(bubbleSel).last().innerText().catch(() => ''));
        }
        if (txt) { out.answeredCount++; out.answerTexts.push(txt); }
      }
    } catch { /* 读结构失败 → 返回默认（不触发诊断），不误报 */ }
    return out;
  }

  // 兜底刷新：当可见条目数还没达到期望并发数、且距上次刷新超过阈值时，reload 观察页面拉取最新列表。
  // 返回是否执行了刷新（刷新后本轮不再点击，等下一轮从新列表点）。带次数上限，避免无限刷新打断画面。
  async _maybeReload() {
    const reloadMs = this.execution.watchReloadMs != null ? this.execution.watchReloadMs : 30000;
    if (!(reloadMs > 0)) return false;
    if (this.reloadCount >= this.maxReloads) return false;        // 刷够次数，不再刷
    const itemSel = this.platform.taskListItemSelector || '.aside-panel-task-list__item';
    const total = await this.frame.locator(itemSel).count().catch(() => 0);
    if (total >= this.expectedN) return false;                   // 任务已出齐，无需刷新
    if (Date.now() - this.lastReloadAt < reloadMs) return false; // 未到刷新间隔
    this.reloadCount++;
    if (this.logger) {
      this.logger.info(`   👀 观察[${this.account}] 左侧任务列表未出齐(${total}/${this.expectedN})，刷新拉取最新（第 ${this.reloadCount}/${this.maxReloads} 次）...`);
    }
    await this._reload();
    this.lastReloadAt = Date.now();
    return true;
  }

  // 刷新观察页面：回到 chatUrl（launcher）重新拉取完整任务列表。
  // 点击任务后 URL 可能已变为 /claw?vm_id=xxx，故用 goto 回首页而非 page.reload，确保拿到完整列表。
  async _reload() {
    const timeout = this.execution.timeout || 60000;
    try {
      await this._navigateAndWait(timeout);
    } catch (e) {
      if (this.logger) this.logger.warn(`   👀 观察[${this.account}] 刷新失败（忽略，继续观察）: ${e.message}`);
    }
  }

  // 读当前内容区最后一组回答的正文片段（去换行、截断），供对照是否串。
  async _peekAnswer(maxLen) {
    try {
      const groupSel = this.platform.answerGroupSelector || '.chat-group.assistant';
      const groups = this.frame.locator(groupSel);
      const n = await groups.count().catch(() => 0);
      if (n === 0) return '';
      const txt = (await groups.nth(n - 1).innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
      return txt.length > maxLen ? txt.slice(0, maxLen) + '…' : txt;
    } catch { return ''; }
  }

  async stop() {
    this.stopped = true;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (this.page) { try { await this.page.close(); } catch {} this.page = null; }
    this.frame = null;
  }
}

module.exports = TaskWatcher;
