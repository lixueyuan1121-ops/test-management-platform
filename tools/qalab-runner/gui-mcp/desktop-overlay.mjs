import { createScopedTransport } from './cdp-connection.mjs';

// A desktop portal belongs to the selected main window only when its exact
// entry point and origin agree. Voice, other tenants and arbitrary tabs cannot
// participate in a locator lookup.
export function selectDesktopOverlay(targets, mainUrl) {
  let main;
  try { main = new URL(mainUrl); } catch { return null; }
  if (!['http:', 'https:'].includes(main.protocol) ||
      !(main.hostname === 'work.n.cn' || main.hostname.endsWith('.work.n.cn'))) return null;
  const matches = targets.filter(t => {
    try {
      const u = new URL(t.url);
      return t.type === 'page' && u.origin === main.origin && u.pathname === '/desktop-overlay.html';
    } catch { return false; }
  });
  if (matches.length > 1) throw Object.assign(new Error('客户端同源桌面浮层不唯一，不能确定菜单所属窗口'), { code: 'CDP_OVERLAY_AMBIGUOUS' });
  return matches[0] || null;
}

export function createDesktopOverlay({ chromium, cdpUrl, getMainPage, fetchImpl = globalThis.fetch, transportFactory = createScopedTransport }) {
  let browser, targetId, cachedAt = 0, cachedOrigin = '', electron;
  const get = async path => {
    const r = await fetchImpl(cdpUrl.replace(/\/$/, '') + path, { signal: AbortSignal.timeout(3000), redirect: 'error' });
    if (!r.ok) throw new Error(`桌面浮层探测 ${path} 返回 HTTP ${r.status}`);
    return r.json();
  };
  const close = async () => {
    try { await browser?.close(); } finally { browser = null; targetId = null; cachedAt = 0; cachedOrigin = ''; }
  };
  return {
    close,
    async scopes() {
      const main = getMainPage();
      if (!main || main.isClosed() || !/^https?:/.test(cdpUrl)) return [];
      const origin = new URL(main.url()).origin;
      if (electron === undefined) electron = /Electron\//i.test((await get('/json/version'))['User-Agent'] || '');
      if (!electron) return [];
      if (origin !== cachedOrigin || Date.now() - cachedAt > 750 || (browser && !browser.isConnected())) {
        const target = selectDesktopOverlay(await get('/json/list'), main.url());
        if (target?.id !== targetId || (browser && !browser.isConnected())) {
          await close();
          if (target) {
            const version = await get('/json/version');
            const transport = await transportFactory(version.webSocketDebuggerUrl, target.id, { timeout: 5000 });
            try { browser = await chromium.connectOverCDP(transport, { timeout: 5000 }); }
            catch (error) { transport.close(); throw error; }
            targetId = target.id;
          }
        }
        cachedAt = Date.now(); cachedOrigin = origin;
      }
      if (!browser) return [];
      const pages = browser.contexts().flatMap(c => c.pages()).filter(p => !p.isClosed());
      if (pages.length !== 1 || !selectDesktopOverlay([{id:targetId,type:'page',url:pages[0].url()}], main.url())) return [];
      // Search descendants, excluding the overlay document's body/html. This
      // keeps main-document assertions unique while still detecting duplicate
      // menu controls across the main document and the desktop portal.
      return [{ scope: pages[0].locator('body'), name: 'desktop-overlay' }];
    },
  };
}
