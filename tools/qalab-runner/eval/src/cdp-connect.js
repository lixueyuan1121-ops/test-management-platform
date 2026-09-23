'use strict';
const { chromium } = require('playwright');
const playwrightVersion = require('playwright/package.json').version;
const [major, minor] = playwrightVersion.split('.').map(Number);
const compatibilitySupported = major > 1 || (major === 1 && minor >= 60);
const versionHint = compatibilitySupported ? '' : '；此 Playwright 版本不支持兼容连接，请在 runner 的 eval 目录执行 npm ci 后重启';
const { setTimeout: pause } = require('node:timers/promises');

async function describeCDP(url) {
  try {
    const [version, targets] = await Promise.all(['/json/version', '/json/list'].map(async path => {
      const response = await fetch(new URL(path, url), { signal: AbortSignal.timeout(2000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }));
    const counts = {};
    for (const target of Array.isArray(targets) ? targets : []) counts[target.type || 'unknown'] = (counts[target.type || 'unknown'] || 0) + 1;
    // Only versions/type counts: never log page content, titles, or signed URLs.
    return `Playwright ${playwrightVersion}${versionHint}；内核 ${version.Browser || '未知'}；调试目标 ${JSON.stringify(counts)}`;
  } catch (error) {
    return `Playwright ${playwrightVersion}${versionHint}；调试端口诊断失败：${error.message}`;
  }
}

// A listening debug port only proves HTTP/WebSocket readiness, not renderer readiness.
// Retry attachment before sending any prompt. Never restart/close the user's client here.
async function connectDesktopCDP(url, { product = '客户端', logger, timeout = 45000, retryDelay = 1500, matches } = {}) {
  let diagnostic = '';
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      // Playwright >= 1.60: fallback skips context overrides that some embedded
      // Chromium builds don't answer (download/focus/media emulation commands).
      if (matches) {
        const { connectProductPages } = require('./cdp-target-connection');
        return await connectProductPages(chromium, url, { matches, logger, timeout, noDefaults: attempt === 2 && compatibilitySupported });
      }
      return await chromium.connectOverCDP(url, { timeout, ...(attempt === 2 && compatibilitySupported ? { noDefaults: true } : {}) });
    } catch (error) {
      if (!diagnostic) diagnostic = await describeCDP(url);
      const retryable = error.name === 'TimeoutError' || /timeout|timed out|ECONNRESET|ECONNREFUSED|socket hang up|Target closed|未响应/i.test(error.message);
      if (attempt === 1 && retryable) {
        logger?.warn(`${product} CDP 初始化失败，稍后${compatibilitySupported ? '使用兼容连接' : ''}重试（2/2）：${error.message.split('\n')[0]}；${diagnostic}`);
        await pause(retryDelay);
        continue;
      }
      throw new Error(`${product} CDP 连接失败（${url}，尝试 ${attempt} 次），尚未发送测评提问。请确认该产品主窗口已打开且可正常操作，再重跑失败用例。诊断：${diagnostic}。原始错误：${error.message}`, { cause: error });
    }
  }
}
module.exports = { connectDesktopCDP, describeCDP };
