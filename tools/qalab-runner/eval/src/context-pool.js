const { chromium } = require('playwright');

class ContextPool {
  constructor(accountConfigs, browserConfig) {
    this.accountConfigs = accountConfigs;
    this.maxConcurrent = browserConfig.maxConcurrent || 5;
    this.headless = browserConfig.headless !== false;
    this.viewport = browserConfig.viewport || { width: 1440, height: 900 };
    this.channel = browserConfig.channel || '';
    this.launchArgs = browserConfig.launchArgs ||
      ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'];
    // 有头模式（单账号串行调试）下禁止「有头崩溃 → 无头」的静默降级：
    // 有头启动失败即报错退出，确保看到的确实是有头浏览器，而非悄悄变回无头跑完。
    this.allowHeadlessFallback = browserConfig.allowHeadlessFallback !== false;
    this.browser = null;
    this.contexts = new Map();
  }

  async init() {
    // 该环境实测：有头浏览器启动即崩溃（堆损坏 0xC0000374，多见于无桌面会话 / 安全软件注入），
    // 无头可用。按「内置 Chromium → 系统 Chrome → 系统 Edge」依次尝试；
    // 若有头全部失败：默认强制无头兜底（无头在绝大多数环境可用），避免整个任务崩掉；
    // 但有头模式（allowHeadlessFallback=false）不做无头兜底，失败即报错——见 init 末尾。
    const channels = this.channel ? [this.channel] : ['', 'chrome', 'msedge'];
    const attempts = [];
    for (const ch of channels) {
      attempts.push({ label: ch ? `系统 ${ch}` : '内置 Chromium', channel: ch, headless: this.headless });
    }
    if (!this.headless && this.allowHeadlessFallback) {
      attempts.push({ label: '内置 Chromium（无头兜底）', channel: '', headless: true });
    }

    const errors = [];
    for (let i = 0; i < attempts.length; i++) {
      const a = attempts[i];
      try {
        const opts = { headless: a.headless, args: this.launchArgs };
        if (a.channel) opts.channel = a.channel;
        this.browser = await chromium.launch(opts);
        if (i > 0) {
          const degraded = !this.headless && a.headless ? '（已自动切换到无头模式）' : '';
          console.log(`   ⚠ 首选浏览器启动失败，已回退到「${a.label}」${degraded}`);
        }
        return;
      } catch (e) {
        const mode = a.headless ? '无头' : '有头';
        errors.push(`  · ${a.label}[${mode}]：${String(e.message).split('\n')[0]}`);
      }
    }

    // 有头模式（禁用无头降级）：给出针对性提示——不静默变无头，让用户明确知道有头启动失败了。
    if (!this.headless && !this.allowHeadlessFallback) {
      throw new Error(
        '有头浏览器启动失败（有头模式已禁用无头降级，不会静默变回无头）：\n' + errors.join('\n') + '\n' +
        '有头模式需要真实桌面会话且浏览器内核能正常启动。排查建议：\n' +
        '  1) 确认在有桌面会话的机器上运行（远程/服务会话、无桌面环境下有头会崩溃 0xC0000374）\n' +
        '  2) 重装内置内核：npx playwright install --force chromium\n' +
        '  3) 临时关闭杀毒 / EDR 对浏览器的实时防护后重试\n' +
        '  4) 若本机确实无法有头，请去掉 --headed 改用无头并发模式'
      );
    }

    throw new Error(
      '浏览器启动失败，已尝试的方案均未成功：\n' + errors.join('\n') + '\n' +
      '排查建议：\n' +
      '  1) 优先用无头模式：run --headless true（或 config.browser.headless=true）——本机有头启动会崩溃\n' +
      '  2) 重装内置内核：npx playwright install --force chromium\n' +
      '  3) 临时关闭杀毒 / EDR 对浏览器的实时防护后重试'
    );
  }

  // 返回缓存的 context Promise；同账号并发调用复用同一个，避免竞态创建出多个 context 泄漏
  getContext(accountName) {
    if (this.contexts.has(accountName)) {
      return this.contexts.get(accountName);
    }

    let config = this.accountConfigs.find(a => a.name === accountName);
    if (!config) config = this.accountConfigs[0];
    if (!config) throw new Error('没有配置任何账号');

    const contextPromise = (async () => {
      const context = await this.browser.newContext({
        storageState: config.storageState,
        viewport: this.viewport,
        locale: 'zh-CN'
      });
      // 分享链接需从系统剪贴板读取，授予剪贴板权限（主 frame 读取时生效）
      await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
      return context;
    })();

    this.contexts.set(accountName, contextPromise);
    return contextPromise;
  }

  async runConcurrently(tasks, worker) {
    const results = [];
    const executing = [];

    for (const task of tasks) {
      const p = Promise.resolve().then(() => worker(task));
      results.push(p);

      if (this.maxConcurrent <= tasks.length) {
        const e = p.then(() => executing.splice(executing.indexOf(e), 1));
        executing.push(e);
        if (executing.length >= this.maxConcurrent) {
          await Promise.race(executing);
        }
      }
    }

    return Promise.all(results);
  }

  async close() {
    for (const contextPromise of this.contexts.values()) {
      try {
        const ctx = await contextPromise;
        await ctx.close();
      } catch (_) {}
    }
    if (this.browser) await this.browser.close();
  }
}

module.exports = ContextPool;
