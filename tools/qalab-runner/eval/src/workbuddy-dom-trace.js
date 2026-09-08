// src/workbuddy-dom-trace.js
// WorkBuddy DOM 轨迹抓取器：WorkBuddy 对话走主进程 HTTP（渲染进程 CDP 截不到流），
// 故从 DOM 抓 thinking/tool_calls/artifacts/answer + footer 元信息，规整成与 ws-trace.js::buildTrace 同结构，
// 让后端判定层零改动复用。对外接口（buildTrace/reset）与 ws-trace 的 collector 对齐，供 reportRun 无缝调用。
//
// 2026-09-07 真机侦察坐实（Task 1）：
//   - 回答正文 .cr-markdown；消息容器 .cr-message-list__content
//   - 思考区是 cr-collapse 折叠组件（header .cr-collapse__title「已完成 Ns」，内容 .cr-collapse__content-inner，
//     默认折叠、内容懒渲染 → 抓前需点开 header）
//   - 耗时在思考折叠头「已完成 Ns」（不在 footer）
//   - 工具调用无结构化卡片，呈现为"来源"面板：触发 .artifact-slot-panel__sources，
//     展开后 .sources-panel（标题 .sources-panel__title「引用来源 (N)」，条目 .sources-panel__list）
//   - 消耗+模型在 .conversation-finished-footer
const { _isMcp, _mcpServer, sanitizeDialogText } = require('./ws-trace');

// 折叠头「已完成 …」完成标记:「已完成」后跟数字(可隔空格/冒号),兼容「已完成 42s」「已完成 2分43秒」
// 「已完成 1小时2分3秒」等所有时长形态。检测(轮询/选耗时 title)与解析(parseDuration)统一用它,
// 避免只认整数秒「\d+s」导致 ≥60s 的复合时长(如「2分43秒」)漏检 → 耗时恒空。
const DONE_MARKER_RE = /已完成[\s:：]*\d/;

// 解析 conversation-finished-footer 文本：含「共消耗 / <bean> / <model> / <time>」等行。
// 纯函数，导出供离线单测。footer 各行由 innerText 换行分隔。
function parseFooter(text) {
  const out = { beanCost: '', model: '' };
  if (!text) return out;
  const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
  for (const line of lines) {
    if (/^[\d.]+$/.test(line)) out.beanCost = line;                                          // 纯数字行 = 算力消耗
    else if (/[（(].*[)）]|Deepseek|GLM|Hy\d|MiniMax|均衡|快速|极致/i.test(line)) out.model = line; // 含模型名/档位
  }
  return out;
}

// 从思考折叠头文本「已完成 …」抽耗时,统一换算成纯秒(字符串)。无「已完成」标记返回空串。
// 纯函数,导出供单测。折叠头 <60s 呈「已完成 42s」、≥60s 呈「已完成 2分43秒」(中文复合,与纳米
// .chat-thinking-toggle__label 同形),也可能是「1小时2分3秒」/「01:05」/纯秒。口径对齐
// dialog-runner._durationToSeconds 与后端 _parse_seconds:解析不出返回空(不臆造耗时)。
function parseDuration(text) {
  const s = ('' + (text || '')).trim();
  if (!s) return '';
  // 必须是「已完成」态才有确定耗时(思考中/其他折叠头无耗时);取「已完成」之后到行尾的时长表达。
  const done = s.match(/已完成[\s:：]*(.+)/);
  if (!done) return '';
  const dur = done[1].trim();
  const sec = _durationToSeconds(dur);
  return sec != null ? String(sec) : '';
}

// 把「X小时Y分Z秒」中文 /「5m 2s」「1h5m2s」英文 / hh:mm:ss / 纯秒数 统一换算成总秒数;解析不出返回 null。
// 与 dialog-runner._durationToSeconds、后端 _parse_seconds 同口径(三处镜像,改一处须同步)。
function _durationToSeconds(text) {
  const s = ('' + (text || '')).trim();
  if (!s) return null;
  // ① hh:mm:ss / mm:ss 冒号格式
  if (/^\d{1,2}(?::\d{1,2}){1,2}$/.test(s)) {
    return s.split(':').reduce((acc, n) => acc * 60 + parseInt(n, 10), 0);
  }
  // ② 时/分/秒任意组合(各段可缺省):中文 小时/时·分·秒,英文 h·m·s。
  //    英文单位用「后不接字母」lookahead 收尾——避免把 500ms 的 m 误当分钟、s 误当秒。
  let total = 0, matched = false;
  const h = s.match(/(\d+(?:\.\d+)?)\s*(?:小时|小時|時|时|h(?![a-z]))/i);
  if (h) { total += parseFloat(h[1]) * 3600; matched = true; }
  const m = s.match(/(\d+(?:\.\d+)?)\s*(?:分钟|分|m(?![a-z]))/i);
  if (m) { total += parseFloat(m[1]) * 60; matched = true; }
  const c = s.match(/(\d+(?:\.\d+)?)\s*(?:秒|s(?![a-z]))/i);
  if (c) { total += parseFloat(c[1]); matched = true; }
  if (matched) return Math.round(total);
  // ③ 纯数字:视为已是秒
  if (/^\d+(?:\.\d+)?$/.test(s)) return Math.round(parseFloat(s));
  return null;
}

// 从多个候选文本里挑出耗时并换算成纯秒。候选优先级:折叠头 titles(主来源)> messageText(兜底,
// 用于耗时不在 cr-collapse 的场景——探针 probe-workbuddy-duration.js 待确认)。任一候选能解析出
// 「已完成 …」耗时即取;返回 { seconds, raw }(raw=命中的原文,供 runner 落日志作证据)。全不命中→seconds=''。
function pickDurationSeconds({ titles = [], messageText = '' } = {}) {
  const candidates = [...titles];
  // messageText 可能整段(含回答正文),只挑含「已完成 …」的那一行,避免把正文塞进 raw。
  if (messageText) {
    for (const line of String(messageText).split('\n')) {
      if (DONE_MARKER_RE.test(line)) candidates.push(line.trim());
    }
  }
  for (const t of candidates) {
    const s = parseDuration(t);
    if (s) return { seconds: s, raw: t };
  }
  return { seconds: '', raw: candidates.find(Boolean) || '' };
}


const SEL = {
  answer: '.cr-markdown',                          // assistant 回答正文
  messageContent: '.cr-message-list__content',     // 本轮消息容器(耗时兜底候选来源)
  thinkingCollapse: 'section.cr-collapse',         // 思考折叠组件
  thinkingHeader: '.cr-collapse__header',          // 折叠头（点开用）
  thinkingTitle: '.cr-collapse__title',            // 头文本「已完成 Ns」（耗时来源）
  thinkingContent: '.cr-collapse__content-inner',  // 折叠内容（思考正文）
  sourcesTrigger: '.artifact-slot-panel__sources', // "来源"触发（工具证据）
  sourcesCount: '.artifact-slot-panel__source-count',
  sourcesPanelTitle: '.sources-panel__title',      // 「引用来源 (N)」
  sourcesList: '.sources-panel__list',             // 展开后来源条目容器
  footer: '.conversation-finished-footer',
};

class WorkbuddyDomTrace {
  constructor(sel = {}) { this.sel = { ...SEL, ...sel }; this._data = this._empty(); }
  _empty() { return { session_id: null, thinking: '', tool_calls: [], artifacts: [], answer: '', beanCost: '', model: '', reportedDuration: '', reportedDurationRaw: '', shareLink: '' }; }
  reset() { this._data = this._empty(); }
  // 对话分享链接由 runner 抓取(点分享→复制链接→读剪贴板)后塞入,buildTrace 一并回写。
  setShareLink(url) { this._data.shareLink = url || ''; }

  // 抓「本轮」DOM。先尝试点开思考折叠、点开来源面板（懒渲染），再读。
  async captureTurn(page) {
    const sel = this.sel;
    // 0) 等"已完成 Ns"折叠头出现(耗时来源):footer 出现后该折叠头仍可能延迟渲染/定值,
    //    短轮询最多 5s,避免抓耗时过早拿到空(真机坐实:会话静止后才稳定出 "已完成 Ns")。
    //    ⚠️ 本轮可能有多个 cr-collapse(如「已完成 15s」+「深度思考」),耗时不一定在最后一个,
    //    故遍历所有 title、任一匹配即收(不能只看 .last())。
    try {
      for (let i = 0; i < 10; i++) {
        const titles = await page.locator(sel.thinkingTitle).allInnerTexts().catch(() => []);
        if (titles.some(t => DONE_MARKER_RE.test(t || ''))) break;
        await page.waitForTimeout(500);
      }
    } catch (_) {}
    // 1) 展开思考折叠（若存在且折叠）——内容懒渲染，不展开读不到。多个折叠全部展开,确保思考正文渲染。
    try {
      const headers = page.locator(sel.thinkingHeader);
      const n = await headers.count();
      for (let i = 0; i < n; i++) { await headers.nth(i).click({ timeout: 1500 }).catch(() => {}); }
      if (n) await page.waitForTimeout(500);
    } catch (_) {}
    // 2) 展开"来源"面板（若存在）——抓引用来源作工具证据
    let sourcesText = '';
    try {
      const trig = page.locator(sel.sourcesTrigger).last();
      if (await trig.count()) {
        await trig.click({ timeout: 2000 }).catch(() => {});
        await page.waitForTimeout(600);
        const list = page.locator(sel.sourcesList).last();
        if (await list.count()) sourcesText = (await list.innerText().catch(() => '')) || '';
        // 读来源计数标题「引用来源 (N)」
        const titleEl = page.locator(sel.sourcesPanelTitle).last();
        if (await titleEl.count()) { const t = await titleEl.innerText().catch(() => ''); if (t) sourcesText = `${t}\n${sourcesText}`.trim(); }
        await page.keyboard.press('Escape').catch(() => {});  // 关面板
      }
    } catch (_) {}

    const d = await page.evaluate(({ sel, doneMarkerSrc }) => {
      const doneRe = new RegExp(doneMarkerSrc);
      const txt = (el) => el ? (el.innerText || '').trim() : '';
      const answers = Array.from(document.querySelectorAll(sel.answer));
      const answer = answers.length ? txt(answers[answers.length - 1]) : '';       // 最后一条 assistant 正文 = 本轮
      // 思考：本轮可能有多个折叠(如「已完成 15s」+「深度思考」)。耗时 title 在其中一个、
      // 不一定是最后一个 → 遍历所有折叠:耗时取首个匹配「已完成 …」的 title;思考正文合并所有折叠内容。
      const collapses = Array.from(document.querySelectorAll(sel.thinkingCollapse));
      const collapseTitles = [];
      let collapseTitle = '';
      const thinkParts = [];
      for (const c of collapses) {
        const tt = txt(c.querySelector(sel.thinkingTitle));
        if (tt) collapseTitles.push(tt);
        if (!collapseTitle && doneRe.test(tt)) collapseTitle = tt;
        const body = txt(c.querySelector(sel.thinkingContent));
        if (body) thinkParts.push(body);
      }
      // 无「已完成 …」时退回最后一个折叠的 title(不至于全空,便于排障)
      if (!collapseTitle && collapses.length) collapseTitle = txt(collapses[collapses.length - 1].querySelector(sel.thinkingTitle));
      const thinking = thinkParts.join('\n');
      // 本轮整条消息文本:耗时若不在 cr-collapse(真机待确认场景)时的兜底候选来源。
      const msgEls = sel.messageContent ? Array.from(document.querySelectorAll(sel.messageContent)) : [];
      const messageText = msgEls.length ? txt(msgEls[msgEls.length - 1]) : '';
      // 来源计数（用于判断是否用了检索工具）
      const cntEl = document.querySelector(sel.sourcesCount);
      const sourcesCountText = txt(cntEl);
      const footers = Array.from(document.querySelectorAll(sel.footer));
      const footerText = footers.length ? txt(footers[footers.length - 1]) : '';
      return { answer, thinking, collapseTitle, collapseTitles, messageText, sourcesCountText, footerText };
    }, { sel, doneMarkerSrc: DONE_MARKER_RE.source });

    const footer = parseFooter(d.footerText);
    this._data.answer = sanitizeDialogText(d.answer || '');
    this._data.thinking = sanitizeDialogText(d.thinking || '');
    this._data.beanCost = footer.beanCost;
    this._data.model = footer.model;
    // 耗时:折叠头 titles 为主来源,整条消息文本兜底(耗时不在 cr-collapse 时);保留 raw 供 runner 落日志作证据。
    const dur = pickDurationSeconds({ titles: d.collapseTitles || [d.collapseTitle], messageText: d.messageText });
    this._data.reportedDuration = dur.seconds;
    this._data.reportedDurationRaw = dur.raw;
    // 工具证据：WorkBuddy 无结构化工具卡，把"来源"面板规整成一条 web_search 工具调用（决策：抓来源作工具证据）。
    // name=web_search，result_text=来源列表/计数；original_tool_name/args 留空（拿不到 MCP 工具名/参数，属产品形态固有差异）。
    this._data.tool_calls = [];
    const hasSources = /来源|引用来源|搜索技术支持/.test(d.footerText || '') || (sourcesText && sourcesText.trim());
    if (hasSources) {
      this._data.tool_calls.push({
        tool_call_id: '_dom_sources', name: 'web_search', original_tool_name: '',
        is_mcp: false, mcp_server: null, args: undefined,
        result_text: (sourcesText || d.sourcesCountText || '（有来源但未展开到条目）').trim(),
        reached_result: true,
      });
    }
    // artifacts：本轮暂不抓产物卡（Task 1 该场景无产物；产物选择器待后续触发型场景补，此处留空数组占位）。
    this._data.artifacts = [];
  }

  buildTrace(runId) {
    return {
      session_id: this._data.session_id, run_id: runId || null,
      thinking: this._data.thinking, tool_calls: this._data.tool_calls,
      artifacts: this._data.artifacts, answer: this._data.answer,
      ws_captured: false, ws_connected: true, dom_captured: true,
      reported_duration: this._data.reportedDuration, reported_duration_raw: this._data.reportedDurationRaw,
      bean_cost: this._data.beanCost, model: this._data.model,
      share_link: this._data.shareLink || null,
    };
  }
}
module.exports = { WorkbuddyDomTrace, parseFooter, parseDuration, pickDurationSeconds };
