'use strict';
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { createQworkShare } = require('../src/qwork-share');
const { allowQworkPermission } = require('../src/qwork-permissions');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function fixture(options, fn) {
  const page = await browser.newPage();
  await page.setContent(`<button aria-label="收起侧栏" id="sidebar">sidebar</button>
    <div data-session-id="s1"><button id="select">own session</button></div>
    <button aria-label="分享任务" id="share">share</button>
    <div id="content"></div><div role="status" id="feedback"></div>`);
  await page.evaluate(options => {
    let selected = !!options.initialChecked;
    window.clicks = { selectAll: 0, copy: 0, allow: 0 };
    window.workGui = { clipboard: { readText: async () => options.badClipboard ? 'https://work.n.cn/share/old' : 'https://qwork.360.cn/share/new' },
      shares: { list: async () => ({ items: [{ id: 'share1', url: 'https://qwork.360.cn/share/new', sourceTaskSessionId: options.wrongOwner ? 's2' : 's1' }] }) } };
    const row = document.querySelector('[data-session-id]'), sidebar = document.getElementById('sidebar');
    const collapsed = value => { row.hidden = value; sidebar.setAttribute('aria-label', value ? '展开侧栏' : '收起侧栏'); };
    collapsed(!!options.collapsed);
    sidebar.onclick = () => collapsed(!row.hidden);
    document.getElementById('select').onclick = () => row.classList.add('bg-[#e6e6e6]');
    document.getElementById('share').onclick = () => {
      document.getElementById('feedback').textContent = '';
      document.getElementById('content').innerHTML = `<button role="checkbox" aria-label="选择完整回合：第一轮" aria-checked="${selected}"></button>
        <button role="checkbox" aria-label="选择完整回合：第二轮" aria-checked="${selected}"></button>
        <section role="region" aria-label="分享任务消息选择"><button role="checkbox" aria-label="全选" aria-checked="${selected}" id="all"></button>
        <button id="copy">复制链接</button><button aria-label="退出分享" id="exit">退出</button></section>`;
      document.getElementById('all').onclick = () => {
        window.clicks.selectAll++; selected = !selected;
        document.querySelectorAll('[role="checkbox"]').forEach(el => el.setAttribute('aria-checked', String(selected)));
      };
      document.getElementById('exit').onclick = () => { document.getElementById('content').innerHTML = ''; };
      document.getElementById('copy').onclick = () => {
        window.clicks.copy++;
        if (options.noCopy) return;
        if (options.copyError) {
          document.querySelector('section').setAttribute('aria-busy', 'false');
          const feedback = document.getElementById('feedback');
          feedback.className = 'fixed top-4'; feedback.textContent = '分享服务连接失败'; return;
        }
        document.getElementById('content').innerHTML = '';
        document.getElementById('feedback').textContent = '链接已复制';
      };
    };
  }, options);
  try { await fn(page); } finally { await page.close(); }
}

test('分享全选：未勾选才点击，已勾选不反选，支持折叠侧栏并恢复', async () => {
  for (const initialChecked of [false, true]) await fixture({ initialChecked, collapsed: !initialChecked }, async page => {
    const result = await createQworkShare(page, { sessionId: 's1', completedTurns: 2 });
    assert.equal(result.url, 'https://qwork.360.cn/share/new');
    assert.equal(result.completed_turns, 2);
    assert.equal(result.selected_all, true);
    assert.deepEqual(await page.evaluate(() => window.clicks), { selectAll: initialChecked ? 0 : 1, copy: 1, allow: 0 });
    assert.equal(await page.getByRole('button', { name: initialChecked ? '收起侧栏' : '展开侧栏' }).count(), 1);
  });
});

test('错误产品链接、其他会话、复制超时不能回填旧剪贴板', async () => {
  for (const [options, error] of [[{ badClipboard: true }, /有效/], [{ wrongOwner: true }, /不匹配/],
    [{ noCopy: true }, /分享失败/], [{ copyError: true }, /分享服务连接失败/]])
    await fixture(options, async page => {
      await assert.rejects(createQworkShare(page, { sessionId: 's1', completedTurns: 2, timeout: 80 }), error);
      assert.equal(await page.getByRole('button', { name: '退出分享' }).count(), 0);
    });
});

test('连续出现相同命令的不同确认请求，逐次允许，不误判弹窗未关闭', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div data-session-id="s1" class="bg-[#e6e6e6]"><button>own task</button></div><section role="region" aria-label="权限询问"><pre>{"command":"fixture"}</pre><button>允许</button></section>');
    await page.evaluate(() => {
      window.pending = [{ request_id: 'p1', input: { command: 'fixture' } }]; window.allowed = [];
      window.workGui = { sessions: { pendingPermissions: async () => window.pending } };
      document.querySelector('section button').onclick = () => {
        window.allowed.push(window.pending[0].request_id);
        if (window.allowed.length === 1) window.pending = [{ request_id: 'p2', input: { command: 'fixture' } }];
        else { window.pending = []; document.querySelector('section').remove(); }
      };
    });
    for (const request_id of ['p1', 'p2'])
      await allowQworkPermission(page, { session_id: 's1', request_id, input: { command: 'fixture' } });
    assert.deepEqual(await page.evaluate(() => window.allowed), ['p1', 'p2']);
  } finally { await page.close(); }
});

test('原生权限弹窗只允许匹配操作，等待确认和弹窗关闭', async () => {
  const page = await browser.newPage();
  const request = { session_id: 's1', request_id: 'p1', input: { command: 'fixture command' }, confirmation_once: true };
  try {
    await page.setContent('<div data-session-id="s1" class="bg-[#e6e6e6]"><button>own task</button></div><section role="region" aria-label="权限询问"><pre>{"command":"fixture command"}</pre><button>允许</button></section>');
    await page.evaluate(() => {
      window.pending = [{ request_id: 'p1' }]; window.allowed = 0;
      window.workGui = { sessions: { pendingPermissions: async () => window.pending } };
      document.querySelector('section button').onclick = () => { window.allowed++; window.pending = []; document.querySelector('section').remove(); };
    });
    await assert.rejects(allowQworkPermission(page, { ...request, input: { command: 'different' } }), /不一致/);
    assert.equal(await page.evaluate(() => window.allowed), 0);
    await allowQworkPermission(page, request);
    assert.equal(await page.evaluate(() => window.allowed), 1);
    assert.equal(await page.getByRole('region', { name: '权限询问' }).count(), 0);
  } finally { await page.close(); }
});
