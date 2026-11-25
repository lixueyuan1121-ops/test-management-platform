# 提测执行（md 任务）——把整份提测文档下发到执行机

## 解决什么

现在提测后的动作是：工作机上让 Claude Code 读 diff → 写一份 md 用例 → 通过 IM 发给测试机 →
人工在测试机上切 hosts、把 md 喂给 codex → codex 执行、报缺陷 → 人工收结果。

这条链路里"AI 分析"和"AI 执行"都能用，卡住的是三处人工：**跨机器投递、切环境、收结果**。
本模块把这三处搬进平台：页面上传 md → 选执行机与环境分组 → 执行机自动切 hosts、跑 agent、回写结果。

## 与现有 exec-queue 的边界

| | exec-queue（已有） | md_run（本模块） |
|---|---|---|
| 下发单位 | 结构化用例（gui/api/cli，逐条断言） | 一整份 md 文档 |
| 结果回流 | 执行结果页 / 质量门禁 / 失败聚类 / 发版质量 | 独立页「提测执行」 |
| 数据表 | `exec_run` | `md_run`（新表） |
| 队列 | `/api/exec-queue` | `/api/md-runs/queue`（独立轮询） |

**不共用表**是刻意的：`exec_run` 被统计、门禁、失败聚类、回归智选、发版质量、自动重试等
约三十处引用，混入"整份文档自由执行"会污染这些口径。做法与性能采集（`perf_run` + `/api/perf/queue`）一致。

## 流程

```
前端「提测执行」页 ──POST /api/md-runs──► md_run(pending)
                                              │
执行机 runner（MD_TASK_ENABLED=1）             │
  GET /api/md-runs/queue?runner=<id> ◄────────┘
  POST /queue/{id}/claim                  → running
  ① 快照系统 hosts
  ② 写本机分组 hosts-groups/<env_group>.hosts
  ③ 校验域名解析（期望值取自分组文件；不过就中止，不启动 agent）
  ④ 调 agent 执行整份 md（默认 `codex exec`，参数可配）
  ⑤ 读 agent 产出的 result.md → 抽失败用例与缺陷候选
  ⑥ 无论成败：还原 hosts
  ⑦ PATCH /queue/{id} 回写 verdict/result_md/findings/env_check
  期间每 60s POST /queue/{id}/heartbeat → 感知平台取消
```

## 敏感内容在哪

- **hosts 分组（真实 IP）只在执行机**：`MD_GROUPS_DIR` 下的 `<分组名>.hosts`。平台只存分组名。
- **账号密码只在执行机**：md 里写别名，agent 在本机账号文件里查。
- **agent 完整日志只在执行机**：`MD_RUNTIME_DIR/md-runs/md-<id>/agent.log`，不回传。
- 平台存 md 原文、agent 产出的结果文档、结构化缺陷候选。仓库是公开的，但库不是。

## 缺陷怎么上报

agent 执行完只产出**缺陷候选**（`findings`），不直接调缺陷系统。

人在「提测执行」详情页复核后点「上报到极库云」→ `POST /api/md-runs/{id}/report-bugs`
逐条调 `services/geelib.report_defect`，把 `external_ref` 回写到 findings 里。

刻意不做自动上报：环境问题、偶发失败自动灌进缺陷库，会迅速让缺陷单失去可信度。
要全自动可以复用 `GEELIB_AUTO_REPORT` 的开关思路再加，默认不写死。

## 需要执行机做什么（一次性）

1. `.env` 里 `MD_TASK_ENABLED=1`。**默认 0**：runner 靠自升级分发到所有执行机，
   新能力必须由人在该机显式打开，否则每台机器都会去抢这类任务。
2. 放好 hosts 分组文件（`MD_GROUPS_DIR`），从 SwitchHosts 导出即可。
3. 装提权助手：它只做三件事（快照/应用/还原系统 hosts），runner 与 agent 本身都不提权。
   - Mac：`bin/helper-mac.sh` → `/usr/local/bin/qa-hosts-helper`，sudoers 只放行这一条命令
   - Win：`bin/helper-win.ps1` + 计划任务「qa-hosts-helper」（最高权限、按需运行，不用 SYSTEM）
4. 配置 `MD_VERIFY_DOMAINS`（需要校验解析的域名）与 `MD_CODEX_*`（agent 调用方式）。

## 状态与取消

`pending → running → passed / failed / blocked / canceled`

- `blocked` = 环境或 agent 本身没跑起来（分组不存在、校验不过、agent 退出码非 0、超时、平台取消）。
  与"产品缺陷"区分开，避免环境问题被当成 bug 统计。
- 取消：平台置 `canceled`；执行机在下一次心跳看到后杀 agent 并还原环境。
  平台不强制杀进程——在切过 hosts 的机器上硬杀，环境会停在测试分组。

## 前端

菜单：功能测试 → 提测执行（`/md-tasks`）。
支持选文件或粘贴；选 md 时会把 frontmatter 里的 `env_group / client_version / req / title`
带进表单，省一次手填。

## 已知边界

- runner 会独占桌面（与 gui 用例同一把锁），所以只适合在这个专用测试机上开。
- agent 的调用参数随 codex 版本可能变化，`MD_CODEX_ARGS` 留了可配置口子。
- 结果解析是宽容的（认表格结论列与「## 缺陷」小节），认不出就当没缺陷；要更严格就在 md 模板里约定格式。
