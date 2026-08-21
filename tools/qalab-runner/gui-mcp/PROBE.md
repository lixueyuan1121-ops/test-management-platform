# 选择器巡检工具(probe-selectors)

`selectors.json` 健康检查 —— 逐页在真实纳米 Work 上探测注册的语义 key 还能不能定位到元素,揪出失效/冗余，供人工决定是否更新 `selectors.json`。复用同目录 `gui-core.mjs` 定位引擎。

## 前提

1. 纳米 Work 桌面端带调试端口启动、且已登录：
   - 彻底退出纳米 Work（含托盘）
   - `& "安装路径\namiclaw.exe" --remote-debugging-port=9222`
2. 本目录已装 `playwright-core`（`npm install`）。
3. **巡检开始时把纳米 Work 停在首页 / 一个对话会话**（否则 shell/chat 类 key 会因当前页不对而假失效）。

## 用法

```bash
node probe-selectors.mjs                 # 巡检所有 navMode=auto 的页
node probe-selectors.mjs --page experts  # 只巡某页(manual 页需先手动切过去)
node probe-selectors.mjs --keys a,b,c    # 只在当前页探指定 key(不导航)
node probe-selectors.mjs dump --page X   # 在(切到)某页 dump 全部可交互控件+CSS候选
```

## 报告怎么读

- **命中**：该 key 在对应页面能定位到 ✓
- **⚠ 失效**：导航到该页了、静态 key 却没命中 → 该有却没有，可能要修候选或删。**先确认不是"当前页不对"造成的假失效**（看脚本首行打印的 url）。
- **🔵 未归类**：`selectors.json` 里有、但 `probe-manifest.json` 任何桶都没覆盖 → 漏配 manifest 或真冗余。
- **跳过的待触发项**：`triggeredKeys`，要点开/发消息/切子 tab 才出现，不作失效判据。

## 失效了怎么修（人在回路）

1. `dump --page 对话` 列出当前页所有控件 + 自动生成的 CSS 候选
2. 从 dump 里指认"哪个是 chatInput"，拿到新候选
3. 把"给 X 加候选 / 删 Y"告诉维护者，**改 `selectors.json` 由人拍板**（改前列 diff、自动备份 `.bak`）

工具**只读**，绝不自动改 `selectors.json`。

## manifest（probe-manifest.json）

每页 `{page, navMode(auto/manual), nav(导航步骤), staticKeys(该有的), triggeredKeys(要触发的)}`。
分类首版基于实测 + `selectors.js` 的 desc，跑一次按实际微调（某 key 其实要触发 → 挪到 triggeredKeys；某页 nav 点不到 → 修 nav 或改 manual）。
