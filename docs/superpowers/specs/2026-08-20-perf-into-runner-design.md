# 设计:性能采集(perf-agent)并入 qalab-runner

- 日期:2026-08-20
- 状态:已评审(待落实现计划)
- 关联代码:`tools/qalab-runner/`、`tools/perf-agent/`、`backend/app/api/perf.py`、`backend/app/api/exec_queue.py`、`backend/app/api/probe.py`、`backend/app/core/deps.py`

## 1. 背景与问题

平台已有两个独立的"执行机"(runner)工具,团队成员各自部署到自己机器:

- **qalab-runner**(`tools/qalab-runner/runner.mjs`):常驻轮询,认领并执行 exec(GUI/E2E/API/CLI 用例)与 probe(选择器探测)两类任务。以"拷文件夹 / git clone"方式分发给写代码的成员。
- **perf-agent**(`tools/perf-agent/perf-agent.mjs`):常驻轮询,认领并执行 perf(性能采集)任务,调用 `nami-perfdog` 采集引擎。以"下载 zip"方式(`pack-agent.bat` → `frontend/public/perf-agent.zip` → 工具广场)分发给不碰代码的被测机用户。

**痛点**:线上(`https://qalab.claw.qihoo.net`)下发性能任务后长期 `pending`,根因是没有 perf-agent 在轮询认领。用户已部署好 qalab-runner,不希望为性能采集再单独部署、配置、常驻第二个进程。

## 2. 目标与非目标

**目标**
- 让 qalab-runner **一个进程、一套已注册的设备 token / runner_id**,同时认领 exec / probe / perf 三类任务。
- perf-agent 退役,能力(常驻轮询 + 交互采集控制 + 本地 session 补传 upload)全部并入 qalab-runner。
- 覆盖**全部**性能场景:长监控(无人值守自动)+ 交互场景(冷启动/对话/热启动/杀进程/首次安装,经平台「采集控制」页远程 prompt/signal 控制)。
- 分发同时覆盖两类用户:写代码的成员 `git clone`;被测机用户下载 `qalab-runner.zip`。

**非目标(YAGNI)**
- 不改后端 perf 接口(`/api/perf/*` 契约不变;runner 侧鉴权已是统一的 `require_runner_ctx`)。
- 不做 exec / probe / perf 的并行执行——一台机同一时刻只做一件事(见 §6)。
- 不改 `nami-perfdog` 采集引擎本身。

## 3. 关键决策(已确认)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 覆盖场景范围 | **全部场景**(长监控 + 交互) |
| 2 | perf-agent 去留 | **退役,全并入 qalab-runner** |
| 3 | 交互采集的并发语义 | **串行阻塞主循环**(采集期间暂停 exec/probe,符合物理独占) |
| 4 | 代码复用方式 | 采集核心抽成**共享模块** `perf-collect.mjs`,避免重复 |
| 5 | 分发方式 | qalab-runner 新增**打包脚本 + zip + 工具广场下载**,并保留 clone 分发 |

## 4. 架构

复用 runner.mjs 现有的"主循环多队列并列轮询"模式。当前主循环:

```
main(): for(;;) { await tick(); await handleProbes(); await sleep(POLL_MS); }
        tick()        → exec 队列  /api/exec-queue
        handleProbes()→ 探测队列  /api/probe/pending  (独立 try)
```

融合后新增第三个并列轮询 `handlePerf()`(perf 队列 `/api/perf/queue`),与前两者同为独立 try、串行执行:

```
main(): for(;;) { await tick(); await handleProbes(); await handlePerf(); await sleep(POLL_MS); }
```

`handlePerf()` 只是薄封装,真正的采集逻辑放在共享模块 `perf-collect.mjs`,由 runner 注入网络/日志/配置。

## 5. 组件改动(文件级)

### 5.1 新增 `tools/qalab-runner/perf-collect.mjs`(采集核心)

从 `tools/perf-agent/perf-agent.mjs` 迁移采集逻辑,改为**依赖注入**、不自带配置与网络:

- `pollPerfOnce(ctx)`:拉 `GET /api/perf/queue?runner=<id>` → 逐条 `claim` → `runPerfdog` → `PATCH` 回传;含交互场景的 prompt 上报 / signal 消费 / canceled 检测。
- `uploadLocalSessions(ctx, target)`:本地已采集 session 直传(原 `cmdUpload`;`target` 为目录 / `--all` / 缺省传未打标记的)。
- 内部辅助:`runPerfdog(ctx, args, before, runId, interactive)`、`readSession`、`decimate`、`listSessionDirs`、`ndjson`。
- `ctx` 形状:`{ api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID }`。`api` 复用 runner 的封装(Bearer token + `{code,msg,data}` 解包)。

### 5.2 改 `tools/qalab-runner/runner.mjs`

- 配置新增:
  - `PERFDOG_DIR`:默认"自身目录存在 `nami-perfdog.mjs` 则用自身目录,否则回落 `D:/git/test/nami-perfdog`"(与原 perf-agent 第 46 行逻辑一致;分发包内 nami-perfdog 与 runner 同目录)。
  - `SESSIONS_DIR = join(PERFDOG_DIR, 'sessions')`、`REPORT_SET_ID`(可选,upload 用)。
- `main()` 循环加第三段:`try { await handlePerf(); } catch (e) { log("perf 轮询异常:", e.message); }`
- 新增 `handlePerf()`:调 `perfCollect.pollPerfOnce({ api, log, RUNNER_ID, PERFDOG_DIR, SESSIONS_DIR, REPORT_SET_ID })`。
- 入口(argv 解析)新增 `upload` 子命令:`node runner.mjs upload [目录|--all]` → `perfCollect.uploadLocalSessions(ctx, arg)`;不带子命令时行为不变(常驻三队列轮询)。
- 启动日志补一行 perfdog 目录,便于排查。

### 5.3 退役 `tools/perf-agent/`

删除整个目录:`perf-agent.mjs`、`.env.example`、`run.cmd`、`DEPLOY.md`、`README.md`。其能力已全部并入 qalab-runner。

### 5.4 分发迁移(替代 perf-agent.zip)

- 新增 `pack-runner.bat`(替代 `pack-agent.bat`):将 `tools/qalab-runner/` 全目录 + `nami-perfdog` 采集引擎(`nami-perfdog.mjs`、`report-logic.mjs`、`纳米性能测试.bat`、`vendor/*`)打包为 `frontend/public/qalab-runner.zip`。删除 `pack-agent.bat`。
- `tools/qalab-runner/.env.example`:补 `PERFDOG_DIR`(留空即用自身目录)、`REPORT_SET_ID`(可选)。
- 工具广场下载卡片:由 perf-agent 改为 qalab-runner(改后端 tools 登记数据 / 前端展示;下载指向 `qalab-runner.zip`)。
- 删除 `frontend/public/perf-agent.zip`,由 `pack-runner.bat` 产出 `qalab-runner.zip`。
- 文档:`tools/qalab-runner/DEPLOY.md` 增补"性能采集(perf)"章节(前置:nami-perfdog 随包、被测应用在跑、交互场景用平台采集控制页);删除 perf-agent 的 DEPLOY/README。
- `start-all.bat`(若仍拉起 perf-agent):改为只拉起 qalab-runner。

## 6. 数据流(perf)

```
平台「性能测试→任务下发」 POST /api/perf/jobs        → PerfRun(status=pending, source=dispatch)
runner handlePerf 轮询    GET  /api/perf/queue?runner=<id>  → 拉到 pending
runner 认领               POST /api/perf/queue/{id}/claim   → status=running
runner spawn nami-perfdog run --scenario ... [--duration ...] [--proc ...]
  · 长监控:带 --duration 无人值守,采完即回传
  · 交互场景:管道模式
      - perfdog 提示行 → PATCH /api/perf/queue/{id}/prompt {prompt}      (上报提示)
      - 轮询同一 PATCH 拿 {signal_seq,status}:seq 变大→向 perfdog stdin 写 "\n";status=canceled→kill
      - 平台「采集控制」页(PerfCollect.vue)点「继续」→ POST /api/perf/queue/{id}/signal(seq+1)
回传                      PATCH /api/perf/queue/{id} {outcome,meta,samples,events} → status=completed
报告                      平台「性能报告」按报告集展示
```

## 7. 错误处理与并发语义

- `handlePerf()` 用独立 try 包裹:perf 异常只记日志,不影响 exec / probe 轮询与下一轮(与 `handleProbes` 一致)。
- 采集失败:`PATCH /api/perf/queue/{id}` 回写 `outcome=failed` + `error`。
- 采集被平台取消:回传前检测 `status=canceled`,跳过回传(不覆盖取消态)。
- **并发语义(串行阻塞)**:交互采集会阻塞主循环(采集期间 exec/probe 暂停)。这是**刻意**的——一台执行机同一时刻只做一件事,避免采性能时又跑 GUI 用例导致 CPU 争抢、数据被污染。长监控 40s / 交互场景等待人工操作期间,该机不认领其他任务,符合预期。
- 进程级兜底沿用 runner.mjs 现有的 `uncaughtException` / `unhandledRejection` 只记日志不退出。

## 8. 验收标准

1. **启动**:`node runner.mjs` 日志出现 perf 轮询启动行(含 base / runner / perfdog 目录)。
2. **长监控端到端**:平台下发长监控(如 40s)→ runner 自动认领采集回传 → 「性能报告」出现该次曲线。
3. **交互端到端**:下发冷启动 → runner 认领并跑 perfdog → 平台「采集控制」页看到提示、点「继续」推进 → 采完 completed → 报告可见。
4. **exec/probe 不回归**:融合后 GUI/E2E 用例仍能正常认领执行、探测仍正常。
5. **upload 子命令**:`node runner.mjs upload` 能把本地未上传 session 直传平台。
6. **分发**:`pack-runner.bat` 产出 `qalab-runner.zip`,解压后 `node runner.mjs` 可直接跑(nami-perfdog 随包、PERFDOG_DIR 默认自身目录生效);工具广场可下载。

## 9. 风险与缓解

- **交互采集长时间占用**:采集期间该机不接 exec/probe。缓解:属预期语义;需并行的团队可用不同机器 / 不同 runner_id 分工。
- **nami-perfdog 打包遗漏**:采集依赖 `vendor/` 等。缓解:`pack-runner.bat` 显式拷贝并在验收 §8.6 校验解压即用。
- **存量 perf-agent.zip 使用者**:退役后旧下载失效。缓解:工具广场卡片与 DEPLOY 文档同步更新,指向 `qalab-runner.zip`。
- **代码迁移引入回归**:perf-collect 从 perf-agent 迁移。缓解:保持采集/交互协议逐行等价,仅改为依赖注入;端到端验收覆盖长监控与交互两条路径。
