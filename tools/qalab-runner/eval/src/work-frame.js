// src/work-frame.js —— 对话 UI 定位自适应:纳米 Work 设备对话界面有的在跨域 iframe 内、
// 有的直接在主文档。统一在此判定并选出"操作上下文(ctx)":有匹配 iframe→frameLocator;无→page。
'use strict';

const DEFAULT_IFRAME_SEL = 'iframe[src*=".work.n.cn"]';

// 页面是否存在匹配的 work.n.cn iframe(异步查 DOM)。
async function hasWorkIframe(page, iframeSel = DEFAULT_IFRAME_SEL) {
  try { return (await page.locator(iframeSel).count()) > 0; }
  catch (_) { return false; }
}

// 选操作上下文:有 iframe 用 frameLocator(第一个),否则用 page 主文档。
// 返回对象都支持 .locator(sel),下游用法一致。
function pickCtx(page, iframeSel = DEFAULT_IFRAME_SEL, hasIframe = false) {
  return hasIframe ? page.frameLocator(iframeSel).first() : page;
}

// evaluate 用的实 Frame(FrameLocator 无 evaluate)。找 <vm>.work.n.cn 的 Frame;
// 主文档形态下 mainFrame.url 本身就是 <vm>.work.n.cn,正则能匹配;兜底 mainFrame。
function liveFrame(page) {
  const f = page.frames().find(fr => /^https?:\/\/[a-z0-9]+\.work\.n\.cn/i.test(fr.url()));
  return f || page.mainFrame();
}

// 判断一个 URL 是不是"work.n.cn 主对话页"(供 _resolveMainPage 选主 page)。
// 用 hostname 结尾匹配(而非整串子串),排除 recovery.html?url=...work.n.cn... 的误判。
function isWorkMainPage(page, url) {
  const u = url || (page && page.url && page.url()) || '';
  try {
    const parsed = new URL(u);
    if (!/(^|\.)work\.n\.cn$/i.test(parsed.hostname)) return false;
    if (/recovery\.html$/i.test(parsed.pathname)) return false;
    return true;
  } catch (_) { return false; }
}

module.exports = { DEFAULT_IFRAME_SEL, hasWorkIframe, pickCtx, liveFrame, isWorkMainPage };
