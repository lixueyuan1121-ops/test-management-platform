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

// 从思考折叠头文本「已完成 17s」抽耗时（秒，字符串）。无完成标记返回空串。纯函数，导出供单测。
function parseDuration(text) {
  if (!text) return '';
  const m = text.match(/已完成\s*(\d+)\s*s/);
  return m ? m[1] : '';
}

// 选择器（Task 1 真机坐实）。构造时可覆盖（走 config.workbuddy）。
const SEL = {
  answer: '.cr-markdown',                          // assistant 回答正文
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
  _empty() { return { session_id: null, thinking: '', tool_calls: [], artifacts: [], answer: '', beanCost: '', model: '', reportedDuration: '' }; }
  reset() { this._data = this._empty(); }

  // 抓「本轮」DOM。先尝试点开思考折叠、点开来源面板（懒渲染），再读。
  async captureTurn(page) {
    const sel = this.sel;
    // 1) 展开思考折叠（若存在且折叠）——内容懒渲染，不展开读不到
    try {
      const header = page.locator(sel.thinkingHeader).last();
      if (await header.count()) { await header.click({ timeout: 2000 }).catch(() => {}); await page.waitForTimeout(500); }
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

    const d = await page.evaluate((sel) => {
      const txt = (el) => el ? (el.innerText || '').trim() : '';
      const answers = Array.from(document.querySelectorAll(sel.answer));
      const answer = answers.length ? txt(answers[answers.length - 1]) : '';       // 最后一条 assistant 正文 = 本轮
      // 思考：最后一个折叠组件的内容 + 头文本（耗时）
      const collapses = Array.from(document.querySelectorAll(sel.thinkingCollapse));
      const lastCollapse = collapses[collapses.length - 1] || null;
      const thinking = lastCollapse ? txt(lastCollapse.querySelector(sel.thinkingContent)) : '';
      const collapseTitle = lastCollapse ? txt(lastCollapse.querySelector(sel.thinkingTitle)) : '';
      // 来源计数（用于判断是否用了检索工具）
      const cntEl = document.querySelector(sel.sourcesCount);
      const sourcesCountText = txt(cntEl);
      const footers = Array.from(document.querySelectorAll(sel.footer));
      const footerText = footers.length ? txt(footers[footers.length - 1]) : '';
      return { answer, thinking, collapseTitle, sourcesCountText, footerText };
    }, sel);

    const footer = parseFooter(d.footerText);
    this._data.answer = sanitizeDialogText(d.answer || '');
    this._data.thinking = sanitizeDialogText(d.thinking || '');
    this._data.beanCost = footer.beanCost;
    this._data.model = footer.model;
    this._data.reportedDuration = parseDuration(d.collapseTitle);
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
      reported_duration: this._data.reportedDuration, bean_cost: this._data.beanCost, model: this._data.model,
    };
  }
}
module.exports = { WorkbuddyDomTrace, parseFooter, parseDuration };
