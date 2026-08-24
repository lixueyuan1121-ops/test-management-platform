// 桌面客户端连接池：把「纳米 Work 桌面版」（Electron 应用，内部就是 work.n.cn 前端）
// 带远程调试端口跑起来，再用 Playwright 通过 CDP 连接，交出可操作的 context + 主页面。
//
// 与 Web 版 ContextPool 的根本差异：
//  · Web 版用 chromium.launch() 起一个受控浏览器，每账号一个 context（注入录制的 storageState）。
//  · 桌面版是已安装的 Electron 应用，登录态在它自己的 userData 里；我们不注入登录态，
//    而是直接复用「客户端当前登录的那个账号」。连接靠 CDP（Electron 的远程调试端口）。
//  · 实测（探针确认）：Electron 主进程掌控窗口创建，CDP 的 Target.createTarget 不被支持，
//    故 context.newPage() / window.open 都开不出新标签页——桌面版只有「一个主窗口/一个 page」，
//    并发只能在这唯一的 page 内、靠平台自身的「新建任务 + 左侧列表切换」实现（见 DesktopRunner）。
//
// 启动策略（对应用户选择「自动带调试端口重启客户端」+「复用客户端登录态」）：
//  1. 若目标调试端口已就绪（用户已手动带端口开着）→ 直接连，不动进程；
//  2. 否则：关闭现有 namiwork 进程（单实例锁会让带端口的新实例把参数转发后退出，端口开不起来），
//     再带 --remote-debugging-port 启动（沿用默认 userData=已登录态），等端口就绪后连接。
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const http = require('http');
const { attachWsTrace } = require('./ws-trace');
const workFrame = require('./work-frame');

class DesktopPool {
  constructor(desktopConfig, platformConfig, logger) {
    const d = desktopConfig || {};
    this.executablePath = d.executablePath || '';
    this.cdpHost = d.cdpHost || '127.0.0.1';
    this.cdpPort = d.cdpPort || 9222;
    this.processName = d.processName || 'Namiwork.exe';
    // attachOnly：只连接已带端口开着的客户端，绝不自己启动/杀进程（对应 --attach）
    this.attachOnly = !!d.attachOnly;
    // killExisting：自动重启前是否强杀现有客户端进程（默认 true；端口已就绪时本就不会杀）
    this.killExisting = d.killExisting !== false;
    // 额外启动参数（如需指定独立 userData 目录等），默认空=复用客户端默认 userData（保留登录态）
    this.extraArgs = Array.isArray(d.launchArgs) ? d.launchArgs : [];
    this.launchTimeout = d.launchTimeout || 45000;   // 等调试端口就绪
    this.readyTimeout = d.readyTimeout || 60000;     // 等主页面对话界面就绪
    this.userDataDir = d.userDataDir || '';          // 留空=不指定，用客户端默认（已登录）

    this.platform = platformConfig || {};
    this.logger = logger || null;

    this.browser = null;    // connectOverCDP 返回的 Browser（仅代表连接，close 只断开不关客户端）
    this.context = null;    // Electron 默认 context
    this.mainPage = null;   // work.n.cn 主窗口 page
    this._launched = false; // 本进程是否亲自启动过客户端
    this._wsTrace = null;   // 平台模式:主 page 的会话 WS 轨迹收集器(见 _resolveMainPage / getWsTrace)
  }

  get cdpUrl() { return `http://${this.cdpHost}:${this.cdpPort}`; }

  _log(m) { if (this.logger) this.logger.info(m); }
  _warn(m) { if (this.logger) this.logger.warn(m); }

  // 轻量 HTTP GET JSON（探测调试端口 /json/version 是否就绪）
  _fetchJson(url, timeout = 2500) {
    return new Promise((resolve, reject) => {
      const req = http.get(url, { timeout }, res => {
        let data = '';
        res.on('data', c => (data += c));
        res.on('end', () => { try { resolve(JSON.parse(data)); } catch (e) { reject(e); } });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    });
  }

  async _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // 端口是否已就绪（能拿到 /json/version）
  async _portReady() {
    try { await this._fetchJson(`${this.cdpUrl}/json/version`); return true; }
    catch { return false; }
  }

  // 等端口就绪，返回版本信息；超时抛错
  async _waitPort(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    let last = null;
    while (Date.now() < deadline) {
      try { return await this._fetchJson(`${this.cdpUrl}/json/version`); }
      catch (e) { last = e; await this._sleep(600); }
    }
    throw new Error(`调试端口 ${this.cdpUrl} 未在 ${Math.round(timeoutMs / 1000)}s 内就绪：${last ? last.message : ''}`);
  }

  // 强杀现有客户端进程（跨平台）。已就绪端口时不会走到这里。
  //  · Windows：taskkill 按镜像名（processName）杀进程树；
  //  · mac/Linux：pkill -f 按可执行文件完整路径精确匹配，杀掉 Electron 主进程即可释放单实例锁
  //    （helper 子进程随主进程退出而自退；用完整路径而非进程名，避免误杀其它同名进程）。
  // 用 execFileSync（参数数组、不经 shell）而非拼字符串，规避 shell 元字符/注入。
  _killExisting() {
    const isWin = process.platform === 'win32';
    try {
      if (isWin) {
        execFileSync('taskkill', ['/IM', this.processName, '/F', '/T'], { stdio: 'ignore' });
      } else {
        if (!this.executablePath) return; // 无路径可匹配，跳过（mac/Linux 不用 processName）
        execFileSync('pkill', ['-9', '-f', this.executablePath], { stdio: 'ignore' });
      }
      this._log(`   已关闭现有客户端进程（${isWin ? this.processName : this.executablePath}）`);
    } catch (_) {
      // 没有在跑的进程时命令返回非 0（taskkill / pkill 皆如此），忽略
    }
  }

  // 带调试端口启动客户端（detached，脱离本进程生命周期，脚本退出后客户端仍在）
  _spawnClient() {
    if (!this.executablePath) {
      throw new Error('未配置 desktop.executablePath（纳米 Work 桌面版 exe 路径），无法自动启动客户端');
    }
    const args = [`--remote-debugging-port=${this.cdpPort}`];
    if (this.userDataDir) args.push(`--user-data-dir=${this.userDataDir}`);
    args.push(...this.extraArgs);
    const child = spawn(this.executablePath, args, { detached: true, stdio: 'ignore' });
    child.unref();
    this._launched = true;
    this._log(`   已启动客户端：${this.executablePath} ${args.join(' ')}`);
  }

  async init() {
    // 1) 端口已就绪 → 直接连（支持「先手动带端口开着」/「上次已启动仍在跑」）
    let ready = await this._portReady();
    if (ready) {
      this._log(`🔌 检测到调试端口 ${this.cdpUrl} 已就绪，直接连接（不重启客户端）`);
    } else if (this.attachOnly) {
      throw new Error(
        `--attach 模式：调试端口 ${this.cdpUrl} 未就绪。请先带端口启动客户端：\n` +
        `   "${this.executablePath || '<namiwork.exe>'}" --remote-debugging-port=${this.cdpPort}\n` +
        `   （或去掉 --attach，让程序自动关闭并带端口重启客户端）`
      );
    } else {
      // 2) 自动重启：关现有进程 → 带端口启动 → 等端口
      this._log('🔌 调试端口未就绪，自动带调试端口重启客户端...');
      if (this.killExisting) this._killExisting();
      await this._sleep(1500);
      this._spawnClient();
      const ver = await this._waitPort(this.launchTimeout);
      this._log(`   调试端口就绪：${ver.Browser || ''}`);
    }

    // 3) CDP 连接
    this.browser = await chromium.connectOverCDP(this.cdpUrl);
    // connectOverCDP 会把浏览器标记为「与 server 不同机」，导致 setInputFiles 走缓冲区传输并限 50MB
    // （大附件如真实代码 zip 直接报错）。但客户端其实就在本机、文件也在本机路径可直接读，故纠正该标记，
    // 让 setInputFiles 走本地路径上传、绕过 50MB 限制。私有字段，失败则忽略（退回受限行为，由上层 fail-closed 兜底）。
    try {
      const impl = this.browser._connection && this.browser._connection.toImpl(this.browser);
      if (impl && impl._isBrowserCollocatedWithServer === false) impl._isBrowserCollocatedWithServer = true;
    } catch (_) { /* Playwright 内部实现变动：忽略，附件上传失败会被 fail-closed 判失败 */ }
    const contexts = this.browser.contexts();
    if (contexts.length === 0) throw new Error('CDP 连接成功但无可用 context（客户端可能尚未创建窗口）');
    this.context = contexts[0];
    // 分享链接需读系统剪贴板：授予剪贴板权限（Electron 下多为已放开，失败也不影响）
    await this.context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});

    // 4) 拿到 work.n.cn 主窗口并等对话界面就绪
    this.mainPage = await this._resolveMainPage(this.readyTimeout);
    this._log(`   已连接主窗口：${this.mainPage.url()}`);
    return this;
  }

  // 找到 work.n.cn 主 page 并等对话输入框可见（对话 iframe 已挂载）。
  // 客户端刚启动时主窗口可能还在加载，故轮询等待 page 出现 + 输入框就绪。
  // 登录态前置预检：轮询中若持续只见登录二维码页(qrcode/im.live.360.cn/passport)而不见 work.n.cn，
  // 给 8s 缓冲（已登录客户端启动可能短暂闪 360 SSO 域名再重定向到 work.n.cn）后即提前判定「未登录」并抛错，
  // 不等 readyTimeout 跑满——未登录时多等无益，对方扫码后重跑即可。缓冲后仍只见登录页=会话失效，非慢加载。
  async _resolveMainPage(timeoutMs) {
    const iframeSel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const inputSel = this.platform.inputSelector || '.chat-compose-rich__content';
    const loginRe = this.platform.loginUrlPattern
      ? new RegExp(this.platform.loginUrlPattern)
      : /qrcode|im\.live\.360\.cn|passport|\/login\b/i;
    const loginError = (urls) => new Error(
      `客户端未登录或会话已过期（停在登录页）。请先手动打开纳米 Work 桌面客户端、扫码完成登录，` +
      `确认进入 work.n.cn 主对话界面后重跑。已连接的页面：${urls.join(', ')}`
    );
    const deadline = Date.now() + timeoutMs;
    let firstLoginAt = 0;
    let page = null;
    while (Date.now() < deadline) {
      const pages = this.context.pages();
      const urls = pages.map(p => p.url());
      page = pages.find(p => workFrame.isWorkMainPage(p, p.url())) || null;
      if (!page && urls.some(u => loginRe.test(u))) {
        // 持续只见登录页：缓冲 8s 仍无 work.n.cn → 提前判定未登录，省掉跑满 readyTimeout 的空等
        if (!firstLoginAt) firstLoginAt = Date.now();
        else if (Date.now() - firstLoginAt > 8000) throw loginError(urls);
      } else {
        firstLoginAt = 0; // 见到 work.n.cn 或非登录页：重置（已登录客户端正常加载路径）
      }
      if (page) {
        // 遇到原生弹窗直接放行，避免卡住主窗口
        page.on('dialog', async (dg) => { try { await dg.accept(); } catch { try { await dg.dismiss(); } catch {} } });
        // 平台模式:抓会话 WebSocket 轨迹(思考/工具)。挂在主 page,收集器存到 pool 供编排取。
        // 本 while 循环拿到同一 page 后可能多次经过此处(输入框未就绪就重试),用标志确保只挂一次,避免重复注册。
        // ⚠️ page.on('websocket') 只捕获【挂载后新建】的 WS。连"已运行客户端"时对话长连早于挂载建立 →
        //   抓不到帧 → ws_captured=false(真机实测确认)。故挂载后【主动 reload 一次】让对话 WS 重连被捕获
        //   (实测:静观0帧、reload后30帧)。reload 只做一次(_wsReloaded 标志),避免 while 重试里反复重载。
        if (!this._wsTrace) {
          this._wsTrace = attachWsTrace(page);
          this._log('   已挂载 WS 轨迹抓取');
        }
        if (!this._wsReloaded) {
          this._wsReloaded = true;
          try {
            await page.reload({ waitUntil: 'domcontentloaded', timeout: this.readyTimeout });
            this._log('   已 reload 对话页触发 WS 重连(确保 ws 轨迹可捕获)');
            await this._sleep(1500);   // 给 WS 重连 + SPA 重挂一点时间
          } catch (e) {
            this._warn(`   reload 触发 WS 重连失败(不阻断,ws 可能抓不到): ${(e.message || '').split('\n')[0]}`);
          }
        }
        try {
          // 自适应:有 work.n.cn iframe 走 frameLocator,否则主文档 page(不同设备对话 UI 挂载位置不同)。
          const hasIframe = await workFrame.hasWorkIframe(page, iframeSel);
          const ctx = workFrame.pickCtx(page, iframeSel, hasIframe);
          await ctx.locator(inputSel).first().waitFor({ state: 'visible', timeout: 3000 });
          return page; // 对话界面就绪(iframe 或主文档)
        } catch { /* 还没就绪，继续轮询 */ }
      }
      await this._sleep(1000);
    }
    if (page) return page; // 拿到了 page 但输入框迟迟没就绪：交给上层，DesktopRunner 会再等/新建对话
    const finalUrls = this.context.pages().map(p => p.url());
    if (finalUrls.some(u => loginRe.test(u))) throw loginError(finalUrls);
    throw new Error(
      `未找到 work.n.cn 主窗口（客户端可能未加载或停在非主页）。请确认桌面客户端已打开并切到主对话窗口后重跑。` +
      `已连接的页面：${finalUrls.join(', ')}`
    );
  }

  getContext() { return this.context; }
  getMainPage() { return this.mainPage; }
  // 平台模式:取主 page 的 WS 轨迹收集器(reset/buildTrace)。未连接/未挂上时返回 null。
  getWsTrace() { return this._wsTrace || null; }

  // 找到挂着 window.clawDeviceService 的 frame(主文档或 work.n.cn iframe)
  async _deviceFrame() {
    if (!this.mainPage) return null;
    for (const f of this.mainPage.frames()) {
      const has = await f.evaluate(() => typeof window.clawDeviceService !== 'undefined').catch(() => false);
      if (has) return f;
    }
    return null;
  }

  // 读客户端设备(vm)列表。返回规整后的数组;读不到返回 []。
  async listDevices() {
    const frame = await this._deviceFrame();
    if (!frame) { this._warn('   未找到 clawDeviceService(读不到设备列表)'); return []; }
    const list = await frame.evaluate(async () => {
      const svc = window.clawDeviceService;
      let arr = [];
      try { arr = await svc.getDeviceList(true); } catch (e) { try { arr = svc._deviceList || []; } catch (_) {} }
      return (arr || []).map(d => ({
        vm_id: d.id, label: String(d.url || '').split('.')[0],
        name: d.name, status: d.status, device_type: d.type,
      }));
    }).catch(() => []);
    return list;
  }

  // 当前显示的设备 vm_id(优先 clawDeviceService.currentDevice,兜底 hostname 首段)
  async currentVmId() {
    const frame = await this._deviceFrame();
    if (frame) {
      const id = await frame.evaluate(() => {
        try { return window.clawDeviceService.currentDevice?.id || null; } catch (_) { return null; }
      }).catch(() => null);
      if (id) return id;
    }
    // 兜底:hostname 首段(33 位去首字符=32位核)
    try {
      const host = new URL(this.mainPage.url()).hostname;
      const seg = host.split('.')[0];
      return seg.length === 33 ? seg.slice(1) : seg;
    } catch (_) { return null; }
  }

  // 切到指定 vm_id 的设备:已是当前则跳过;离线云设备尝试唤醒;切换后等就绪并重挂 wsTrace。
  async switchTo(vmId) {
    if (!vmId) return { ok: true, url: this.mainPage.url() };
    const cur = await this.currentVmId();
    if (cur === vmId) return { ok: true, url: this.mainPage.url() };

    let devices = await this.listDevices();
    let dev = devices.find(d => d.vm_id === vmId);
    if (!dev) throw new Error(`目标设备 ${vmId} 不在客户端列表`);

    const online = (s) => s === 'online' || s === 'active';
    // 离线处理:云设备(type=0)尝试 API 唤醒并轮询上线;其它类型无法远程唤醒 → 抛错(上层 fail-closed)
    if (!online(dev.status)) {
      if (dev.device_type === 0) {
        this._log(`   目标云设备离线,尝试唤醒 ${vmId} ...`);
        const frame = await this._deviceFrame();
        await frame.evaluate((id) => { try { window.clawDeviceService.startCloudDevice(id); } catch (_) {} }, vmId).catch(() => {});
        const deadline = Date.now() + 60000;
        while (Date.now() < deadline) {
          await this._sleep(3000);
          devices = await this.listDevices();
          dev = devices.find(d => d.vm_id === vmId) || dev;
          if (online(dev.status)) break;
        }
        if (!online(dev.status)) throw new Error(`云设备 ${vmId} 唤醒后仍未上线`);
      } else {
        throw new Error(`目标设备 ${vmId} 离线(type=${dev.device_type},无法远程唤醒)`);
      }
    }

    // 注入切换:location.assign 到 <label>.work.n.cn/claw?vm_id=<label>(实测 isInIframe=false 路径)
    const frame = await this._deviceFrame();
    const label = dev.label;
    await frame.evaluate((lb) => {
      const origin = location.origin.replace(/\/\/[^.]+\./, '//' + lb + '.');
      window.location.assign(`${origin}/claw?vm_id=${encodeURIComponent(lb)}`);
    }, label).catch(() => {});

    // 等 URL 切到目标 vm(host 首段含 label)
    const deadline = Date.now() + 30000;
    let ok = false;
    while (Date.now() < deadline) {
      await this._sleep(1000);
      try {
        const host = new URL(this.mainPage.url()).hostname;
        if (host.split('.')[0].includes(label)) { ok = true; break; }
      } catch (_) {}
    }
    if (!ok) throw new Error(`切换到 ${vmId} 后 URL 未到位(可能环境不同/走了父壳),当前 ${this.mainPage.url()}`);

    // 导航后重新 resolve 主 page + 等对话输入框就绪 + 重挂 wsTrace
    await this._sleep(2000);
    this._wsTrace = null;      // 允许重挂(挂在新导航的 page 上)
    this._wsReloaded = false;  // 允许 _resolveMainPage 对新页面再 reload 一次触发 WS 重连(切设备后同样需要)
    this.mainPage = await this._resolveMainPage(this.readyTimeout);
    // 确认当前 vm 就是目标 + 输入框可用
    const nowVm = await this.currentVmId();
    if (nowVm !== vmId) throw new Error(`切换后当前设备 ${nowVm} != 目标 ${vmId}`);
    this._log(`   ✅ 已切到设备 ${dev.name || vmId} (${this.mainPage.url()})`);
    return { ok: true, url: this.mainPage.url() };
  }

  // 断开 CDP 连接。默认不关闭客户端（keepClient=true）：让用户重启后的客户端继续留用，
  // 下次可直接连（端口仍开着）。仅断开 Playwright 侧连接。
  async close({ keepClient = true } = {}) {
    try { if (this.browser) await this.browser.close(); } catch (_) {}
    this.browser = null;
    if (!keepClient && this._launched) {
      this._killExisting();
    }
  }
}

module.exports = DesktopPool;
