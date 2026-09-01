# AI 任务队列 P1（归因试点）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans（本会话内联执行）。步骤用 `- [ ]` 勾选跟踪。

**Goal:** 落地 `ai_job` 队列骨架 + 进程内 worker 池 + 统一轮询端点，并把「AI 失败归因」从同步/SSE 直连改为「入队→worker 消费→前端轮询」。

**Architecture:** 特性端点建 `ai_job(pending)` 立即返回 `job_id`；进程内 daemon 线程池（大小=并发上限）条件 UPDATE 抢占 pending→running，调引擎→解析→写域表→job done/failed；前端轮询 `GET /api/ai-jobs/{id}` 取结果。零新依赖，契合单进程 uvicorn。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（MySQL5.6/SQLite）、threading daemon 池、Vue3/axios。

**Spec:** `docs/superpowers/specs/2026-09-01-ai-job-queue-design.md`

## Global Constraints
- 不引入 Redis/Celery/Docker/外部 worker；进程内线程池。
- 结构化数据用 `Text` 存 JSON 字符串（MySQL5.6 无原生 JSON 列）。
- 加表走 `app/db/migrate.py::ensure_*`（`inspect(engine).has_table` 守卫）+ startup 调用；模型在 `app/models/__init__.py` 汇总导入。
- 统一响应信封 `{code,msg,data}`；`ok()`/`raise HTTPException`。手写 `_to_out`。
- 后端自测：内存 SQLite + TestClient，`backend/.venv/bin/python -m scripts.test_x`。
- 每线程另开 `SessionLocal`（长跑连接失效根治）。
- 迁移期与旧 `AI_MAX_CONCURRENCY` 信号量并存不冲突（P1 只迁归因）。

---

### Task 1: `ai_job` 模型 + 建表迁移

**Files:**
- Create: `backend/app/models/ai_job.py`
- Modify: `backend/app/models/__init__.py`（导入 + `__all__` 加 `AiJob`）
- Modify: `backend/app/db/migrate.py`（新增 `ensure_ai_job_table()`）
- Modify: `backend/app/main.py::init_db`（调 `ensure_ai_job_table()`）
- Test: `backend/scripts/test_ai_jobs.py`

**Interfaces:**
- Produces: `AiJob(id, kind:str, provider:str, status:str, project_id:int|None, user_id:int|None, input:str|None(JSON), result:str|None(JSON), output_raw:str|None, error:str|None, ref_kind:str|None, ref_id:int|None, tokens:str|None, cost_usd, duration_ms:int|None, worker:str|None, claimed_at, created_at, updated_at)`；`status` 默认 `pending`。

- [ ] Step1 写失败测试：内存建表后 `AiJob(kind="triage", input=json)` 落库、查回，字段默认值正确（status=="pending"）。
- [ ] Step2 跑测试确认失败（模型不存在）。
- [ ] Step3 建模型（仿 `ai_eval.py` 风格：Text 存 JSON、`server_default`），`__init__` 汇总导入；`ensure_ai_job_table` 用 `insp.has_table("ai_job")` 守卫，缺则建（SQLite 靠 `create_all`；MySQL 靠该函数 `CREATE TABLE`）。`init_db` 里调用。
- [ ] Step4 跑测试确认通过。
- [ ] Step5 commit：`feat(ai-jobs): ai_job 队列表模型 + 建表迁移`

---

### Task 2: 队列核心——enqueue / 原子 claim / queue_position / get

**Files:**
- Create: `backend/app/services/ai_jobs.py`
- Test: `backend/scripts/test_ai_jobs.py`

**Interfaces:**
- Produces:
  - `enqueue(db, kind:str, *, provider:str|None, project_id:int|None, user_id:int|None, input:dict, ref_kind:str|None=None, ref_id:int|None=None) -> AiJob`（建 pending，commit，`input` json.dumps）
  - `claim_next(db) -> AiJob|None`（条件 UPDATE 抢占最早 pending→running，写 worker/claimed_at；`rowcount==1` 才算抢到；返回该 job 或 None）
  - `queue_position(db, job) -> int`（同 pending 中 created_at/id 早于本 job 的条数；running/done 返回 0）
  - `get_job(db, job_id) -> AiJob|None`

- [ ] Step1 写失败测试：`enqueue` 建出 pending 且 input 可 json.loads 回；两次 `claim_next` 对单条 pending 只有一次拿到、状态 running（原子性）；`queue_position` 对第 2 条 pending 返回 1。
- [ ] Step2 跑测试确认失败。
- [ ] Step3 实现：`claim_next` 用 `update(AiJob).where(AiJob.id==<最早pending.id>, AiJob.status=="pending").values(status="running",...)`＋`rowcount` 判定（仿 eval_pipeline 抢占）；worker 名用传入或线程名。
- [ ] Step4 跑测试确认通过。
- [ ] Step5 commit：`feat(ai-jobs): 入队/原子抢占/排队位次/查询`

---

### Task 3: handler 注册表 + 归因 handler + run_job（同步核心）

**Files:**
- Modify: `backend/app/services/ai_jobs.py`（`_HANDLERS` 注册表 + `run_job`）
- Modify: `backend/app/services/exec_triage.py`（新增 `run_triage_job(db, job) -> dict`，复用现有 `build_triage_prompt`/`parse_triage`；不再需要旧 `triage_run`，保留或删除均可，本任务不动它）
- Test: `backend/scripts/test_ai_jobs.py`

**Interfaces:**
- Produces:
  - `register_handler(kind:str, fn:Callable[[Session, AiJob], dict])`；`_HANDLERS: dict`
  - `run_job(session_factory, job_id:int) -> None`：另开 session 取 job→查 handler→跑→成功置 `status=done, result=json, output_raw, tokens/cost/duration`，失败置 `status=failed, error`（**不覆盖域数据**）。异常全捕获。
  - `exec_triage.run_triage_job(db, job)`：读 `job.input={run_id,provider}`→载 ExecRun→`engine.stream_generate` 累积→`parse_triage`→写 `run.triage_kind/run.triage`→返回归因 dict（含 run_id/provider/at）。引擎 error/解析失败 → 抛异常（由 run_job 记 failed）。
- Consumes: `generators.get_provider/normalize_provider`、`build_triage_prompt`、`parse_triage`。

- [ ] Step1 写失败测试：seed failed ExecRun；`enqueue(kind="triage", input={"run_id":501})`→`run_job(_Session, job.id)`（patch 引擎返回合法 JSON）→ job.status==done、job.result["kind"]=="bug"、ExecRun.triage_kind=="bug"；再来一条坏输出引擎 → job.status==failed、error 非空、且已有 triage_kind 不被覆盖。
- [ ] Step2 跑测试确认失败。
- [ ] Step3 实现 handler + run_job；triage handler 在 exec_triage 模块 import 时 `ai_jobs.register_handler("triage", run_triage_job)`（worker 启动前确保 import；在 `ai_jobs` 里惰性 `import app.services.exec_triage`）。
- [ ] Step4 跑测试确认通过。
- [ ] Step5 commit：`feat(ai-jobs): handler 注册表 + 归因 handler + run_job`

---

### Task 4: worker 池 + 启动收口 + 生命周期接线 + 配置

**Files:**
- Modify: `backend/app/services/ai_jobs.py`（`start_pool()`/`stop_pool()`/`_worker_loop()`/`notify_new_job()`/`reap_stale_ai_jobs_on_startup(db)`）
- Modify: `backend/app/core/config.py`（`AI_WORKER_CONCURRENCY: int = 2`）
- Modify: `backend/app/main.py`（startup 起池 + reap；shutdown 停池）
- Test: `backend/scripts/test_ai_jobs.py`

**Interfaces:**
- Produces:
  - `reap_stale_ai_jobs_on_startup(db) -> int`：`update(AiJob).where(status=="running").values(status="failed", error="服务重启中断")`，返回条数。
  - `start_pool(size:int|None=None)` / `stop_pool()`：起/停 N 个 daemon 线程；`_worker_loop` = 循环 `claim_next`→`run_job`，空转等 `Event`（`notify_new_job` 唤醒）+ 短超时兜底；停止标志退出。
  - `notify_new_job()`：`enqueue` 末尾调用，唤醒空闲 worker。
- Consumes: Task2/3 的 `claim_next`/`run_job`。

- [ ] Step1 写失败测试：seed 2 条 running AiJob → `reap_stale_ai_jobs_on_startup` 返回 2、状态变 failed；`start_pool(1)` 后 `enqueue` 一条 triage（patch 引擎）→ 轮询等待 job 达 done（有超时上限，如 5s）→ 断言 done；`stop_pool()` 不抛。
- [ ] Step2 跑测试确认失败。
- [ ] Step3 实现池 + reap；`enqueue` 末尾 `notify_new_job()`；config 加项；main.py startup 调 `reap_stale_ai_jobs_on_startup` + `start_pool()`（try 包裹不影响主服务），shutdown 调 `stop_pool()`。
- [ ] Step4 跑测试确认通过（含 `test_ai_jobs` 全绿）。
- [ ] Step5 commit：`feat(ai-jobs): worker 池 + 启动收口僵尸 + 生命周期接线`

---

### Task 5: 统一轮询端点 `GET /api/ai-jobs/{id}` + cancel

**Files:**
- Create: `backend/app/api/ai_jobs.py`（router，prefix `/api/ai-jobs`）
- Modify: `backend/app/api/router.py`（import + include_router）
- Test: `backend/scripts/test_ai_jobs.py`（新增端点段，或独立 `test_ai_jobs_api.py`）

**Interfaces:**
- Produces:
  - `GET /api/ai-jobs/{id}` → `ok({id,kind,status,queue_position,result(解析JSON),error,output_raw,ref_kind,ref_id,created_at,updated_at})`。鉴权：`job.user_id==user.id` 或 `assert_project_role(job.project_id, admin/member/guest)`；越权 403/404。
  - `POST /api/ai-jobs/{id}/cancel` → 仅 `pending` 置 `cancelled`（条件 UPDATE），否则 409。
- Consumes: `get_job`/`queue_position`。

- [ ] Step1 写失败测试：done job → GET 返回 status done + result；pending 第2条 → queue_position==1；非成员越权 → 非 0/403；cancel pending → cancelled，cancel running → 409。
- [ ] Step2 跑测试确认失败。
- [ ] Step3 实现 router（手写 `_to_out`，result/`input` json.loads 容错），注册。
- [ ] Step4 跑测试确认通过。
- [ ] Step5 commit：`feat(ai-jobs): 统一轮询端点 + 取消`

---

### Task 6: 归因端点改「入队→job_id」

**Files:**
- Modify: `backend/app/api/exec_queue.py`（`triage` 端点：去掉 SSE 流式，改为校验后 `enqueue(kind="triage", input={run_id,provider}, ref_kind="exec_run", ref_id=run_id, ...)` → `ok({"job_id": job.id})`）
- Modify: `backend/scripts/test_exec_triage.py`（端点契约改为：POST→job_id；`run_job` 同步跑；断言域写入。删 SSE/心跳断言）
- Test: 同上

**Interfaces:**
- Consumes: `ai_jobs.enqueue`。
- Produces: `POST /exec-queue/{id}/triage` → `{job_id}`（校验 404/400/503 仍同步返回）。

- [ ] Step1 改测试为新契约（RED）：POST 501 → data.job_id；`ai_jobs.run_job(_Session, job_id)`（patch 引擎）→ poll `GET /api/ai-jobs/{job_id}` done + result.kind=="bug" + ExecRun.triage_kind=="bug"；passed→400、404 仍普通响应。
- [ ] Step2 跑测试确认失败。
- [ ] Step3 改端点：保留 404/400/503 前置校验；引擎可用性检查保留（不可用直接 503，不入队）；否则 enqueue 返回 job_id。移除 SSE `sse()`/`StreamingResponse`（若无其它用途，连带清理该文件 `_sse`/`SessionLocal`/`StreamingResponse` 未用导入）。
- [ ] Step4 跑测试确认通过 + `test_ai_jobs` + exec 回归全绿。
- [ ] Step5 commit：`feat(ai-jobs): 归因端点改入队, 结果走轮询`

---

### Task 7: 前端轮询接入

**Files:**
- Modify: `frontend/src/api/index.js`（新增 `pollAiJob`；`triageExecRun` 改「POST 拿 job_id → pollAiJob」，两处 axios 调用带 `{silent:true}` 交由调用方统一提示）
- Modify: `frontend/src/views/ExecResults.vue`（`doTriage` 保持 catch 单条 `ElMessage.error`；`_triaging` 覆盖整段轮询；可选 onTick 展示「排队第 N 位/归因中…」）
- Test: `npm run build`

**Interfaces:**
- Consumes: `GET /api/ai-jobs/{id}`、`POST /exec-queue/{id}/triage`。
- Produces: `pollAiJob(jobId,{interval=2000,signal,onTick}) -> Promise<result>`（done resolve result / failed|cancelled reject）；`triageExecRun(run_id,provider) -> Promise<result>`。

- [ ] Step1 实现 `pollAiJob`（轮询 `http.get('/ai-jobs/'+id,{silent:true})`，done 返回 result、failed/cancelled 抛错、间隔 setTimeout）。
- [ ] Step2 改 `triageExecRun`：`const {job_id}=await http.post('/exec-queue/'+id+'/triage',null,{params,silent:true}); return pollAiJob(job_id,{onTick})`。
- [ ] Step3 `doTriage`：catch 单条提示（已具备）；`_triaging` 覆盖全程。
- [ ] Step4 `npm run build` 通过。
- [ ] Step5 commit（含 dist）：`feat(ai-jobs): 前端归因改入队+轮询`

---

## Self-Review
- **Spec 覆盖**：ai_job 表(T1)/worker 池限流(T4)/抢占(T2)/启动收口(T4)/轮询端点+cancel(T5)/归因迁移(T6)/前端轮询(T7) 均有任务；P1 只迁归因（spec 分期一致）。P2/P3 另立计划。
- **占位符**：无 TBD；各任务给了确切签名与测试意图。
- **类型一致**：`enqueue`→`AiJob`、`run_job(session_factory,job_id)`、`run_triage_job(db,job)`、`pollAiJob(jobId,opts)→result`、端点 `{job_id}` 全程一致。
- **风险**：worker 池线程在测试中不启动——测试直接调 `run_job`（同步）验证 handler；Task4 单独小测池启停+reap，带超时上限防挂起。
