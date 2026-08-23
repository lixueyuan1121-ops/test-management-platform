# CLI 对话 UI 定位自适应 实现计划

> **For agentic workers:** 本计划由控制者内联执行(superpowers:executing-plans),CLI 为非 git 仓库、改动为文件交付 + 本机真机验证。步骤用 checkbox 跟踪。

**Goal:** CLI 元素定位自适应——有匹配 iframe 走 frameLocator、无则走主文档 page,让 iframe 形态与主文档形态的纳米 Work 设备都能跑通对话。

**Architecture:** 新增共享 `src/work-frame.js`(纯函数判定/选择 ctx),四个文件(dialog-runner/desktop-runner/desktop-pool/task-watcher)把写死的 `page.frameLocator(...)` 换成"探测后决定的 ctx",一次判定缓存、下游同步用,43 处调用方不改。

**Tech Stack:** Node + Playwright(CDP)。目标目录 `D:\code\ai-eval-cli-yt`(非 git)。

## Global Constraints
- 有匹配 iframe 时行为与现在**逐字一致**(走 frameLocator),绝不破坏"以前跑通的 iframe 形态"。
- 判据 `await page.locator(iframeSel).count() > 0`;`iframeSel` 缺省 `'iframe[src*=".work.n.cn"]'`。
- 选择器本身不动;只改"元素挂在哪个 ctx"。
- CLI 非 git,**不 commit**(文件交付);无测试框架,验证走 helper 脱机(node)+ 本机真机(CDP 127.0.0.1:9222)+ `node -c` 语法自检。
- 本机即 lili-win,客户端 CDP 在 9222;可切"云电脑内部"(主文档形态)/"云龙虾A"或"win测试本"(iframe 形态,若是)验证两路径。
- shell 跑脚本:`cd /d/code/ai-eval-cli-yt && node <file>`。

---

## Task 1: 共享 helper `src/work-frame.js`

**Files:** Create `D:\code\ai-eval-cli-yt\src\work-frame.js`;临时验证 `tmp_wf_test.js`。

**Interfaces (Produces):**
- `hasWorkIframe(page, iframeSel?) -> Promise<boolean>`
- `pickCtx(page, iframeSel, hasIframe) -> FrameLocator|Page`
- `liveFrame(page) -> Frame`
- `isWorkMainPage(page, url?) -> boolean`
- `DEFAULT_IFRAME_SEL = 'iframe[src*=".work.n.cn"]'`

- [ ] **Step 1: 写 helper**

```javascript
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
    if (!/\.work\.n\.cn$/i.test(parsed.hostname)) return false;
    if (/recovery\.html$/i.test(parsed.pathname)) return false;
    return true;
  } catch (_) { return false; }
}

module.exports = { DEFAULT_IFRAME_SEL, hasWorkIframe, pickCtx, liveFrame, isWorkMainPage };
```

- [ ] **Step 2: 脱机验证 isWorkMainPage(纯逻辑,不连客户端)**

创建 `tmp_wf_test.js`:
```javascript
const { isWorkMainPage } = require('./src/work-frame');
const cases = [
  ['https://n41372f99adf94fcca60da56b0c76f784.work.n.cn/?vm_target=elec', true],
  ['https://pc72140eed2ac4df3aeb9a3b14c45bce8.work.n.cn/?status=online', true],
  ['https://work.n.cn/launcher', true],
  ['file:///C:/x/recovery.html?url=https://n413.work.n.cn/', false],
  ['https://evil.com/?x=work.n.cn', false],
  ['https://a.work.n.cn.evil.com/', false],
];
let ok = true;
for (const [url, exp] of cases) {
  const got = isWorkMainPage(null, url);
  const pass = got === exp;
  if (!pass) ok = false;
  console.log(`${pass ? 'OK ' : 'FAIL'} ${got} (exp ${exp})  ${url}`);
}
console.log(ok ? 'ALL PASS' : 'HAS FAILURE');
process.exit(ok ? 0 : 1);
```

- [ ] **Step 3: 跑脱机验证**

Run: `cd /d/code/ai-eval-cli-yt && node tmp_wf_test.js`
Expected: `ALL PASS`(work.n.cn 主页/vm 子域/launcher=true;recovery.html/伪装域=false)。

- [ ] **Step 4: 删验证脚本 + 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && rm -f tmp_wf_test.js && node -c src/work-frame.js && echo OK`
Expected: `OK`。

---

## Task 2: dialog-runner.js 自适应

**Files:** Modify `D:\code\ai-eval-cli-yt\src\dialog-runner.js`。

**Interfaces:** Consumes Task1 helper。改 `_waitForFrame`/`attachToPage`/`_liveFrame`。

- [ ] **Step 1: 顶部 require helper**

在文件顶部(`const path = require('path');` 后)加:
```javascript
const workFrame = require('./work-frame');
```

- [ ] **Step 2: attachToPage 改主文档安全默认**

把(dialog-runner.js:36-41):
```javascript
  attachToPage(page) {
    this.page = page;
    this.ownsPage = false;
    this.frame = this.page.frameLocator(this.platform.iframeSelector || 'iframe[src*=".work.n.cn"]').first();
    return this;
  }
```
改为:
```javascript
  attachToPage(page) {
    this.page = page;
    this.ownsPage = false;
    // ctx 由 desktop-runner._fl() 每次注入(desktop 模式)或 _waitForFrame 设(Web 模式);
    // 这里给主文档安全默认,避免主文档形态下写死 frameLocator 失效。
    this.frame = page;
    return this;
  }
```

- [ ] **Step 3: _waitForFrame 自适应**

把(dialog-runner.js:76-98)整个 `_waitForFrame` 改为:
```javascript
  async _waitForFrame(timeout) {
    const iframeSel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const inputSel = this.platform.inputSelector;
    const deadline = Date.now() + timeout;
    let lastErr = null;
    while (Date.now() < deadline) {
      try {
        // 自适应:有 work.n.cn iframe 走 frameLocator,否则主文档 page。
        const hasIframe = await workFrame.hasWorkIframe(this.page, iframeSel);
        const ctx = workFrame.pickCtx(this.page, iframeSel, hasIframe);
        const input = ctx.locator(inputSel).first();
        await input.waitFor({ state: 'visible', timeout: 3000 });
        await input.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
        return ctx;
      } catch (e) {
        lastErr = e;
        await this.page.waitForTimeout(1000);
      }
    }
    throw new Error(
      `页面未加载完成，找不到对话输入框（input: ${inputSel}，iframe/主文档均已尝试）：` +
      `${lastErr ? lastErr.message.split('\n')[0] : 'timeout'}。` +
      `若页面显示“刷新重试”，多为后端 VM 未就绪，可增大 execution.timeout 后重试。`
    );
  }
```

- [ ] **Step 4: _liveFrame 用 helper**

把(dialog-runner.js:122-124 附近)`_liveFrame` 改为:
```javascript
  _liveFrame() {
    return workFrame.liveFrame(this.page);
  }
```

- [ ] **Step 5: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -c src/dialog-runner.js && echo OK`
Expected: `OK`。

---

## Task 3: desktop-runner.js 自适应(对话执行核心)

**Files:** Modify `D:\code\ai-eval-cli-yt\src\desktop-runner.js`。

- [ ] **Step 1: 顶部 require + 构造加 _ctxKind**

顶部(`class DesktopRunner {` 之前)加:
```javascript
const workFrame = require('./work-frame');
```
在 constructor 末尾(this.watcher.attach(page) 之后)加:
```javascript
    this._ctxKind = null;   // 'iframe' | 'main';首次 _ensureCtx 判定后缓存
```

- [ ] **Step 2: _fl 改据缓存 + 加 _ensureCtx**

把(desktop-runner.js:38)`_fl()`:
```javascript
  _fl() { return this.page.frameLocator(this.platform.iframeSelector || 'iframe[src*=".work.n.cn"]').first(); }
```
改为:
```javascript
  // 据已判定的形态返回操作 ctx(frameLocator 或主文档 page)。判定前(理论不发生,入口已前置 _ensureCtx)按无 iframe 回退。
  _fl() {
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    return workFrame.pickCtx(this.page, sel, this._ctxKind === 'iframe');
  }
  // 判定当前 page 的对话 UI 形态并缓存。每条 run 开始/切设备重建 runner 后首次调用。
  async _ensureCtx() {
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    this._ctxKind = (await workFrame.hasWorkIframe(this.page, sel)) ? 'iframe' : 'main';
    return this._ctxKind;
  }
```

- [ ] **Step 3: _openCleanConversation 前置判定**

在 `_openCleanConversation()` 方法体开头(`const P = this.platform;` 之前或之后)加一行:
```javascript
    await this._ensureCtx();   // 每条 run 开始先判定当前设备对话 UI 形态(iframe/主文档)
```

- [ ] **Step 4: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -c src/desktop-runner.js && echo OK`
Expected: `OK`。

---

## Task 4: desktop-pool.js 自适应 + 修 recovery 隐患

**Files:** Modify `D:\code\ai-eval-cli-yt\src\desktop-pool.js`。

- [ ] **Step 1: 顶部 require helper**

在 `const { attachWsTrace } = require('./ws-trace');` 后加:
```javascript
const workFrame = require('./work-frame');
```

- [ ] **Step 2: _resolveMainPage 主 page 判据用 isWorkMainPage**

`_resolveMainPage` 里两处用 `/work\.n\.cn/.test(...)` 选 page 的逻辑,改用 `workFrame.isWorkMainPage`:
- `page = pages.find(p => /work\.n\.cn/.test(p.url()))` → `page = pages.find(p => workFrame.isWorkMainPage(p, p.url()))`
- 末尾 `if (finalUrls.some(u => loginRe.test(u)))` 保持;其它 `/work\.n\.cn/` 主页判定同样替换。

- [ ] **Step 3: _resolveMainPage 输入框就绪自适应**

把其中"等 iframe 里输入框可见"的块(desktop-pool.js:193-197 附近):
```javascript
        try {
          const fl = page.frameLocator(iframeSel).first();
          await fl.locator(inputSel).first().waitFor({ state: 'visible', timeout: 3000 });
          return page; // 对话界面就绪
        } catch { /* 还没就绪，继续轮询 */ }
```
改为:
```javascript
        try {
          const hasIframe = await workFrame.hasWorkIframe(page, iframeSel);
          const ctx = workFrame.pickCtx(page, iframeSel, hasIframe);
          await ctx.locator(inputSel).first().waitFor({ state: 'visible', timeout: 3000 });
          return page; // 对话界面就绪(iframe 或主文档)
        } catch { /* 还没就绪，继续轮询 */ }
```

- [ ] **Step 4: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -c src/desktop-pool.js && echo OK`
Expected: `OK`。

---

## Task 5: task-watcher.js 自适应

**Files:** Modify `D:\code\ai-eval-cli-yt\src\task-watcher.js`。

- [ ] **Step 1: 顶部 require + 加 _ensureFrame**

顶部加 `const workFrame = require('./work-frame');`。
在 `attach(page)` 里,把 `this.frame = page.frameLocator(...).first();` 改为 `this.frame = page;`(安全默认),并加方法:
```javascript
  // 判定当前 page 对话 UI 形态,设 this.frame(frameLocator 或主文档 page)。start/_navigateAndWait 前调用。
  async _ensureFrame() {
    if (!this.page) return;
    const sel = this.platform.iframeSelector || workFrame.DEFAULT_IFRAME_SEL;
    const hasIframe = await workFrame.hasWorkIframe(this.page, sel);
    this.frame = workFrame.pickCtx(this.page, sel, hasIframe);
  }
```

- [ ] **Step 2: _navigateAndWait 用自适应**

把 `_navigateAndWait` 里 `this.frame = this.page.frameLocator(iframeSel).first();` 改为 `await this._ensureFrame();`。

- [ ] **Step 3: start 首次前置判定**

`start(n, intervalMs, getActiveCount)` 是同步的且首行 `if (this.stopped || !this.frame) return;`。因 attach 已把 this.frame 设为 page(非 null),该守卫仍通过。为确保形态正确,在 `_tick`(定时器回调,已 async)首次执行时 `await this._ensureFrame()`——或更简单:在 attach 之后由调用方保证 _navigateAndWait/_ensureFrame 已跑。**实现:** 在 `_switchTick`/首个 async tick 开头加 `if (!this._frameReady) { await this._ensureFrame(); this._frameReady = true; }`(找到 task-watcher 里定时器实际调用的 async 方法名,在其开头加此保护)。

- [ ] **Step 4: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -c src/task-watcher.js && echo OK`
Expected: `OK`。

---

## Task 6: 真机端到端验证(双形态)

**Files:** 无(验证)。前置:平台后端在跑(11.120.81.7:4173)、CLI .env 已配、平台有 pending 任务(或临时下发一条)。

- [ ] **Step 1: 主文档形态跑通**

确保客户端当前停在主文档形态设备(如"云电脑内部")。平台有一条 pending(target_device 空或指向当前设备)。
Run: `cd /d/code/ai-eval-cli-yt && node bin/ai-eval.js platform --once`
Expected: 不再"新建对话未干净";出现 `新建对话`成功 → 发送 → `✅ 回写 run X (done, ...)`。

- [ ] **Step 2: iframe 形态不回归**

若有 iframe 形态设备(下发一条 target_device 指向它,或手动切过去),再跑一轮 `platform --once`,确认同样 `✅ 回写 done`,证明 iframe 老形态未回归。

- [ ] **Step 3: 记录 ws_captured 与形态**

观察每条 report 的 `ws=<true|false>` 与是否走对 ctx。记录结果(真机联调结论)。

---

## Self-Review
- **Spec 覆盖**:§4 helper→Task1;§5.1 dialog-runner→Task2;§5.2 desktop-runner→Task3;§5.3 desktop-pool+recovery→Task4;§5.4 task-watcher→Task5;§8 验证→Task1脱机+Task6真机。全覆盖。
- **类型一致**:helper 四函数签名(hasWorkIframe/pickCtx/liveFrame/isWorkMainPage)在 Task2-5 引用一致;ctx 落地统一为"frameLocator 或 page,下游 .locator 用法不变"。
- **不破坏 iframe**:所有 pickCtx 在 hasIframe=true 时返回 frameLocator,与现状逐字一致。
- **占位**:Task5-Step3 需在实施时定位 task-watcher 定时器实际 async 方法名(读文件确认),已注明"找到实际方法名在其开头加保护"——实施时读 task-watcher 的 start/timer 回调确认。
