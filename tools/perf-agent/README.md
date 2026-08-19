# perf-agent —— 性能任务执行机代理

把本机的 [nami-perfdog](../../../test/nami-perfdog) 采集能力接到测试管理平台的「性能测试」模块。
纯 Node（v18+），零依赖。对应平台后端 `app/api/perf.py` 的 runner 侧接口。

## 双轨模型（与平台一致）

| 轨道 | 场景 | 怎么触发 |
|---|---|---|
| **dispatch 下发·无人值守** | 长监控（带时长）| 平台「任务下发」建任务 → 本 agent 轮询认领 → 自动跑 perfdog → 回传 |
| **upload 本地采集·直传** | 冷启动/对话/热启动/杀进程/首次安装 等**需人工介入**的场景 | 本机用 `纳米性能测试.bat` 人工采集 → `perf-agent upload` 直传 |

> 为什么交互场景不走下发：perfdog 这些场景要人工「看基线→回车开始→操作→回车结束」，
> 执行机无法无人值守。agent 认领到这类任务会**如实回传 failed 并提示改走 upload**，不假装成功。

## 一次性准备

```bash
cp .env.example .env      # 按注释填 BASE_URL / RUNNER_TOKEN / RUNNER_ID / PERFDOG_DIR
```

- `RUNNER_TOKEN`：填后端 `.env` 的 `RUNNER_TOKEN`（共享），或在平台「我的设备」注册设备拿专属 token（推荐，任务归属到人）。
- `PERFDOG_DIR`：本机 `nami-perfdog` 目录（含 `nami-perfdog.mjs`）。

## 用法

```bash
# 1) 常驻轮询：自动执行下发的长监控任务
run.cmd
#   或： node perf-agent.mjs

# 2) 轮询一轮就退出（配 Windows 计划任务定时跑，或联调用）
node perf-agent.mjs poll-once

# 3) 直传本地已采集的 session（交互场景）
node perf-agent.mjs upload            # 传所有未打 .uploaded 标记的
node perf-agent.mjs upload --all      # 全传（忽略标记）
node perf-agent.mjs upload "20260817-105220.-冷启动-2.3.1197"   # 只传一个目录
```

上传成功后会在该 session 目录写一个 `.uploaded` 标记文件，`upload`（不带参数）默认跳过已传的。

## 数据流

```
平台「任务下发」──①下发(pending)──► GET /api/perf/queue ──► perf-agent
                                                              │ 认领 claim(running)
   长监控 → spawn `node nami-perfdog.mjs run --scenario 长监控 --variant X --duration Y`
                                                              │ 读 sessions/<新目录>/{meta,samples,events}
                                                              │ 抽稀 samples（每指标≤2000 点）
平台 PerfRun(completed) ◄──②PATCH 回传──────────────────────┘

本地交互采集（纳米性能测试.bat）→ sessions/<目录> → perf-agent upload → POST /api/perf/queue/upload
```

回传/直传后，数据进平台「性能报告」页，前端复用 `report-logic` 做场景×对象对比、胜负结论、KPI。
