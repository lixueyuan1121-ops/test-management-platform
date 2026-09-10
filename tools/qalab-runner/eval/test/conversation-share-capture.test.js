const { test } = require('node:test');
const assert = require('node:assert/strict');
const DialogRunner = require('../src/dialog-runner');

function runner({ delayedButton = false, clipboard = true, selected = true, direct = '' } = {}) {
  const r = Object.create(DialogRunner.prototype);
  const stats = { buttonWaits: 0, generations: 0, reads: 0, warnings: [] };
  r.platform = { shareBtnSelector: '#share', shareGenerateSelector: '#generate' };
  r._warnShare = msg => stats.warnings.push(msg);
  const button = { first() { return this; }, async waitFor() { if (++stats.buttonWaits === 1 && delayedButton) throw Error('not ready'); }, async click() {} };
  const gen = { first() { return this; }, async waitFor() {}, async click() { stats.generations++; } };
  r._ctx = () => ({ locator: sel => sel === '#share' ? button : sel === '#generate' ? gen : { count: async () => 0 } });
  r._liveFrame = () => ({ evaluate: async () => selected });
  r.page = { keyboard: { press: async () => {} }, waitForTimeout: async () => {},
    evaluate: async fn => {
      if (fn.toString().includes('writeText')) { if (!clipboard) throw Error('denied'); return true; }
      stats.reads++;
      return 'https://work.n.cn/share/fresh-link';
    } };
  r._readShareUrlFromPanel = async () => direct;
  return { r, stats };
}

test('late share button retries and fresh clipboard URL is read immediately', async () => {
  const { r, stats } = runner({ delayedButton: true });
  assert.equal(await r.extractConversationShareLink(), 'https://work.n.cn/share/fresh-link');
  assert.equal(stats.buttonWaits, 2);
  assert.equal(stats.generations, 1);
  assert.equal(stats.reads, 1);
});

test('DOM link works without clipboard permission; old clipboard is never read', async () => {
  const { r, stats } = runner({ clipboard: false, direct: 'https://work.n.cn/share/dom-link' });
  assert.equal(await r.extractConversationShareLink(), 'https://work.n.cn/share/dom-link');
  assert.equal(stats.reads, 0);
});

test('unconfirmed selection never publishes an incomplete conversation', async () => {
  const { r, stats } = runner({ selected: false });
  assert.equal(await r.extractConversationShareLink(), '');
  assert.equal(stats.generations, 0);
  assert(stats.warnings.some(w => w.includes('未能确认全部对话')));
});
