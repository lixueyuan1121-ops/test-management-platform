# qalab-runner —— 平台勾选用例 → 本地 Claude Code 执行 → 回写 pass/fail 闭环

被测客户端:**纳米Work 桌面端**(Electron,真实进程 `namiclaw.exe`,UI 加载 `work.n.cn`)。

```
qalab 平台(FastAPI)  ──①拉 pending──►  runner.mjs(node 轮询)
   ▲                                        │ 调 claude -p (headless)
   └──────③回写 {verdict}◄──────────────────┤
                                            ▼ 三类用例
      GUI: gui-mcp(Playwright connectOverCDP :9222)
      api: Claude 的 Bash + curl
      cli: Claude 的 Bash 起进程/验退出码
```

## 目录
- `runner.mjs` —— 轮询平台、调 Claude Code、回写结果;GUI 用例前自动冷启动 namiclaw 带 CDP。
- `gui-mcp/server.mjs` —— GUI MCP server,把 CDP 操作封装成 `mcp__gui__*` 工具。
- `.mcp.json` —— 注册 gui server(供 `claude -p` 加载)。
- `cases/example-gui-login.json` —— 样例 GUI 用例 payload。
- `run.cmd` —— 启动脚本(填 token 后运行)。
- `platform/` —— 平台侧要并入的 FastAPI 代码(见对话中的 model + router)。

## 一次性准备
```powershell
# 1) 安装 GUI MCP 依赖(playwright-core 无需下载浏览器,连的是 namiclaw 自带 Chromium)
cd D:\code\daily-work\qalab-runner\gui-mcp
npm install

# 2) 确认 Claude Code 已登录且能 headless 无交互运行(claude -p "echo hi")
```

## 分阶段验证(强烈建议按序)
1. **平台接口上线** → 把对话给的 `ExecRun` 模型 + `exec_queue` 路由并入 qalab,建一条 pending 测试数据。
2. **验握手(不调 Claude)**:`run.cmd --dry` —— 只拉 pending / claim / 回写假 pass,确认 runner ↔ 平台通。
3. **接 GUI 首条**:队列放 `example-gui-login.json` 那类用例,去掉 `--dry`,跑通"冷启动 namiclaw → connectOverCDP → 断言 → 回写"。
4. 再加 api / cli 用例。

## 关键约束 / 坑
- **单实例锁**:namiclaw 双击打开不带调试端口;runner 的 `ensureNamiclaw()` 会先 `Stop-Process` 全杀再带参数冷启动。跑 GUI 用例会打断你手动开的客户端。
- **无人值守授权**:`runner.mjs` 里 `claude -p` 用 `--permission-mode acceptEdits` + `--allowedTools "Bash mcp__gui__*"`;按真实用例收敛白名单。
- **判定可信度**:能用 `gui_assert_text` / 退出码 / 接口响应等确定性断言就用,少让 LLM"看一眼觉得对"。
- **本机环境**:runner 用 node(本机 python 在 git-bash 下无法 fork);GUI 启动用 PowerShell `Start-Process`(bash 后台起 GUI 不可靠)。
