# AI 任务异步队列 + Worker 池限流 + 前端轮询 —— 设计

- 日期：2026-09-01
- 状态：已评审（用户确认形状），待落 writing-plans
- 关联：`fix(exec) 9ba26d1f`（归因改 SSE 心跳消 504，本方案的止血前置）；`AI_MAX_CONCURRENCY=2` 现状

## 1. 背景与问题

平台所有 AI 动作（约 7 处）当前都**同步/SSE 直连生成引擎**（claude CLI 子进程 / deepseek HTTP 网关），并由全局信号量 `AI_MAX_CONCURRENCY=2` 限流，且**超限即拒绝**（`claude_runner._slots.acquire(blocking=False)` → 「AI 生成繁忙」）。后果：

- **并发门槛=2**：第 3 个并发 AI 请求直接被拒；用户一多，「繁忙」误拒频发。
- **504**：长调用占着 HTTP 连接，任何反代/网关空闲超时会切断（归因已用 SSE 心跳止血，但仍是同步占线程）。
- **线程占用**：FastAPI 同步端点在有限线程池里跑长调用，长任务（综合评价最长 15min）占线程，规模化后拖垮普通接口。

用户补充的关键事实：**现有「流式」页实际也只在最后一把出结果、并未真正逐字吐字**——因此改成「入队+轮询」不损失现有体验。

## 2. 目标 / 非目标

**目标**
- 把 AI 调用从「每请求同步直连」改为「**入队 → 进程内 worker 池按并发上限消费 → 前端轮询取结果**」。
- 并发从「超限拒绝」变为「**有界排队等待**」：多余任务排队而非报错。
- HTTP 请求时长与模型调用时长**解耦**：彻底消除 504、不再长占请求线程。
- 全 7 处 AI 动作统一走队列（用户已选「全量入队+轮询」）。

**非目标**
- 不引入 Redis / Celery / 外部 worker / Docker（违背线上 Windows + 单进程 uvicorn + 无 Docker + MySQL 5.6 + 依赖最小化约束）。
- 不做真流式逐字（现无此体验，YAGNI；预留 `output_raw` 周期刷新做「伪流式」的扩展点，本期不实现）。
- 不改鉴权模型、不改各特性端点的入参校验语义。

## 3. 关键决策（含备选与理由）

**D1 队列骨架 = 新建 `ai_job` 表**。备选复用 `AiTask` 被否：`AiTask` 已绑生成域（`case_count`/`task_id`/`TestCase` FK、仅覆盖生成类），塞入归因/判定会污染语义。`ai_job` 只作「执行/队列记录」，域写入（`TestCase`/`EvalQuery`/`ExecRun.triage`/`EvalRun.verdict`/`EvalTask.summary_html` 等）由各 handler 完成。`AiTask` 继续作为「生成任务」的业务记录（生成类 handler 内部照常建/更新 `AiTask`）。

**D2 worker 形态 = 进程内 daemon 线程池**。与现有 `eval_pipeline`/`notify`/`claude_runner` 线程范式一致，零新依赖，契合单进程 uvicorn。asyncio 被否（引擎是阻塞子进程/HTTP，和 async 冲突）；外部 worker 被否（违背无 Docker）。**并发上限 = 池大小**，取代「超限拒绝」信号量。

**D3 抢占 = 条件 UPDATE 门闩**。`UPDATE ai_job SET status='running',… WHERE id=? AND status='pending'`，`rowcount==1` 者胜——沿用 `eval_pipeline` 已验证的原子抢占，防多 worker 双跑。

**D4 全量迁移、分阶段上线**。每阶段独立可上线可回滚。

## 4. 架构与数据流

```
前端 ──POST 特性端点(鉴权+校验不变)──▶ 建 ai_job(pending) ──▶ 立即返回 {job_id}
                                              │ 触发 Event 唤醒 worker
worker 池(N 线程) ──条件UPDATE抢占 pending→running──▶ 调引擎 stream_generate(累积)
                  ──▶ 解析 ──▶ 写域表(TestCase/triage/verdict/summary…) ──▶ job done/failed(+result)
前端 ──GET /api/ai-jobs/{id} 每~2s轮询──▶ {status, queue_position, result, error} ──▶ done 渲染
```

## 5. 数据模型：`ai_job`（新增表，走 `migrate.py::ensure_*` 加表，不改老表）

| 列 | 类型 | 说明 |
|---|---|---|
| id | PK int | |
| kind | String(32) index | `triage`/`testcase_gen`/`eval_query_gen`/`eval_judge`/`eval_summary`/`script_gen`/`feedback_script` |
| provider | String(16) | claude/deepseek/…（归一后） |
| status | String(16) index | pending/running/done/failed/cancelled |
| project_id | int index, nullable | 鉴权与展示 |
| user_id | int nullable | 发起人（鉴权） |
| input | Text | JSON：该 kind 所需入参（如 `{run_id}` / `{requirement,dimensions,eval_task_id}`），**下发那刻的快照** |
| result | Text nullable | JSON：结构化结果（回给前端渲染） |
| output_raw | Text nullable | 模型原始文本（排障；预留伪流式刷新） |
| error | Text nullable | 失败原因 |
| ref_kind / ref_id | String(24)/int nullable | 回链域对象（如 `exec_run`/`ai_task`/`eval_task`），便于幂等与追溯 |
| tokens / cost_usd / duration_ms | 记账 | 复用现有口径 |
| worker | String(48) nullable | 抢到的线程名（排障） |
| claimed_at / created_at / updated_at | DateTime | 队列时序、`queue_position` 计算 |

`queue_position` = 同 `pending` 中 `created_at` 早于本 job 的条数（读时现算，不落列）。结构化数据一律 Text 存 JSON（兼容 MySQL 5.6，无原生 JSON 列）。

## 6. Worker 运行时（新 `app/services/ai_jobs.py`）

- **kind→handler 注册表**：`HANDLERS: dict[str, Callable[[Session, AiJob], dict]]`。每个 handler = 「读 `job.input` → 建 prompt → `engine.stream_generate` 累积 → 解析 → 写域表 → 返回 result dict」。逐 kind 隔离、可单测。复用现有纯函数（`build_triage_prompt`/`parse_triage`、`build_eval_query_prompt`/`parse_eval_queries`、eval_judge/summary 的 prompt+解析等），**只搬「编排+落库」，不重写 prompt/解析**（避免与既有逻辑漂移）。
- **抢占**：D3 条件 UPDATE；claim 后写 `worker`/`claimed_at`。
- **池**：`AI_WORKER_CONCURRENCY`（新配置，默认 2，延续现值）。入队时 `threading.Event.set()` 唤醒；worker 空转时短轮询（如 2s）兜底。每线程另开 `SessionLocal`（长跑连接失效根治，沿用 eval_summary 套路）。
- **硬超时**：复用引擎自带 `AI_TIMEOUT_SECONDS=900` 硬超时；worker 侧兜底把超时 job 置 failed。
- **启动自愈**：`reap_stale_ai_jobs_on_startup()`——把残留 `running` 收口为 failed（沿用 `reap_stale_running_on_startup` 范式，防重启后僵尸）。
- **优雅退出**：daemon 线程随进程退出；未完成 job 留 `running`，靠启动自愈回收（与平台其它线程一致，不额外做 join）。
- **信号量退场**：迁移完成后删除 `claude_runner`/`deepseek_runner` 里的 `_slots` 拒绝逻辑（并发改由池大小控制）。分阶段期间两者并存不冲突（未迁的仍走旧信号量）。

## 7. API 与轮询契约

- **特性端点**：路径/鉴权/校验**不变**，内部由「同步调引擎」改为「建 `ai_job(pending)` → 返回 `{job_id}`」。涉及：`POST /exec-queue/{id}/triage`、`POST /ai/testcases`、`POST /ai/eval-queries`、`POST /ai/testcases/{id}/script`、`POST /eval-tasks/{id}/summarize`、判定端点、feedback 脚本生成。
- **统一轮询**：`GET /api/ai-jobs/{id}` → `{id, kind, status, queue_position, result, output_raw?, error, created_at, ...}`。鉴权：job 所有者或该 `project_id` 成员。
- **取消**：`POST /api/ai-jobs/{id}/cancel`，仅 `pending` 可取消（`running` 无法远程中断，返回 409）。
- **前端统一助手** `pollAiJob(jobId, {interval=2000, signal, onTick})`：轮询到 `done` resolve `result`、`failed` reject `error`；`onTick` 回传 `queue_position`/`status` 供「排队第 N 位 / 生成中…」展示。替换现有各 SSE 消费者。

## 8. 分阶段（每阶段独立上线 + 回滚）

- **P1 试点**：`ai_job` 表 + worker 池 + `GET /api/ai-jobs/{id}`(+cancel) + **归因**改入队轮询。最小、结果型、刚碰过。绿了上线验证并发/504 是否根治。
- **P2 结果型**：判定 `eval_judge`、脚本生成（`ai.py generate_script`、`feedback`）。
- **P3 生成/评价型**：测试点生成、对话 query 生成、综合评价 → 入队轮询。迁完后**删旧 SSE 端点 + 退场「超限拒绝」信号量**。

每阶段 = 一个 writing-plans 计划；本 spec 覆盖整体，计划分期落。

## 9. 错误处理

- 引擎报错/解析失败/超时 → job `failed` + `error`；**不覆盖既有域数据**（如归因失败不动已有 `triage_kind`，沿用现有语义）。
- 入队时特性端点的前置校验（404/400/403/引擎不可用 503）仍**同步**返回，不进队列。
- 前端轮询到 `failed` → `ElMessage.error(error)`；`pollAiJob` 网络抖动重试有限次。

## 10. 测试（沿用内存 SQLite + TestClient 自测脚本）

- 各 handler 单测：mock 引擎 → 断言域表被正确写入 + job 置 done/result。
- 抢占原子性：两次 claim 同一 pending 仅一胜。
- 启动自愈：残留 running → failed。
- 轮询端点鉴权（所有者/成员/越权）、queue_position 计算、cancel 仅 pending。
- 每阶段特性端点：POST 返回 job_id + 不再同步阻塞；端到端（enqueue→worker 同步跑一次→poll done）。

## 11. 风险与权衡

- **生成页失去「进行中文本」**：现无逐字流，无损失；预留 `output_raw` 周期刷新做伪流式，本期不做。
- **单进程内存/子进程压力**：池大小=并发上限，`AI_WORKER_CONCURRENCY` 可调；claude 每任务 fork 子进程，池不宜过大。
- **迁移期双轨**：P1–P3 期间已迁走队列、未迁仍走旧信号量，两套限流并存但互不干扰；全部迁完再删旧信号量。
- **重启丢在跑任务**：靠启动自愈收口为 failed，用户重试（与平台既有线程行为一致）。
