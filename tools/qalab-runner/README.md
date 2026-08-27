# qalab-runner —— 平台勾选用例 → 本地执行 → 回写 pass/fail 闭环

被测客户端:**纳米Work 桌面端**(Electron;Mac 进程 `Namiwork`、Windows `namiclaw.exe`,UI 加载 `work.n.cn`)。

> 📖 **要在自己电脑上跑 runner?看 [`操作手册.md`](操作手册.md)**(执行机操作员 SOP:安装 → 配置 → 启动 → 下发 → 看结果 → 排障)。
> 本 README 讲整体设计与运行细节;`DEPLOY.md` 是精简部署卡片。

```
qalab 平台(FastAPI)          runner.mjs(node,每 5s 轮询三条队列)
   │  ①勾选用例下发到设备
   │  ②GET pending → claim ── exec  → gui/e2e/api/cli/manual 执行
   │                          probe → 探测/巡检语义选择器
   │                          perf  → 性能采集(可选)
   │  ③PATCH 回写 {verdict, fail_kind, reason, evidence, report}
   ▼
  gui/e2e/api:有结构化 script → 确定性执行器(step-executor/api-executor,不经 LLM)
              无 script → Claude Code(headless)兜底:GUI 用 mcp__gui__*(CDP :9222)、api/cli 用 Bash
  cli:始终 Claude + Bash 起进程/验退出码
```

## 目录
- `runner.mjs` —— 核心:轮询平台三条队列(exec/probe/perf)、调确定性执行器或 Claude Code、回写结果;GUI 用例前自动冷启动被测客户端带 CDP。
- `step-executor.mjs` / `api-executor.mjs` —— 结构化 script 的**确定性执行器**(gui/e2e、api;不经 LLM,断言直接算 pass/fail)。
- `reset-home.mjs` / `os-key.mjs` / `core-keys.mjs` —— gui/e2e 用例间**复位自愈**(reload → 掉登录检测 → 首页就绪门禁 → 分层 ESC / 新建会话)。
- `perf-collect.mjs` —— 性能采集(perf 队列,共用同进程/配置/token)。
- `gui-mcp/gui-core.mjs` —— GUI 定位引擎(runner 与 MCP server 共用一套);`server.mjs` 把它封装成 `mcp__gui__*` 工具供 claude 调。
- `gui-mcp/selectors.json` —— 语义选择器注册表(用法/更新见 `gui-mcp/README.md`)。
- `.mcp.json` —— 注册 gui server(供 `claude -p` 加载)。
- `cases/example-gui-login.json` —— 样例 GUI 用例 payload。
- `run.sh`(Mac/Linux)/ `run.cmd`(Windows)—— 启动脚本;或直接 `node runner.mjs`。
- `platform/` —— **历史存档**:平台侧参考代码**已并入后端**(`backend/app/api/exec_queue.py` 等),勿重复并入;详见 `platform/已并入-勿重复并入.md`。

## 对话测评执行器(eval/)

`eval/` 是对话测评执行器(原 ai-eval-cli-yt,CommonJS)——跑 **eval_queue**(对话测评:驱动纳米Work对话、抓 WS 轨迹、大模型判定回写)。与本目录功能测试点 runner(`runner.mjs`)**共用同一套平台配置**(BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT),各自独立进程、互不干扰。

- 一次性准备:`cd eval && npm install`(装 playwright 等依赖;比功能点 runner 的零依赖重)。
- 启动:`run-eval.cmd`(Windows)/ `run-eval.sh`(Mac),或 `cd eval && node bin/ai-eval.js platform`(常驻轮询;`--once` 只跑一轮)。
- 配置:eval 侧 `.env` 加载器优先读上级 `tools/qalab-runner/.env`(与功能点 runner 共享一份),兜底 `eval/.env`。BASE_URL/RUNNER_TOKEN/RUNNER_ID 等同名同义、天然共享;飞书导出等 eval 专属项见 `.env.example`。

## 一次性准备
```powershell
# 1) 安装 GUI MCP 依赖(playwright-core 无需下载浏览器,连的是被测客户端自带 Chromium)
cd gui-mcp && npm install && cd ..

# 2) 确认 Claude Code 已登录且能 headless 无交互运行(claude -p "echo hi")
```

## 分阶段验证(强烈建议按序)
> 平台侧 4 接口**已并入后端**(`backend/app/api/exec_queue.py`),无需再并入;直接从握手开始。逐步 SOP 见 `操作手册.md`。
1. **验握手(不调 Claude)**:`./run.sh --dry`(Windows:`node runner.mjs --dry`)—— 只拉 pending / claim / 回写假 pass,确认 runner ↔ 平台通。
2. **接 GUI 首条**:平台派一条 gui 用例(如 `example-gui-login.json` 那类),去掉 `--dry`,跑通"冷启动被测客户端 → connectOverCDP → 断言 → 回写"。
3. 再加 api / cli 用例。

## 关键约束 / 坑
- **单实例锁**:namiclaw 双击打开不带调试端口;runner 的 `ensureNamiclaw()` 会先 `Stop-Process` 全杀再带参数冷启动。跑 GUI 用例会打断你手动开的客户端。
- **无人值守授权**:`runner.mjs` 里 `claude -p` 用 `--permission-mode acceptEdits` + `--allowedTools "Bash mcp__gui__*"`;按真实用例收敛白名单。
- **判定可信度**:能用 `gui_assert_text` / 退出码 / 接口响应等确定性断言就用,少让 LLM"看一眼觉得对"。
- **本机环境**:runner 用 node(本机 python 在 git-bash 下无法 fork);GUI 启动用 PowerShell `Start-Process`(bash 后台起 GUI 不可靠)。
