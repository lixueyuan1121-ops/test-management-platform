'use strict';
// 独立端口、独立进程、独立连接校验，不复用 WorkBuddy/纳米的启动策略。
const childProcess = require('node:child_process');
const http = require('node:http');
const { connectDesktopCDP } = require('./cdp-connect');
const { resolveExecutable } = require('./electron-executable');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

class QworkPool {
  constructor(config = {}, logger = null) {
    this.config = config; this.logger = logger;
    this.cdpUrl = `http://${config.cdpHost || '127.0.0.1'}:${config.cdpPort || 9336}`;
  }
  async _ready() {
    return new Promise(resolve => {
      const req = http.get(`${this.cdpUrl}/json/version`, res => {
        let body = ''; res.on('data', d => body += d);
        res.on('end', () => { try { resolve(!!JSON.parse(body).webSocketDebuggerUrl); } catch { resolve(false); } });
      });
      req.on('error', () => resolve(false)); req.setTimeout(1500, () => req.destroy());
    });
  }
  async init() {
    if (!await this._ready()) {
      if (this.config.attachOnly) throw new Error(`QWork 调试端口未就绪：${this.cdpUrl}`);
      const exe = this.config.executablePath || (process.platform === 'darwin' ? '/Applications/QWork.app' : '');
      if (!exe) throw new Error('请设置 QWORK_EXE 为本机 QWork 可执行文件路径');
      let resolved;
      try { resolved = resolveExecutable(exe); }
      catch (error) { throw new Error(error.message.replaceAll('WORKBUDDY_EXE', 'QWORK_EXE')); }
      this.launchError = null;
      await new Promise((resolve, reject) => {
        const child = childProcess.spawn(resolved, [`--remote-debugging-port=${this.config.cdpPort || 9336}`, '--remote-debugging-address=127.0.0.1',
          `--inspect=127.0.0.1:${this.config.inspectPort || 9337}`],
          { detached: true, stdio: 'ignore', env: process.env });
        child.on('error', error => { this.launchError = error; reject(error); });
        child.once('exit', () => { this.launchError ||= new Error('QWork 启动进程已退出；若已手动打开，请退出后由 runner 启动以启用调试端口'); });
        child.once('spawn', () => { child.unref(); resolve(); });
      });
      const deadline = Date.now() + (this.config.launchTimeout || 60000);
      while (!await this._ready()) {
        if (this.launchError) throw this.launchError;
        if (Date.now() >= deadline) throw new Error(`QWork 调试端口未就绪：${this.cdpUrl}`);
        await pause(500);
      }
    }
    this.browser = await connectDesktopCDP(this.cdpUrl, { product: 'QWork', logger: this.logger });
    const deadline = Date.now() + (this.config.readyTimeout || 30000);
    do {
      for (const context of this.browser.contexts()) for (const page of context.pages()) {
        if (await page.evaluate(() => !!(window.workGui?.sessions?.history && window.workGui?.events?.onSessionEvent)).catch(() => false)) {
          this.mainPage = page; this.context = context;
          this.logger?.info(`已连接 QWork：${this.cdpUrl}`); return;
        }
      }
      await pause(300);
    } while (Date.now() < deadline);
    await this.close();
    throw new Error('端口中的页面不是支持原生会话采集的 QWork，未下发任务');
  }
  getMainPage() { return this.mainPage; }
  getContext() { return this.context; }
  async close() { await this.browser?.close().catch(() => {}); }
}
module.exports = { QworkPool };
