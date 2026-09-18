'use strict';

function isQworkShareUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'qwork.360.cn' && !url.username && !url.password &&
      /^\/share\/[a-zA-Z0-9-]+$/.test(url.pathname);
  } catch { return false; }
}

// 使用客户端自己的序列化/上传流程，完整保留思考、工具和附件，不能自己拼一个只有最后回复的分享。
async function createQworkShare(page, { sessionId, completedTurns, timeout = 45000 }) {
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId) || completedTurns < 1) throw new Error('QWork 没有可分享的完整回合');
  const row = page.locator(`[data-session-id="${sessionId}"]`);
  const expand = page.getByRole('button', { name: '展开侧栏', exact: true });
  const wasCollapsed = await expand.isVisible();
  const exit = page.getByRole('button', { name: '退出分享', exact: true });
  try {
    if (wasCollapsed) await expand.click({ timeout: 5000 });
    if (await exit.isVisible()) await exit.click({ timeout: 5000 });
    await row.getByRole('button').first().click({ timeout: 10000 });
    // 只操作指定会话。并发手动切换时停止分享，避免发布另一条对话。
    const assertSelected = async () => {
      if (!await row.evaluate(el => el.classList.contains('bg-[#e6e6e6]'))) throw new Error('QWork 当前会话已切换，停止分享');
    };
    await assertSelected();
    await page.getByRole('button', { name: '分享任务', exact: true }).click({ timeout: 10000 });
    const panel = page.getByRole('region', { name: '分享任务消息选择', exact: true });
    await panel.waitFor({ timeout: 10000 });
    const all = panel.getByRole('checkbox', { name: '全选', exact: true });
    const turns = page.getByRole('checkbox', { name: /^选择完整回合：/ });
    await page.waitForFunction(expected => document.querySelectorAll('[role="checkbox"][aria-label^="选择完整回合："]').length === expected,
      completedTurns, { timeout: 10000 });
    if (await all.getAttribute('aria-checked') !== 'true') await all.click({ timeout: 5000 });
    if (await all.getAttribute('aria-checked') !== 'true' || await turns.count() !== completedTurns ||
      !await turns.evaluateAll(nodes => nodes.every(el => el.getAttribute('aria-checked') === 'true')))
      throw new Error('QWork 分享未选中全部完整回合，未复制链接');
    await assertSelected();
    // 清除本次复制之前的成功提示，防止把旧剪贴板误认为新结果。
    await page.getByText('链接已复制', { exact: true }).waitFor({ state: 'hidden', timeout: 10000 });
    await panel.getByRole('button', { name: '复制链接', exact: true }).click({ timeout: 5000 });
    const feedback = await page.waitForFunction(() => {
      const statuses = [...document.querySelectorAll('[role="status"]')].filter(el => el.getClientRects().length);
      if (statuses.some(el => el.textContent.trim() === '链接已复制')) return '链接已复制';
      const panel = document.querySelector('[aria-label="分享任务消息选择"]');
      if (panel?.getAttribute('aria-busy') === 'false')
        return statuses.find(el => el.classList.contains('fixed') && el.classList.contains('top-4'))?.textContent.trim() || false;
      return false;
    }, null, { timeout });
    const message = await feedback.jsonValue();
    if (message !== '链接已复制') throw new Error(message);
    const { copied, shares } = await page.evaluate(async () => {
      let timer;
      try {
        const [copied, shares] = await Promise.race([
          Promise.all([window.workGui.clipboard.readText(), window.workGui.shares.list('task', undefined, 100)]),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('分享归属校验超时')), 10000); }),
        ]);
        return { copied, shares };
      } finally { clearTimeout(timer); }
    });
    const url = copied.trim();
    if (!isQworkShareUrl(url)) throw new Error('QWork 未复制有效的对话分享链接');
    const record = shares.items?.find(item => item.url === url && item.sourceTaskSessionId === sessionId);
    if (!record) throw new Error('QWork 分享链接与当前会话不匹配，未回填');
    return { status: 'completed', url, id: record.id, session_id: sessionId, selected_all: true, completed_turns: completedTurns };
  } catch (error) {
    const detail = await page.getByRole('alert').allTextContents().catch(() => []);
    throw new Error(`QWork 分享失败：${detail.join('；') || error.message.split('\n')[0]}`);
  } finally {
    if (await exit.isVisible().catch(() => false)) await exit.click({ timeout: 2000 }).catch(() => {});
    if (wasCollapsed) await page.getByRole('button', { name: '收起侧栏', exact: true }).click({ timeout: 2000 }).catch(() => {});
  }
}

module.exports = { createQworkShare, isQworkShareUrl };
