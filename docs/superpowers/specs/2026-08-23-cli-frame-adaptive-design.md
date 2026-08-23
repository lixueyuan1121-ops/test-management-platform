# 设计:CLI 对话 UI 定位自适应(iframe / 主文档)

- 日期:2026-08-23
- 状态:已评审(用户 /goal 认可核心机制"完整自适应";本 spec 定稿供 review)
- 所属大工程:对话测评链路 · CLI 执行层修复(子项A)
- 范围:纳米 Work 桌面客户端不同设备的对话 UI 挂载位置不同(iframe 内 vs 主文档直挂),CLI 写死 frameLocator → 主文档形态设备全线定位失败。改为**自适应**:有匹配 iframe 走 frameLocator,无则走主文档 page。
- 关联代码(均 `D:\code\ai-eval-cli-yt`,非 git,文件交付):`src/dialog-runner.js`、`src/desktop-runner.js`、`src/desktop-pool.js`、`src/task-watcher.js`;新增 `src/work-frame.js`(共享 helper)。

## 1. 背景与根因(已实测坐实)

真机联调发现:平台模式跑对话反复"新建对话未干净 / `locator.waitFor: Timeout 10000ms`",全部 run 回写 failed、`ws_captured=false`。

系统化排查(非猜测)定论:
- 客户端当前设备"云电脑(内部)"(`pc72…work.n.cn`)的对话 UI **直接在主文档,页面里没有任何 iframe**(实测 `iframe count=0`;`.chat-compose-rich__content`/`.aside-panel__chat-button`/`button.send-btn` 均在主文档 count=1)。
- CLI 所有元素定位走 `_fl() = page.frameLocator('iframe[src*=".work.n.cn"]')`(为"work.n.cn 业务在跨域 iframe 内"设计,子项2 的假设)。**在无 iframe 的页面里 frameLocator 永远匹配空 → 所有 locator 超时**。
- 与设备在线/离线无关。用户确认:客户端设备是**混合形态**——有的对话 UI 在 iframe 里(以前 CLI 跑通的形态)、有的直接在主文档。

**最小可行性验证(已实测)**:在当前主文档设备上,用自适应判据 `iframe count>0 ? frameLocator : page` + 同一套选择器,一次性定位到新建任务/输入框/发送按钮(各 count=1)、输入框 `visible` ✅、历史气泡 count=0。修法方向坐实。

## 2. 目标与非目标

**目标**
- CLI 的"操作上下文(ctx)"从写死 frameLocator 改为**探测后决定**:页面存在匹配 `iframeSelector` 的 iframe → frameLocator(老形态,零行为变化);否则 → 主文档 page(新形态)。
- 覆盖 desktop 平台模式全链(连接/新建对话/输入/发送/抓取/切设备/串台诊断)与 Web 模式(run 命令)。
- 同一套选择器、同一套逻辑,自动适配两种 DOM 形态。**不破坏"以前跑通的 iframe 形态"**。
- 顺带修一个相关隐患:`desktop-pool._resolveMainPage` 用整串 URL 子串匹配 `work.n.cn`,会把 `recovery.html?url=…work.n.cn…` 误判为主 page(Task5 review 记录的现存隐患,与本次 page/ctx 判定同源)。

**非目标(YAGNI)**
- 不改选择器本身(选择器在两种形态下一致,只是挂载容器不同)。
- 不改设备切换逻辑(switchTo 已就绪)、不改判定/回填/平台 API。
- 不做 Shadow DOM 穿透(Playwright locator 已自动穿透 open shadow root,现状足够)。

## 3. 关键决策(AI 自主拍板,用户已认可"完整自适应")

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 自适应判据 | `await page.locator(iframeSel).count() > 0` → 有 iframe;否则主文档。实测可靠。 |
| 2 | 判定时机 | 一个设备的形态在会话期不变,故**在已有 frame 解析入口异步判定一次、缓存 ctx**,下游同步用(不改 43 处调用方)。切设备后(会话页可能换形态)重新判定。 |
| 3 | 复用方式 | 新增共享 `src/work-frame.js`(纯函数),四个文件复用,避免各自实现漂移(DRY)。 |
| 4 | ctx 落地 | 各文件已有 `this.frame`(dialog-runner/task-watcher)或 `_fl()`(desktop-runner)的抽象;把它们的值从"写死 frameLocator"改为"pickCtx 结果(frameLocator 或 page)"。下游 `.locator(...)` 用法不变。 |
| 5 | evaluate 用的实 Frame | `_liveFrame`:找 `*.work.n.cn` 的 Frame,主文档时兜底 `page.mainFrame()`(实测主文档下 mainFrame.url 就是 `<vm>.work.n.cn`,现有正则能匹配)。 |
| 6 | 顺带修 recovery 隐患 | `_resolveMainPage` 的主 page 判据从"url 含 work.n.cn"改为 **hostname 匹配 `\.work\.n\.cn$` 且路径非 recovery.html**,防误连恢复页。 |

## 4. 共享 helper(`src/work-frame.js`,新建)

```
hasWorkIframe(page, iframeSel) -> Promise<bool>
  return (await page.locator(iframeSel).count()) > 0

pickCtx(page, iframeSel, hasIframe) -> FrameLocator | Page
  return hasIframe ? page.frameLocator(iframeSel).first() : page

liveFrame(page) -> Frame
  找 page.frames() 中 url 匹配 /^https?:\/\/[a-z0-9]+\.work\.n\.cn/ 的 Frame;无则 page.mainFrame()

isWorkMainPage(page, url) -> bool   // 供 _resolveMainPage 用,替代整串子串匹配
  解析 url 的 hostname 以 .work.n.cn 结尾,且 pathname 不是 recovery.html
```

`iframeSel` 缺省 `'iframe[src*=".work.n.cn"]'`(与现有一致)。

## 5. 各文件改造

### 5.1 `src/dialog-runner.js`
- `_ctx()` 保持(返回 `this.frame || this.page`)——`this.frame` 现在可能是 page 或 frameLocator,`_ctx()` 语义仍对。
- `_waitForFrame(timeout)`(Web 模式 init 路径):改为先 `hasWorkIframe` 判定 → `pickCtx` 得 ctx → 在 ctx 上等 `inputSelector` 可见 → 返回该 ctx(存 this.frame)。等不到时错误信息带上"形态=iframe/主文档"。
- `attachToPage(page)`(desktop 模式):保持同步设 `this.page`;`this.frame` 初值设 `pickCtx(page, sel, false)`=page 也可,但因 desktop 模式下 `desktop-runner._sendOne/_extractCurrent` 每次会 `dr.frame = this._fl()`(见 5.2,已自适应)覆盖,故此处不影响。为稳妥仍可留 frameLocator——**决策:改为 `this.frame = page`(主文档安全默认),真正 ctx 由 desktop-runner._fl() 每次注入**(desktop 模式)/`_waitForFrame` 设(Web 模式)。
- `_liveFrame()`:改用 `workFrame.liveFrame(this.page)`(已兼容主文档)。

### 5.2 `src/desktop-runner.js`(desktop 平台模式核心)
- 加 `this._ctxKind = null`(缓存 'iframe'|'main')。
- 加 `async _ensureCtx()`:`this._ctxKind = (await hasWorkIframe(this.page, sel)) ? 'iframe' : 'main'`。在 `_openCleanConversation` 开头调用一次(每条 run 开始;切设备后 bin 重建 runner → 新 runner 首次即重判)。
- `_fl()` 改为同步据缓存返回:`pickCtx(this.page, sel, this._ctxKind === 'iframe')`;若 `_ctxKind` 尚未判定则回退 `hasWorkIframe` 之前的安全值(判定前不调用 _fl 的路径已由 _ensureCtx 保证)。**实现细节**:_openCleanConversation/_sendOne 前置 `await this._ensureCtx()`,确保 _fl 有缓存。

### 5.3 `src/desktop-pool.js`
- `_resolveMainPage(timeoutMs)`:
  - 主 page 判据用 `workFrame.isWorkMainPage(page, url)`(替代 `/work\.n\.cn/.test(url)`),排除 recovery.html。
  - 找到 page 后:`hasWorkIframe` 判形态 → `pickCtx` 得 ctx → 在 ctx 上等 `inputSelector` 可见(替代写死 frameLocator)。
  - 登录页判定(loginRe)保持。
- `listDevices/currentVmId/switchTo`(设备能力):`_deviceFrame` 找 clawDeviceService(遍历 frames,已兼容主文档 mainFrame);切换后就绪判定复用 _resolveMainPage 的自适应等待。无需额外改。

### 5.4 `src/task-watcher.js`(串台诊断/观察)
- `attach(page)` / `_navigateAndWait`:`this.frame = pickCtx(page, sel, await hasWorkIframe(page, sel))`。attach 现同步——改为 `attach` 设 `this.page`,`this.frame` 延迟到首次 `_ensureFrame()`(异步判定缓存)或在 `start`/`_navigateAndWait` 判定。**决策**:加 `async _ensureFrame()`,在 `start()` 首次 tick 前与 `_navigateAndWait` 里调用,设 this.frame;下游 `this.frame.locator` 不变。
- 平台模式下 TaskWatcher 由 DesktopRunner 持有(desktop-runner.js:31),仅并发观察(watchSwitch)时才 start;单条平台执行通常不触发切换观察,但诊断读取(_readFirstQuery 等)仍走 this.frame,故必须自适应。

## 6. 迁移与 schema
- 无平台 schema/DB 变更。纯 CLI 逻辑改动 + 新增一个 helper 文件。
- spec/plan 文档提交平台仓库(分支 spec/cli-frame-adaptive);CLI 代码文件交付(非 git)。

## 7. 影响面与风险
- **隔离**:只改 frame/ctx 解析;选择器、发送/抓取/判定/切设备/平台 API 均不动。
- **不破坏 iframe 老形态**:有 iframe 时 `pickCtx` 返回 frameLocator,行为与现在逐字一致。
- **风险1(判定时机)**:切设备后会话页可能换形态。缓解:bin 平台模式切设备后**重建 runner**(已有),新 runner 首次 `_ensureCtx` 重判;desktop-pool 切换后 `_resolveMainPage` 重判。
- **风险2(异步判定引入的时序)**:_ensureCtx 前误调 _fl。缓解:在 _openCleanConversation/_sendOne 入口前置 await _ensureCtx;_fl 回退安全值。
- **风险3(真验证)**:主文档形态已最小验证定位成功;完整对话链路(新建→发送→抓取)与 iframe 形态不回归须真机联调(本机 lili-win 可切两种形态设备各跑一次)。

## 8. 验证方式(本仓库无测试框架)
1. 主文档形态:客户端停在主文档设备(如"云电脑内部"),`node bin/ai-eval.js platform --once` 跑一条 → `新建对话干净`→发送→`✅ 回写 done`。
2. iframe 形态:切到 iframe 形态设备(以前跑通的),同样跑一条,确认不回归。
3. helper 单元:脱机验 `isWorkMainPage`(work.n.cn/其它域/recovery.html)、`liveFrame` 选择逻辑(可 mock frames)。
4. recovery 隐患:构造 recovery.html url,`isWorkMainPage` 返回 false。

## 9. 交付清单
- [ ] src/work-frame.js:hasWorkIframe/pickCtx/liveFrame/isWorkMainPage
- [ ] dialog-runner.js:_waitForFrame 自适应 + attachToPage 默认 + _liveFrame 用 helper
- [ ] desktop-runner.js:_ensureCtx + _fl 据缓存 + _openCleanConversation/_sendOne 前置判定
- [ ] desktop-pool.js:_resolveMainPage 自适应 + isWorkMainPage 修 recovery 隐患
- [ ] task-watcher.js:_ensureFrame 自适应
- [ ] 真机验证(主文档 + iframe 双形态)+ helper 脱机验证

## 10. 后续(不在本子项)
- 子项B:eval_query 历史页 + 再次触发验证(平台侧)。
- 子项C:ai-eval-cli-yt 合并进 qalab-runner(统一执行器、一处配置)。
