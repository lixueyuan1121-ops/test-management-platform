// src/workbuddy-pool.js
// WorkBuddy Electron 客户端的 CDP 连接池（独立于 DesktopPool，零改动纳米链路）。
// WorkBuddy 单 page 单 frame（本地 asar React UI），无 work.n.cn iframe、无 clawDeviceService。
const childProcess = require('child_process');
const http = require('http');
const { connectDesktopCDP } = require('./cdp-connect');
const { resolveExecutable } = require('./electron-executable');

class WorkbuddyPool {
  constructor(desktopConfig = {}, logger = null) {
    const d = desktopConfig;
    this.executablePath = d.executablePath || '/Applications/WorkBuddy.app';
    this.envPort = d.envPort || 'WORKBUDDY_REMOTE_DEBUGGING_PORT';  // WorkBuddy 用环境变量开调试端口
    this.cdpHost = d.cdpHost || '127.0.0.1';
    this.cdpPort = d.cdpPort || 9335;
    this.launchTimeout = d.launchTimeout || 75000;   // WorkBuddy 首屏偏慢，给足
    this.readyTimeout = d.readyTimeout || 30000;
    this.killExisting = d.killExisting !== false;
    this.logger = logger;
    this.browser = null; this.context = null; this.mainPage = null; this._launched = false;
    this._launchError = null;
  }
  get cdpUrl() { return `http://${this.cdpHost}:${this.cdpPort}`; }
  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }
  _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  _fetchJson(url, timeout = 2500) {
    return new Promise((resolve, reject) => {
      const req = http.get(url, (res) => { let b = ''; res.on('data', d => b += d); res.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } }); });
      req.on('error', reject); req.setTimeout(timeout, () => req.destroy(new Error('timeout')));
    });
  }
  async _portReady() { try { await this._fetchJson(`${this.cdpUrl}/json/version`); return true; } catch { return false; } }
  async _waitPort(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (this._launchError) throw this._launchError;
      const ready = await this._portReady();
      if (this._launchError) throw this._launchError;
      if (ready) return true;
      await this._sleep(500);
    }
    if (this._launchError) throw this._launchError;
    throw new Error(`WorkBuddy 调试端口 ${this.cdpPort} 未就绪超时`);
  }
  _killExisting() {
    try { childProcess.execFileSync('pkill', ['-9', '-f', this.executablePath], { stdio: 'ignore' }); } catch (_) {}
  }
  _resolveExecutable() {
    const resolved = resolveExecutable(this.executablePath);
    if (resolved !== this.executablePath) this._log(`   WorkBuddy 应用路径解析为：${resolved}`);
    this.executablePath = resolved;
  }
  async _spawnClient() {
    this._resolveExecutable();
    this._launched = false;
    this._launchError = null;
    // spawn 的 EACCES/ENOENT 通过异步 error 事件抛出，try/catch 本身捕获不到。
    // 等 spawn 事件后才标记已启动，让 init 的调用方能正常回写失败而不崩溃。
    await new Promise((resolve, reject) => {
      const failed = error => {
        this._launched = false;
        this._launchError = new Error(`WorkBuddy 启动失败${error.code ? `（${error.code}）` : ''}：${this.executablePath}；${error.message}`);
        reject(this._launchError);
      };
      try {
        const child = childProcess.spawn(this.executablePath, [], {
          detached: true, stdio: 'ignore',
          env: { ...process.env, [this.envPort]: String(this.cdpPort) },
        });
        child.on('error', failed);
        child.once('exit', (code, signal) => {
          this._launched = false;
          this._launchError ||= new Error(`WorkBuddy 启动进程已退出（${signal || `退出码 ${code}`}），调试端口 ${this.cdpPort} 未就绪`);
          reject(this._launchError);
        });
        child.once('spawn', () => {
          child.unref(); this._launched = true;
          this._log(`   已启动 WorkBuddy：${this.executablePath}（${this.envPort}=${this.cdpPort}）`);
          resolve();
        });
      } catch (error) { failed(error); }
    });
  }
  async init() {
    // 1) 端口未就绪则（可选杀旧后）启动
    if (!(await this._portReady())) {
      // 先解析并验证启动文件，配置错误时不能先关闭正在使用的客户端。
      this._resolveExecutable();
      if (this.killExisting) { this._killExisting(); await this._sleep(1000); }
      await this._spawnClient();
      await this._waitPort(this.launchTimeout);
    } else {
      this._log('   WorkBuddy 调试端口已就绪，直接 attach');
    }
    // 2) CDP 连接
    this.browser = await connectDesktopCDP(this.cdpUrl, { product: 'WorkBuddy', logger: this.logger });
    // 与纳米同款：纠正 collocated 标记，让 setInputFiles 走本地路径（附件上传绕 50MB）。私有字段，失败忽略。
    try {
      const impl = this.browser._connection && this.browser._connection.toImpl(this.browser);
      if (impl && impl._isBrowserCollocatedWithServer === false) impl._isBrowserCollocatedWithServer = true;
    } catch (_) {}
    const contexts = this.browser.contexts();
    if (!contexts.length) throw new Error('CDP 连接成功但无 context（WorkBuddy 窗口未创建）');
    this.context = contexts[0];
    await this.context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
    // 3) 取主 page（WorkBuddy 单 page）+ 等输入框就绪
    const page = this.context.pages()[0];
    if (!page) throw new Error('WorkBuddy context 下无 page');
    await page.locator('[contenteditable="true"][role="textbox"]').first().waitFor({ state: 'visible', timeout: this.readyTimeout }).catch(() => {});
    this.mainPage = page;
    this._log(`   已连接 WorkBuddy 主窗口：${page.url().slice(0, 60)}`);
  }
  getMainPage() { return this.mainPage; }
  getContext() { return this.context; }
  async close({ keepClient = true } = {}) {
    try { if (this.browser) await this.browser.close(); } catch (_) {}  // 只断 CDP 连接，不关客户端
    if (!keepClient && this._launched) { this._killExisting(); }
  }
}
module.exports = { WorkbuddyPool };
