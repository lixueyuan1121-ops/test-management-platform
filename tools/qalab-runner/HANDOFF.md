# HANDOFF —— 项目交接说明(Windows → Mac)

> 读这一份就够上手。它讲清:项目要做什么、现在到哪一步、代码结构、**Mac 上怎么继续 + 哪些事在 Mac 上做不了**。

## 1. 项目目标(一句话)

在 **qalab 测试管理平台**(https://qalab.claw.qihoo.net,FastAPI + SQLAlchemy 2.0)上勾选设计好的用例,
**发送 → 本地 Claude Code(headless)执行对纳米Work桌面端的自动化测试 → pass/fail 回写平台对应用例**,形成闭环。

## 2. 数据流

```
qalab 平台            runner.mjs(node 轮询)         Claude Code(headless)        被测客户端
  │  ①勾选用例入队(POST /enqueue)                                                   namiclaw
  │  ②pending  ──GET /exec-queue──►  拉取 → claim                                   (Electron,
  │                                     │ 调 claude -p ────────►  按 kind 执行:        UI=work.n.cn)
  │                                     │                          gui → mcp__gui__*(CDP :9222)
  │                                     │                          api → Bash + curl
  │                                     │                          cli → Bash 起进程
  │  ③PATCH /exec-queue/{id}  ◄──回写 {verdict,reason,evidence}──┘
```

- **触发方式 = Pull 轮询**(runner 主动拉),平台/CC 零侵入,绕开一切网络入站问题。
- **GUI 方案 = B 方案**:GUI MCP server 用 Playwright `connectOverCDP(:9222)` 操作 namiclaw 的 DOM。
  已实测:namiclaw 带 `--remote-debugging-port=9222` 冷启动后,CDP 返回 `Chrome/132`,主页面 `work.n.cn`,
  **可按 CSS selector 断言,无坐标依赖、不怕锁屏**。

## 3. 目录结构

```
qalab-runner/
├── runner.mjs              ★ 核心:轮询平台 → 调 Claude Code → 回写。已跨平台(自动读 .env,GUI 冷启动分 Win/Mac)。
├── gui-mcp/
│   ├── server.mjs          GUI MCP server:gui_connect/click/fill/get_text/wait_for/assert_text/screenshot
│   └── package.json        依赖 @modelcontextprotocol/sdk + playwright-core
├── .mcp.json               注册 gui server(相对路径,跨平台)
├── cases/example-gui-login.json   样例 GUI 用例 payload
├── .env.example            ★ 配置样例 → 复制成 .env 填写
├── run.cmd                 Windows 启动脚本
├── run.sh                  Mac/Linux 启动脚本
├── platform/               ★ 要并入 qalab 后端的 FastAPI 代码(见 platform/README-并入说明.md)
│   ├── models/exec_queue.py
│   └── routers/exec_queue.py
└── README.md               运行细节与坑
```

## 4. 当前进度

| 部分 | 状态 |
|---|---|
| runner 骨架(轮询/claim/回写/--dry)| ✅ 写完 |
| GUI MCP server(7 个工具)| ✅ 写完 |
| namiclaw CDP 可行性 | ✅ **已在 Windows 实测通过** |
| 平台侧 FastAPI 代码(4 接口)| ✅ 写完并已并入 qalab(backend/) |
| 跨平台改造(.env / run.sh / GUI 冷启动分支)| ✅ 写完 |
| **端到端跑通(gui 用例)** | ✅ **2026-08-13 在 Mac 跑通** —— 见下 |

**结论:整条链路已在 Mac 上真正跑通(gui 用例 pass 回写)。** Mac 用 mac-01(装了 Namiwork),Windows 保留 win-01。

### 4.1 Mac gui 闭环实测记录(2026-08-13)
- 被测客户端 **Namiwork(纳米Work)Mac 版已装**:`/Applications/Namiwork.app`(Electron 42),可执行文件 `Contents/MacOS/Namiwork`。
- 实测带 `--remote-debugging-port=9222` 冷启动 → CDP 2s 就绪(`Chrome/148 Electron/42`),主页面 `work.n.cn`。
- **真实业务 UI 在跨域子 iframe `<vm_id>.work.n.cn` 里**(顶层 `work.n.cn/claw` 是空壳)。为此给 gui-mcp 加了
  **iframe 自动下钻**(`contentFrame()`):元素操作作用在业务 iframe,页面级(截图/goto)仍在顶层。
- **坑(已修):执行机上用户全局 MCP 会拖垮 claude**。claude 从 qalab-runner/ 跑时,除 `.mcp.json` 的 gui 外还会加载
  用户全局 `.claude.json` 的 MCP(context7/figma/playwright…),spawn 一堆 server 导致 runClaude 长挂。
  runner 已改为 `--mcp-config <abs>/.mcp.json --strict-mcp-config`,只加载 gui。另给 runClaude 加了硬超时。
- 闭环结果:seed 一条 gui 用例派 mac-01 → runner 冷启动/复用 CDP → claude 用 `mcp__gui__*`(gui_connect→
  gui_assert_text)→ 34s 回写 **pass**。

## 5. 在 Mac 上继续 —— 分阶段(按序,别跳)

### 阶段 0:环境
```bash
cp .env.example .env        # 填 BASE_URL / RUNNER_TOKEN / RUNNER_ID
cd gui-mcp && npm install    # 装 MCP + playwright-core(不下载浏览器)
# 确认 Claude Code 已登录且能 headless:claude -p "echo hi"
```

### 阶段 1:平台接口上线(与机器无关,在哪都能做)
按 `platform/README-并入说明.md` 把 4 个接口并入 qalab,配 `RUNNER_TOKEN`,建表。
验证:`curl -H "Authorization: Bearer <token>" "https://qalab.../api/exec-queue?runner=<id>"` 返回 `{"code":0,...,"data":[]}`。

### 阶段 2:验 runner ↔ 平台握手(Mac 上就能做,不需要客户端)
```bash
./run.sh --dry      # 只拉 pending / claim / 回写假 pass,不调 Claude、不碰客户端
```
平台放一条 pending 数据,看 runner 能否拉到并把它置成 pass。**这步通了,闭环的“平台↔runner”就焊死了。**

### 阶段 3:接第一条 **CLI 或 api** 用例(Mac 上可做,不依赖 namiclaw)
去掉 `--dry`,让 Claude 用 Bash 跑一条命令行/接口用例,回写真实 pass/fail。**闭环首次全链路跑通。**

### 阶段 4:GUI 用例 —— ⚠️ **见第 6 节,Mac 上有硬前提**

## 6. ⚠️ Mac 上做不了 / 需要注意的事(最重要,别踩)

1. **被测客户端 Namiwork(纳米Work)—— Mac 版已装,gui 可在 Mac 跑(2026-08-13 已验)。**
   - Mac:`/Applications/Namiwork.app`(Electron 42);`.env` 里 `NAMICLAW_EXE=/Applications/Namiwork.app/Contents/MacOS/Namiwork`。
   - Windows(win-01)保留:`D:\Program Files\namiclaw\Application\namiclaw.exe`。
   - 两台各用不同 `RUNNER_ID`、共用同一 `RUNNER_TOKEN`;平台入队时**在前端选哪台就派哪台**(kind 来自用例 exec_kind,平台不强绑定 kind↔机器)。
   - **真实业务 UI 在跨域子 iframe(`<vm_id>.work.n.cn`),不是顶层 `work.n.cn/claw`。** gui-mcp 已做 iframe 自动下钻,
     写 selector 时按业务页面(iframe 内)的 DOM 写即可,无需自己处理 frame。

2. **Windows 专属实现已隔离**,搬到 Mac 不会报错(会走另一分支或跳过):
   - `runner.mjs` 的 `coldStartClient()`:`process.platform` 分 Windows(PowerShell)/ 其它(spawn)。
   - `.env.example` 里 `NAMICLAW_EXE` 的 Windows 路径 —— Mac 上改掉或留空。

3. **本机(旧 Windows)的历史环境坑,到 Mac 后不再适用**——别被 README/注释里的相关说明误导:
   - “python 无法 fork、要用 node 复刻” “git-bash 启动慢 120s” “PowerShell Start-Process 冷启动” 这些**都是那台 Windows 的特有问题**。Mac 上 node/python/shell 都正常。
   - runner 用 node 写这点**保留**(跨平台、无依赖,挺好),但不是因为 Mac 有 fork 问题。

4. **judge 可信度**(与平台无关,但影响结果质量):让 Claude 判 pass/fail 有主观性。用例设计上**优先确定性断言**(`gui_assert_text`、退出码、接口响应),少让 LLM“看一眼觉得对”。

## 7. 待确认 / TODO(交接遗留)

> 已并入 qalab 后端时完成的项标 ✅(实际代码见 backend/，非本目录 platform/ 存档件)。

- [x] ✅ 平台 4 接口已并入并按本仓库架构改写(Base/get_db/信封/枚举/schema 同步)。
- [x] ✅ 鉴权:runner 用单独长期 token(`settings.RUNNER_TOKEN` + `require_runner` 依赖);
      claim/report 额外校验 `?runner=<RUNNER_ID>` 归属,防多机共用 token 串扰。
- [x] ✅ 前端“发送到本地执行”:任务清单页勾选清单项 →`POST /api/exec-queue/enqueue`
      (入参 `{project_id, runner, checklist_item_ids}`,走用户 JWT + 项目角色校验)。
- [x] ✅ 回写落点:`PATCH` 同步 `checklist_item.exec_status`(pass→passed / fail→failed)。
- [x] ✅ **命令注入(HIGH)已修**:用例 payload(用户可控)改经 **stdin** 传给 claude,不进命令行 argv。
      Windows 下 `shell:true` 执行 claude.cmd 是必需的,但 argv 里已无用户数据可被 cmd 解释。
- [ ] `--allowedTools "Bash mcp__gui__*"` 目前允许任意 Bash;按真实用例形态收敛成命令白名单(独立于上条注入修复,属纵深防御)。
- [x] ✅ **语义选择器库**:`gui-mcp/selectors.json`(注册表)+ server.mjs 内置定位引擎(移植自
      nami-work-test/lib/dom.js)。用例引用语义 key(如 `navTasks`/`chatInput`),不再猜 CSS;
      多候选失效自愈 + frameLocator 穿透业务 iframe。v1 基座导出自 nami-work-test/selectors.js 并
      并入 daily_test 实测项 + 主导航 nav*(57 key)。更新只改 selectors.json(git 拉取/更新)。
      用法/更新见 `gui-mcp/README.md`;claude 侧先 `gui_list_keys` 再用 key 定位。
- [x] ✅ MCP 隔离:runner 调 claude 加 `--mcp-config <abs>/.mcp.json --strict-mcp-config`,只加载 gui,屏蔽执行机全局 MCP。
- [x] ✅ runClaude 硬超时(`CLAUDE_TIMEOUT_MS`,默认 240s):卡死用例不再让 run 永久 running。

## 8. 怎么搬

整个 `qalab-runner/` 目录拷到 Mac 即可(`.env` 不要拷,到 Mac 重新 `cp .env.example .env`;`node_modules` 不要拷,重新 `npm install`)。
建议直接 `git init` 提交后推到内网 git,再在 Mac clone。`.gitignore` 已配好(忽略 .env / node_modules / evidence)。
