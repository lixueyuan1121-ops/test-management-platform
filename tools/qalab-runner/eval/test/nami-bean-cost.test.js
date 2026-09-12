const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const DialogRunner = require('../src/dialog-runner');
const DesktopRunner = require('../src/desktop-runner');

// Run the actual DOM callback against successive answer groups. No avatar,
// ledger, clipboard or click API is provided: this path must only read the DOM.
function fixture(snapshots, { baseGroups = 0, timeout = 1000, reload = false } = {}) {
  const r = new DialogRunner(null, {}, { beanCostTimeoutMs: timeout, beanCostRetryGapMs: 1, beanCostReloadOnce: reload });
  const stats = { reads: 0, waits: 0, warnings: [], users: ['question'], url: 'https://vm.work.n.cn/chat?id=1' };
  r.logger = { info() {}, warn: msg => stats.warnings.push(msg) };
  r._turnBaseline = { groupCount: baseGroups };
  r.page = { waitForTimeout: async () => { stats.waits++; } };
  r._liveFrame = () => ({ evaluate: async (fn, args) => {
    const snapshot = snapshots[Math.min(args.userSel ? Math.max(0, stats.reads - 1) : stats.reads++, snapshots.length - 1)];
    if (snapshot instanceof Error) throw snapshot;
    const document = { querySelectorAll: selector => {
      if (selector === '.chat-group.user') return stats.users.map(textContent => ({ textContent }));
      assert.equal(selector, '.chat-group.assistant');
      return snapshot.map(text => ({ querySelectorAll: selector => {
        assert.equal(selector, '.chat-token-cost');
        return text == null ? [] : [{ textContent: text }];
      } }));
    } };
    return vm.runInNewContext(`(${fn.toString()})(args)`, { document, args, location: { href: stats.url } });
  } });
  return { r, stats };
}

test('reads screenshot consumption, not the token number', async () => {
  const raw = '本次回答消耗：279.9K tokens（23 算力豆）（边想边做）';
  const { r, stats } = fixture([[raw]]);
  assert.deepEqual(await r.extractBeanCost(), { value: '23', raw });
  assert.equal(stats.waits, 0);
});

test('waits for delayed bean count after the token footer appears', async () => {
  const { r, stats } = fixture([
    [null], ['本次回答消耗：279.9K tokens'],
    ['本次回答消耗：279.9K tokens（-- 算力豆）'],
    ['本次回答消耗：279.9K tokens（23 算力豆）'],
  ]);
  assert.equal((await r.extractBeanCost()).value, '23');
  assert.equal(stats.waits, 3);
});

test('latest turn never falls back to an earlier answer cost', async () => {
  const old = '本次回答消耗：8K tokens（99 算力豆）';
  const { r, stats } = fixture([
    [old], [old, null], [old, '本次回答消耗：9K tokens（7 算力豆）'],
  ], { baseGroups: 1 });
  assert.equal((await r.extractBeanCost()).value, '7');
  assert.equal(stats.waits, 2);
});

test('missing current footer times out empty instead of copying old cost or zero', async () => {
  const { r, stats } = fixture([['本次回答消耗：8K tokens（99 算力豆）', null]],
    { baseGroups: 1, timeout: 0 });
  assert.deepEqual(await r.extractBeanCost(), { value: '', raw: '' });
  assert.equal(stats.reads, 1);
  assert.equal(stats.warnings.length, 1);
});

test('polling respects the configured deadline instead of waiting indefinitely', async t => {
  const { r, stats } = fixture([['本次回答消耗：1K tokens']], { timeout: 2500 });
  let now = 0;
  t.mock.method(Date, 'now', () => now);
  r.execution.beanCostRetryGapMs = 1000;
  const waits = [];
  r.page.waitForTimeout = async ms => { waits.push(ms); now += ms; };
  assert.equal((await r.extractBeanCost()).value, '');
  assert.deepEqual(waits, [1000, 1000, 500]);
  assert.equal(stats.reads, 4);
});

test('keeps zero and parses decimals, grouping commas and DOM whitespace', async () => {
  for (const [text, expected] of [
    ['本次回答消耗：12K tokens (0 算力豆)', '0'],
    ['本次回答消耗：12K tokens（1,234.5\n算力豆）', '1234.5'],
    ['本次回答消耗：12K tokens（\n0.25\u00a0算力豆\n）', '0.25'],
  ]) {
    const { r } = fixture([[text]]);
    assert.equal((await r.extractBeanCost()).value, expected);
  }
});

test('tokens, placeholders and invalid quantities are not bean values', async () => {
  for (const text of ['本次回答消耗：279.9K tokens', '（-- 算力豆）', '（-23 算力豆）', '（1,23 算力豆）']) {
    const { r } = fixture([[text]], { timeout: 0 });
    assert.equal((await r.extractBeanCost()).value, '');
  }
});

test('re-resolves DOM after a transient frame error', async () => {
  const { r, stats } = fixture([new Error('frame detached'), ['本次回答消耗：1K tokens（3 算力豆）']]);
  assert.equal((await r.extractBeanCost()).value, '3');
  assert.equal(stats.waits, 1);
});

for (const embedded of [false, true]) {
  test(`refreshes ${embedded ? 'only the work iframe' : 'the main page'} when consumption needs a reload`, async () => {
    const { r, stats } = fixture([['本次回答消耗：1K tokens'], ['本次回答消耗：1K tokens（23 算力豆）']],
      { timeout: 0, reload: true });
    const frame = r._liveFrame();
    r._liveFrame = () => frame;
    let refreshes = 0, rebound = 0;
    frame.url = () => stats.url;
    r.page.mainFrame = () => embedded ? {} : frame;
    const refresh = async () => { refreshes++; };
    if (embedded) frame.goto = async url => { assert.equal(url, stats.url); await refresh(); };
    else r.page.reload = refresh;
    r._waitForFrame = async () => { rebound++; return frame; };
    assert.equal((await r.extractBeanCost()).value, '23');
    assert.equal(refreshes, 1);
    assert.equal(rebound, 1);
    assert.equal(r.frame, frame);
  });
}

test('reload returning a different conversation never fills its bean cost', async () => {
  const { r, stats } = fixture([['本次回答消耗：1K tokens'], ['本次回答消耗：1K tokens（99 算力豆）']],
    { timeout: 0, reload: true });
  const frame = r._liveFrame();
  r._liveFrame = () => frame;
  r.page.mainFrame = () => frame;
  r.page.reload = async () => { stats.users = ['different question']; };
  r._waitForFrame = async () => frame;
  assert.equal((await r.extractBeanCost()).value, '');
  assert.equal(stats.reads, 1, 'must not read consumption from the mismatched conversation');
  assert.equal((await r.extractBeanCost({ reload: false })).value, '');
  assert.equal(stats.reads, 1, 'refill must continue checking the original conversation');
});

test('refill can suppress another reload and reload failures keep consumption empty', async () => {
  const { r } = fixture([['本次回答消耗：1K tokens']], { timeout: 0, reload: true });
  const frame = r._liveFrame();
  r._liveFrame = () => frame;
  r.page.mainFrame = () => frame;
  let refreshes = 0;
  r.page.reload = async () => { refreshes++; throw new Error('reload failed'); };
  assert.equal((await r.extractBeanCost({ reload: false })).value, '');
  assert.equal(refreshes, 0);
  assert.equal((await r.extractBeanCost()).value, '');
  assert.equal(refreshes, 1);
});

test('intermediate turns require beans while continuing to skip share panels', () => {
  const { r } = fixture([]);
  r._skipPanelFields = true;
  r.execution.requiredFields = ['conversationShareLink', 'artifactShareLink', 'beanCost'];
  assert.deepEqual(r._missingFields({ hasArtifact: true }), ['beanCost']);
  assert.deepEqual(r._missingFields({ hasArtifact: true, beanCost: '0' }), []);
});

test('desktop intermediate turn captures consumption and refills without sharing', async () => {
  const { r: dr } = fixture([['本次回答消耗：279.9K tokens（23 算力豆）']]);
  dr.extractLastAnswer = async () => ({ text: 'answer', fromBubble: true });
  dr.extractCost = async () => ({ cost: '279.9K tokens', raw: 'tokens' });
  dr.extractReportedDuration = async () => ({ value: '162', raw: '2m42s' });
  dr._withCritical = fn => fn();
  let refills = 0;
  dr._refillEmptyFields = async out => { refills++; assert.equal(out.beanCost, '23'); };
  const desktop = Object.create(DesktopRunner.prototype);
  desktop.dr = dr;
  desktop._fl = () => ({});
  desktop.execution = { copyAnswer: false };
  const result = await desktop._extractCurrent({ question: 'question' }, { skipPanels: true });
  assert.equal(result.beanCost, '23');
  assert.equal(result.cost, '279.9K tokens');
  assert.equal(result.shareLink, '');
  assert.equal(refills, 1);
  desktop.execution.answerOnly = true;
  assert.equal((await desktop._extractCurrent({ question: 'question' })).beanCost, '');
  assert.equal(refills, 1);
});
