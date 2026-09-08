// WorkBuddy captureTurn→buildTrace 全链路集成自测(无框架/无浏览器,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/workbuddy-capture-integration.test.js
//
// 为什么要这个:parseDuration 纯函数单测只证「字符串→秒」,不证真实抓取链路(轮询等完成标记→
// 遍历折叠头→pickDurationSeconds→buildTrace.reported_duration)在 ≥60s 长会话下真能回填非空。
// 这里用「假 page」把 captureTurn 依赖的 Playwright 表面(locator/evaluate/waitForTimeout/keyboard)
// 全部最小实现,evaluate 回调在「假 document」上真实执行,坐实整条链路产出正确秒数。

const assert = require('assert');
const { WorkbuddyDomTrace } = require('../src/workbuddy-dom-trace');

// —— 最小假 DOM:元素只需 innerText + querySelector(按注册选择器返回子元素)——
function el(innerText, children = {}) {
  return { innerText, querySelector: (s) => children[s] || null };
}
// 假 document:querySelectorAll(sel) 命中预置 map;querySelector 取首个。
function makeDoc(map) {
  return {
    querySelectorAll: (s) => (map[s] || []),
    querySelector: (s) => (map[s] || [])[0] || null,
  };
}

// 假 page:实现 captureTurn 用到的全部表面。evaluate 在 global.document=假doc 下真跑回调。
function makePage(doc, titlesForLocator) {
  const locator = (sel) => ({
    allInnerTexts: async () => titlesForLocator(sel),
    count: async () => (doc.querySelectorAll(sel) || []).length,
    nth: () => ({ click: async () => {} }),
    last: () => ({ count: async () => 0, click: async () => {}, innerText: async () => '' }),
  });
  return {
    locator,
    waitForTimeout: async () => {},
    keyboard: { press: async () => {} },
    evaluate: async (fn, arg) => {
      const prev = global.document;
      global.document = doc;
      try { return fn(arg); } finally { global.document = prev; }
    },
  };
}

const SEL = {
  answer: '.cr-markdown', messageContent: '.cr-message-list__content',
  thinkingCollapse: 'section.cr-collapse', thinkingHeader: '.cr-collapse__header',
  thinkingTitle: '.cr-collapse__title', thinkingContent: '.cr-collapse__content-inner',
  sourcesTrigger: '.artifact-slot-panel__sources', sourcesCount: '.artifact-slot-panel__source-count',
  sourcesPanelTitle: '.sources-panel__title', sourcesList: '.sources-panel__list',
  footer: '.conversation-finished-footer',
};

// 组一条「≥60s 长会话」页面:折叠1=「已完成 2分43秒」,折叠2=「深度思考」(耗时不在最后一个,复刻真机)
function longSessionDoc() {
  const c1 = el('已完成 2分43秒', {
    [SEL.thinkingTitle]: el('已完成 2分43秒'),
    [SEL.thinkingContent]: el('先分析问题……'),
  });
  const c2 = el('深度思考', {
    [SEL.thinkingTitle]: el('深度思考'),
    [SEL.thinkingContent]: el('再深入推理……'),
  });
  return makeDoc({
    [SEL.answer]: [el('这是最终回答')],
    [SEL.messageContent]: [el('这是最终回答')],
    [SEL.thinkingCollapse]: [c1, c2],
    [SEL.footer]: [el('共消耗\n5\nDeepseek')],
  });
}

async function test_long_session_backfills_duration() {
  const doc = longSessionDoc();
  const trace = new WorkbuddyDomTrace(SEL);
  // locator 的 allInnerTexts 用于轮询等「已完成 …」,喂折叠头文本
  const page = makePage(doc, () => ['已完成 2分43秒', '深度思考']);
  await trace.captureTurn(page);
  const t = trace.buildTrace(0);
  assert.strictEqual(t.reported_duration, '163', `长会话应回填 163 秒,实得 ${t.reported_duration}`);
  assert.strictEqual(t.reported_duration_raw, '已完成 2分43秒', 'raw 原文留档');
  assert.ok((t.thinking || '').includes('深入推理'), '思考正文合并所有折叠');
  assert.ok((t.answer || '').includes('最终回答'), '回答正文抓到');
  console.log('✓ 长会话(2分43秒)整条链路回填 reported_duration=163(非 null)');
}

async function test_duration_outside_collapse_fallback() {
  // 真机待确认场景:耗时不在 cr-collapse,只在消息容器文本里 → 兜底候选命中
  const c = el('深度思考', { [SEL.thinkingTitle]: el('深度思考'), [SEL.thinkingContent]: el('推理') });
  const doc = makeDoc({
    [SEL.answer]: [el('回答')],
    [SEL.messageContent]: [el('回答正文\n已完成 1分5秒\n共消耗 3')],
    [SEL.thinkingCollapse]: [c],
    [SEL.footer]: [el('')],
  });
  const trace = new WorkbuddyDomTrace(SEL);
  const page = makePage(doc, () => ['深度思考']);   // 折叠头无耗时 → 轮询走满不阻塞(waitForTimeout 空实现)
  await trace.captureTurn(page);
  const t = trace.buildTrace(0);
  assert.strictEqual(t.reported_duration, '65', `耗时不在折叠头时应从消息文本兜底=65,实得 ${t.reported_duration}`);
  console.log('✓ 耗时不在折叠头时,从消息文本兜底回填=65');
}

async function main() {
  await test_long_session_backfills_duration();
  await test_duration_outside_collapse_fallback();
  console.log('\n✅ WorkBuddy captureTurn 全链路回填 全部通过');
}
main().catch(e => { console.error(e); process.exit(1); });
