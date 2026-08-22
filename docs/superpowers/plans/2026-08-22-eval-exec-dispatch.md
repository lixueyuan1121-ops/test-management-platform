# 对话测评执行下发 + CLI 执行器改造 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台把 eval_query 下发到执行机(落 eval_run),CLI 新增"平台模式"拉任务、驱动 work.n.cn 对话、CDP 截 WebSocket 帧规整成 trace 轨迹、回写平台。

**Architecture:** 平台新建 /api/eval-queue(仿 exec_queue,require_runner_ctx 鉴权),eval_run 加 target_engine 列,trace 走独立 multipart 端点存磁盘。CLI 新增 platform-client(HTTP)+ ws-trace(WS帧规整)两个纯模块 + 平台模式编排,复用现有 DialogRunner/DesktopPool,与飞书模式并存。只实现 namiwork 被测引擎。

**Tech Stack:** 平台 FastAPI+SQLAlchemy2.0;CLI Node18+ / node-fetch@2 / playwright(CDP)。work.n.cn WS 协议见 D:\code\namiwork\openclaw360-web\src\ui\AGENTS.md。

**Spec:** `docs/superpowers/specs/2026-08-22-eval-exec-dispatch-design.md`

## Global Constraints

- **隔离**:不改 exec_queue / gen_testcases / CLI 飞书模式。新建 eval-queue router + CLI 平台模式。
- **不用原生 JSON 列**:结构化数据 Text 存 JSON(MySQL5.6)。大 trace 走磁盘文件、DB 存 URL。
- **两份 schema 同步**:models/ai_eval.py + sql/schema.sql。
- **老库加列走 migrate**:eval_run 加 target_engine 走幂等 ensure_eval_run_target_engine + main.py 调用。
- **本仓库无测试框架**:验证用一次性 Python 脚本 / CLI 纯函数脱机验 / mock HTTP。后端命令 backend/ 下,SQLite。CLI 命令 D:\code\ai-eval-cli-yt 下。
- **鉴权**:enqueue 用 get_current_user+assert_project_role(admin/member);runner 侧 list/claim/report/trace 用 require_runner_ctx,设备 token 下用 ctx.device.runner_id 覆盖 query runner,claim/report 校验 r.runner==runner 否则 403。
- **信封** {code,msg,data} + ok() + 手写 _to_out。
- **只实现 namiwork 被测引擎**;codex/claude CLI out-of-scope。
- **WS 协议**:event 帧 {type:"event",event:"agent",payload:{stream,data,runId,sessionId,...}};thinking=stream:"thinking" 的 data.text;tool=stream:"tool" 的 data.{name,originalToolName,phase,toolCallId,args,result};mcp 靠 originalToolName 的 mcp__<server>__<tool> 前缀;nami_panel 信封展开一层;toolCallId 聚合多 phase 帧。

## 文件结构

**平台**:
- Modify `backend/app/models/ai_eval.py` — EvalRun 加 target_engine 列。
- Modify `backend/sql/schema.sql` — eval_run 加列。
- Modify `backend/app/db/migrate.py` — ensure_eval_run_target_engine。
- Modify `backend/app/main.py` — init_db 调用。
- Modify `backend/app/schemas/ai_eval.py` 或新建 — EvalEnqueueIn/EvalReportIn(放 schemas/,新建 schemas/eval_queue.py)。
- Create `backend/app/api/eval_queue.py` — 下发/拉取/认领/回写/trace/history 端点。
- Modify `backend/app/api/router.py` — 注册。
- Modify `backend/app/api/ai_eval.py` 无关;trace 存储目录复用 uploads/。

**CLI** (D:\code\ai-eval-cli-yt):
- Create `src/platform-client.js` — 平台 HTTP 对接。
- Create `src/ws-trace.js` — WS 帧规整 trace。
- Modify `bin/ai-eval.js` — 平台模式子命令编排。
- Modify `src/desktop-pool.js` — 挂 attachWsTrace(桌面主 page)。
- Modify `config/default.config.js` / `.env.example` — 平台段。

**前端**:
- Modify `frontend/src/views/AIEvalGen.vue` — 下发按钮 + runner 选择。
- Modify `frontend/src/api/index.js` — enqueueEvalQueries + (复用 listDevices)。

---

### Task 1: eval_run 加 target_engine 列

数据地基。后续下发落 target_engine 依赖它。

**Files:**
- Modify: `backend/app/models/ai_eval.py`(EvalRun,device_kind 附近)
- Modify: `backend/sql/schema.sql`(eval_run 建表)
- Modify: `backend/app/db/migrate.py`
- Modify: `backend/app/main.py`
- Verify(临时,删): `backend/_verify_target_engine.py`

**Interfaces:**
- Produces: `EvalRun.target_engine`(str|None);`migrate.ensure_eval_run_target_engine()`

- [ ] **Step 1: 模型加列** — `backend/app/models/ai_eval.py` 的 EvalRun,`device_kind` 列之后加:
```python
    # 被测引擎(namiwork/codex/claude...);本阶段只实现 namiwork。留空兼容。
    target_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- [ ] **Step 2: schema.sql 加列** — `CREATE TABLE \`eval_run\`` 内 `device_kind` 行后加:
```sql
  `target_engine` VARCHAR(32) NULL,
```

- [ ] **Step 3: migrate 函数** — `backend/app/db/migrate.py` 末尾加(对齐 ensure_eval_query_dimension 写法):
```python
def ensure_eval_run_target_engine() -> None:
    """eval_run 补 target_engine 列(被测引擎)。老库已建表走 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_run"):
        return
    if "target_engine" not in _columns("eval_run"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN target_engine VARCHAR(32) NULL"))
```

- [ ] **Step 4: init_db 调用** — `backend/app/main.py::init_db` 的 ensure_* 序列末尾加 `ensure_eval_run_target_engine()`;顶部 migrate import 名单加该名。

- [ ] **Step 5: 验证脚本** — `backend/_verify_target_engine.py`:
```python
from sqlalchemy import inspect
from app.db.session import Base, SessionLocal, engine
from app.models import EvalRun, Project
from app.db.migrate import ensure_eval_run_target_engine
Base.metadata.create_all(bind=engine)
cols = {c["name"] for c in inspect(engine).get_columns("eval_run")}
assert "target_engine" in cols, cols
ensure_eval_run_target_engine()  # 幂等
db = SessionLocal()
try:
    proj = db.query(Project).first(); pid = proj.id if proj else 1
    r = EvalRun(project_id=pid, runner="mac-01", target_engine="namiwork")
    db.add(r); db.commit(); db.refresh(r)
    assert r.target_engine == "namiwork"
    db.delete(r); db.commit()
    print("OK: target_engine 列建出/幂等/存取正常")
finally:
    db.close()
```

- [ ] **Step 6: 跑** — `python _verify_target_engine.py`(backend/下),末行 `OK: target_engine 列建出/幂等/存取正常`。依赖缺失先 pip install。Windows CreateFile Error:5 忽略。

- [ ] **Step 7: 删脚本** — `rm backend/_verify_target_engine.py`

- [ ] **Step 8: 提交**
```bash
git add backend/app/models/ai_eval.py backend/sql/schema.sql backend/app/db/migrate.py backend/app/main.py
git commit -m "feat(eval): eval_run 加 target_engine 列 + migrate 补列

被测引擎列(本阶段 namiwork)。新库 create_all 含,老库 ensure_eval_run_target_engine 幂等补。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 平台 /api/eval-queue 下发/拉取/认领/回写/trace 端点

平台闭环。仿 exec_queue。

**Files:**
- Create: `backend/app/schemas/eval_queue.py`
- Create: `backend/app/api/eval_queue.py`
- Modify: `backend/app/api/router.py`
- Verify(临时,删): `backend/_verify_eval_queue.py`

**Interfaces:**
- Consumes: `EvalRun`(含 target_engine)、`EvalQuery`、`require_runner_ctx`/`RunnerCtx`、`assert_project_role`、`AiTaskStatus`? 用 `EvalRunStatus`。
- Produces: `POST /api/eval-queue/enqueue`、`GET /api/eval-queue`、`POST /api/eval-queue/{id}/claim`、`PATCH /api/eval-queue/{id}`、`POST /api/eval-queue/{id}/trace`、`GET /api/eval-queue/history`。

- [ ] **Step 1: schemas** — `backend/app/schemas/eval_queue.py`:
```python
from pydantic import BaseModel, Field


class EvalEnqueueIn(BaseModel):
    project_id: int
    runner: str = Field("mac-01", max_length=64)
    target_engine: str = Field("namiwork", max_length=32)
    eval_query_ids: list[int] = Field(..., min_length=1)


class EvalReportIn(BaseModel):
    status: str  # "done" | "failed"
    share_link: str | None = None
    artifact_share_link: str | None = None
    answer: str | None = None
    reported_duration: str | None = None
    bean_cost: str | None = None
    tokens: str | None = None
    session_id: str | None = None
    reason: str | None = None
    duration_ms: int | None = None
```

- [ ] **Step 2: 端点文件** — `backend/app/api/eval_queue.py`(完整):
```python
"""对话测评执行队列:下发 eval_query → 执行器拉取/认领/回写 eval_run → trace 上传。

独立于 exec_queue(功能测试点执行)。沿用 {code,msg,data} 信封、手写 _to_out、
require_runner_ctx 双通道鉴权(设备 token 锁 runner_id / 共享 token 兜底)。
trace(会话轨迹)大、走独立 multipart 端点存磁盘,eval_run.trace 存 URL(避 MySQL5.6 TEXT 截断)。
"""
import json
import os
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import EvalRunStatus, ProjectRole
from app.db.session import get_db
from app.models import EvalQuery, EvalRun, User
from app.schemas.common import ok
from app.schemas.eval_queue import EvalEnqueueIn, EvalReportIn

router = APIRouter(prefix="/api/eval-queue", tags=["eval-queue"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _payload_of(q: EvalQuery) -> dict:
    """下发 payload 快照:执行器驱动对话要用的字段(避免执行时配置漂移)。"""
    return {
        "eval_query_id": q.id,
        "title": q.title,
        "prompt": q.prompt,
        "attachments": json.loads(q.attachments) if q.attachments else [],
        "dialog_options": json.loads(q.dialog_options) if q.dialog_options else {},
        "conversation_group": q.conversation_group,
        "turn_index": q.turn_index,
    }


def _to_out(r: EvalRun) -> dict:
    return {
        "run_id": r.id,
        "eval_query_id": r.eval_query_id,
        "project_id": r.project_id,
        "batch_id": r.batch_id,
        "runner": r.runner,
        "target_engine": r.target_engine,
        "device_kind": getattr(r.device_kind, "value", r.device_kind),
        "status": getattr(r.status, "value", r.status),
        "payload": json.loads(r.payload) if r.payload else {},
        "session_id": r.session_id,
        "share_link": r.share_link,
        "artifact_share_link": r.artifact_share_link,
        "answer": r.answer,
        "trace": r.trace,
        "reported_duration": r.reported_duration,
        "bean_cost": r.bean_cost,
        "reason": r.reason,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.post("/enqueue")
def enqueue(body: EvalEnqueueIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.eval_query_ids))
    qs = db.query(EvalQuery).filter(EvalQuery.id.in_(ids)).all()
    found = {q.id: q for q in qs}
    for qid in ids:
        q = found.get(qid)
        if q is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不存在")
        if q.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测评题 {qid} 不属于该项目")
    created = []
    batch_id = _new_batch_id()
    for qid in ids:
        q = found[qid]
        row = EvalRun(
            eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
            runner=body.runner, target_engine=body.target_engine,
            status=EvalRunStatus.pending, payload=json.dumps(_payload_of(q), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row); db.flush(); created.append(row.id)
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


@router.get("")
def list_pending(runner: str = Query("mac-01"), limit: int = Query(5, le=20),
                 db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow(); db.commit()
    rows = (db.query(EvalRun)
            .filter(EvalRun.status == EvalRunStatus.pending, EvalRun.runner == runner)
            .order_by(EvalRun.id).limit(limit).all())
    return ok([_to_out(r) for r in rows])


@router.post("/{run_id}/claim")
def claim(run_id: int, runner: str = Query(...), db: Session = Depends(get_db),
          ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r or r.status != EvalRunStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项不可认领")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = EvalRunStatus.running; db.commit(); db.refresh(r)
    return ok(_to_out(r))


@router.patch("/{run_id}")
def report(run_id: int, body: EvalReportIn, runner: str = Query(...),
           db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = EvalRunStatus.done if body.status == "done" else EvalRunStatus.failed
    r.share_link = body.share_link
    r.artifact_share_link = body.artifact_share_link
    r.answer = body.answer
    r.reported_duration = body.reported_duration
    r.bean_cost = body.bean_cost
    r.tokens = body.tokens
    r.session_id = body.session_id
    r.reason = body.reason
    r.duration_ms = body.duration_ms
    db.commit(); db.refresh(r)
    return ok(_to_out(r))


_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_TRACE_ROOT = os.path.join(_UPLOADS_DIR, "eval_traces")
_MAX_TRACE_BYTES = 20 * 1024 * 1024


@router.post("/{run_id}/trace")
async def upload_trace(run_id: int, file: UploadFile = File(...), runner: str = Query("mac-01"),
                       db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    """执行器上传会话轨迹 JSON。存 uploads/eval_traces/{run_id}.json,回写 eval_run.trace=URL。"""
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    data = await file.read()
    if len(data) > _MAX_TRACE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"轨迹过大(>{_MAX_TRACE_BYTES//1024//1024}MB)")
    try:
        json.loads(data)  # 校验是合法 JSON
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="轨迹须为合法 JSON")
    os.makedirs(_TRACE_ROOT, exist_ok=True)
    rel = f"eval_traces/{run_id}.json"
    with open(os.path.join(_UPLOADS_DIR, rel), "wb") as f:
        f.write(data)
    r.trace = f"/uploads/{rel}"; db.commit()
    return ok({"trace_url": f"/uploads/{rel}"})


@router.get("/history")
def list_history(project_id: int = Query(...), limit: int = Query(100, le=500),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun).filter(EvalRun.project_id == project_id)
            .order_by(EvalRun.id.desc()).limit(limit).all())
    return ok([_to_out(r) for r in rows])
```

- [ ] **Step 3: 注册** — `backend/app/api/router.py`:import 加 `eval_queue`;`include_router(ai_eval.router)` 后加 `api_router.include_router(eval_queue.router)`。

- [ ] **Step 4: 验证脚本** — `backend/_verify_eval_queue.py`(直接调函数验落库,不起 HTTP):
```python
"""Task2 验证:enqueue 落 eval_run pending / report 落库 / trace 存文件。backend/下运行。"""
import json, os
from app.db.session import SessionLocal
from app.models import EvalQuery, EvalRun, AiTask, Project, User
from app.core.enums import EvalRunStatus
from app.api.eval_queue import _payload_of, _to_out

db = SessionLocal()
try:
    proj = db.query(Project).first(); pid = proj.id if proj else 1
    q = EvalQuery(project_id=pid, provider="claude", title="t", prompt="写贪吃蛇",
                  attachments=None, dialog_options=None, conversation_group="g1", turn_index=0)
    db.add(q); db.commit(); db.refresh(q)
    # 模拟 enqueue 落库
    r = EvalRun(eval_query_id=q.id, project_id=pid, batch_id="B1", runner="mac-01",
                target_engine="namiwork", status=EvalRunStatus.pending,
                payload=json.dumps(_payload_of(q), ensure_ascii=False))
    db.add(r); db.commit(); db.refresh(r)
    assert json.loads(r.payload)["prompt"] == "写贪吃蛇"
    assert _to_out(r)["status"] == "pending" and _to_out(r)["target_engine"] == "namiwork"
    # 模拟 report
    r.status = EvalRunStatus.done; r.share_link = "http://x/s"; r.answer = "done"; db.commit()
    assert _to_out(r)["status"] == "done" and _to_out(r)["share_link"] == "http://x/s"
    db.delete(r); db.delete(q); db.commit()
    print("OK: eval-queue enqueue/report/_to_out 落库路径正常")
finally:
    db.close()
```

- [ ] **Step 5: 跑 + 端点注册冒烟** —
```bash
python _verify_eval_queue.py
python -c "from app.main import app; ps=[r.path for r in app.routes]; assert '/api/eval-queue/enqueue' in ps and '/api/eval-queue/{run_id}/trace' in ps, ps; print('OK: eval-queue 端点已注册')"
```
Expected: 两行 OK。

- [ ] **Step 6: 删脚本** — `rm backend/_verify_eval_queue.py`

- [ ] **Step 7: 提交**
```bash
git add backend/app/schemas/eval_queue.py backend/app/api/eval_queue.py backend/app/api/router.py
git commit -m "feat(eval): /api/eval-queue 下发/拉取/认领/回写/trace 端点

仿 exec_queue,落 eval_run。enqueue(用户JWT)+ list/claim/report/trace(require_runner_ctx)。
trace 走 multipart 存 uploads/eval_traces/{id}.json,eval_run.trace 存 URL。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CLI 平台客户端 src/platform-client.js

CLI 纯 HTTP 模块。仿 runner.mjs 的 api()。

**Files:**
- Create: `D:\code\ai-eval-cli-yt\src\platform-client.js`
- Verify(临时,删): `D:\code\ai-eval-cli-yt\_verify_platform_client.js`

**Interfaces:**
- Produces: `class PlatformClient { fetchPending(limit), claim(runId), report(runId, body), uploadTrace(runId, traceObj) }`

- [ ] **Step 1: 实现** — `D:\code\ai-eval-cli-yt\src\platform-client.js`:
```javascript
const fetch = require('node-fetch');
const FormData = require('form-data');

// 平台对话测评执行队列客户端:拉 pending / claim / report / 上传 trace。
// 仿 qalab-runner/runner.mjs 的 api():Bearer token + {code,msg,data} 解封。
class PlatformClient {
  constructor(config = {}) {
    this.baseUrl = (config.baseUrl || process.env.BASE_URL || '').replace(/\/$/, '');
    this.token = config.token || process.env.RUNNER_TOKEN || '';
    this.runnerId = config.runnerId || process.env.RUNNER_ID || 'mac-01';
    if (!this.baseUrl) throw new Error('平台模式需配置 BASE_URL(平台地址)');
    if (!this.token) throw new Error('平台模式需配置 RUNNER_TOKEN(在平台「我的设备」注册获取)');
  }

  get _headers() {
    return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` };
  }

  // 解 {code,msg,data} 信封;code 0/200/201/缺省视为成功,返回 data。
  async _api(method, path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method, headers: this._headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    let env;
    try { env = await res.json(); } catch { throw new Error(`平台返回非 JSON(HTTP ${res.status})`); }
    const code = env.code;
    if (code !== 0 && code !== 200 && code !== 201 && code !== undefined) {
      throw new Error(`平台接口失败(${path}): ${env.msg || code}`);
    }
    return env.data;
  }

  fetchPending(limit = 5) {
    return this._api('GET', `/api/eval-queue?runner=${encodeURIComponent(this.runnerId)}&limit=${limit}`);
  }
  claim(runId) {
    return this._api('POST', `/api/eval-queue/${runId}/claim?runner=${encodeURIComponent(this.runnerId)}`);
  }
  report(runId, body) {
    return this._api('PATCH', `/api/eval-queue/${runId}?runner=${encodeURIComponent(this.runnerId)}`, body);
  }
  // trace 走 multipart(与截图同理);multipart 不手设 Content-Type,让 form-data 自动补 boundary。
  async uploadTrace(runId, traceObj) {
    const form = new FormData();
    form.append('file', Buffer.from(JSON.stringify(traceObj), 'utf-8'), {
      filename: `${runId}.json`, contentType: 'application/json',
    });
    const res = await fetch(
      `${this.baseUrl}/api/eval-queue/${runId}/trace?runner=${encodeURIComponent(this.runnerId)}`,
      { method: 'POST', headers: { 'Authorization': `Bearer ${this.token}` }, body: form });
    let env;
    try { env = await res.json(); } catch { throw new Error(`trace 上传返回非 JSON(HTTP ${res.status})`); }
    if (env.code !== 0 && env.code !== undefined) throw new Error(`trace 上传失败: ${env.msg}`);
    return env.data;
  }
}

module.exports = PlatformClient;
```
(注:`form-data` 是否已在依赖?若无,Step 3 前 `npm i form-data`;node-fetch@2 配 form-data 是标准组合。)

- [ ] **Step 2: 验证脚本** — `_verify_platform_client.js`(mock 一个本地 http server 验 fetch/claim/report/uploadTrace 发对了):
```javascript
const http = require('http');
const PlatformClient = require('./src/platform-client');

const seen = [];
const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', c => body += c);
  req.on('end', () => {
    seen.push({ method: req.method, url: req.url, auth: req.headers['authorization'], ct: req.headers['content-type'] });
    res.setHeader('Content-Type', 'application/json');
    if (req.url.startsWith('/api/eval-queue?')) return res.end(JSON.stringify({ code: 0, data: [{ run_id: 1 }] }));
    res.end(JSON.stringify({ code: 0, data: { ok: true } }));
  });
});
server.listen(0, async () => {
  const port = server.address().port;
  const c = new PlatformClient({ baseUrl: `http://127.0.0.1:${port}`, token: 'T', runnerId: 'mac-01' });
  const pending = await c.fetchPending(3);
  await c.claim(1);
  await c.report(1, { status: 'done', answer: 'x' });
  await c.uploadTrace(1, { session_id: 's', thinking: 't', tool_calls: [] });
  server.close();
  const ok = (cond, m) => { if (!cond) { console.error('FAIL:', m, JSON.stringify(seen)); process.exit(1); } };
  ok(Array.isArray(pending) && pending[0].run_id === 1, 'fetchPending 解封 data');
  ok(seen[0].auth === 'Bearer T', 'Bearer 头');
  ok(seen[0].url.includes('runner=mac-01'), 'runner query');
  ok(seen[1].method === 'POST' && seen[1].url.includes('/1/claim'), 'claim');
  ok(seen[2].method === 'PATCH' && seen[2].url.includes('/api/eval-queue/1'), 'report PATCH');
  ok(seen[3].url.includes('/1/trace') && /multipart\/form-data/.test(seen[3].ct || ''), 'trace multipart');
  console.log('OK: PlatformClient fetch/claim/report/uploadTrace 正常');
});
```

- [ ] **Step 3: 跑** — `cd /d/code/ai-eval-cli-yt && node _verify_platform_client.js`,末行 `OK: PlatformClient ...`。(若报 form-data 缺失先 `npm i form-data`。)

- [ ] **Step 4: 删脚本** — `rm _verify_platform_client.js`

- [ ] **Step 5: 提交**
```bash
cd /d/code/ai-eval-cli-yt
git add src/platform-client.js package.json package-lock.json
git commit -m "feat(platform): PlatformClient 对接平台 eval-queue

拉 pending/claim/report/uploadTrace(multipart)。仿 qalab-runner api():Bearer+信封解封。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(注:此仓库 D:\code\ai-eval-cli-yt 独立 git;若非 git 仓库则跳过 commit,仅落文件——先 `git -C /d/code/ai-eval-cli-yt rev-parse --is-inside-work-tree` 判定。)

---

### Task 4: CLI WS 轨迹抓取 src/ws-trace.js

核心新能力:纯函数规整,可脱机验(构造帧序列)。

**Files:**
- Create: `D:\code\ai-eval-cli-yt\src\ws-trace.js`
- Verify(临时,删): `D:\code\ai-eval-cli-yt\_verify_ws_trace.js`

**Interfaces:**
- Produces: `attachWsTrace(page) -> collector`;`collector.buildTrace(runId?) -> {session_id,run_id,thinking,tool_calls[],artifacts[],answer,ws_captured}`;`collector.reset()`。内部纯函数 `handleFrame(state, frameObj)` 导出供测。

- [ ] **Step 1: 实现** — `D:\code\ai-eval-cli-yt\src\ws-trace.js`:
```javascript
// 抓 work.n.cn 对话 WebSocket 事件帧,规整成 trace(思考/工具·mcp 调用/产物)。
// 协议(见 openclaw360-web/src/ui/AGENTS.md):帧 {type:"event",event,payload,seq};
// event=="agent" 用 payload.stream 二次判别:thinking→data.text;tool→data.{name,originalToolName,phase,toolCallId,args,result}。
// nami_panel 是信封,内层在 payload.data,需展开一层。mcp 靠 originalToolName 的 mcp__<server>__<tool> 前缀。
// 同一 toolCallId 跨 start/update/result 多帧,按 id 聚合。

function _unwrapPanel(payload) {
  // nami_panel 信封:真正字段在 payload.data(内层可能又是 {stream,data,...})。展开一层。
  if (payload && payload.stream === 'nami_panel' && payload.data && typeof payload.data === 'object') {
    return payload.data;
  }
  return payload;
}

function _isMcp(originalToolName) {
  return typeof originalToolName === 'string' && originalToolName.startsWith('mcp__');
}
function _mcpServer(originalToolName) {
  if (!_isMcp(originalToolName)) return null;
  const parts = originalToolName.split('__'); // mcp__<server>__<tool>
  return parts.length >= 2 ? parts[1] : null;
}

// 初始状态
function newState() {
  return { sessionId: null, runId: null, thinking: '', toolsById: new Map(), toolOrder: [], answer: '', artifacts: [], sawAny: false };
}

// 处理一帧(已 JSON.parse 的对象)。纯函数式副作用在 state 上。异常安全由调用方包 try。
function handleFrame(state, frame) {
  if (!frame || frame.type !== 'event') return;
  let payload = _unwrapPanel(frame.payload || {});
  const event = frame.event;
  if (payload && payload.sessionId && !state.sessionId) state.sessionId = payload.sessionId;
  if (payload && payload.runId && !state.runId) state.runId = payload.runId;

  if (event === 'agent') {
    state.sawAny = true;
    const stream = payload.stream;
    const data = payload.data || {};
    if (stream === 'thinking') {
      const t = (data.text != null ? data.text : (data.data && data.data.text)) || '';
      if (t) state.thinking += t;
    } else if (stream === 'assistant') {
      const t = (data.text != null ? data.text : '') || '';
      if (t) state.answer += t;
    } else if (stream === 'tool') {
      const id = data.toolCallId || data.subToolCallId || `_anon_${state.toolOrder.length}`;
      let entry = state.toolsById.get(id);
      if (!entry) {
        entry = { tool_call_id: id, name: data.name || '', original_tool_name: data.originalToolName || data.name || '',
                  is_mcp: false, mcp_server: null, args: undefined, result_text: '', reached_result: false };
        state.toolsById.set(id, entry); state.toolOrder.push(id);
      }
      if (data.name) entry.name = data.name;
      if (data.originalToolName) entry.original_tool_name = data.originalToolName;
      entry.is_mcp = _isMcp(entry.original_tool_name);
      entry.mcp_server = _mcpServer(entry.original_tool_name);
      if (data.args !== undefined) entry.args = data.args;
      const r = data.result != null ? data.result : data.partialResult;
      if (r != null) entry.result_text = typeof r === 'string' ? r : JSON.stringify(r);
      if (data.phase === 'result') entry.reached_result = true;
    }
  }
}

// 挂到 page:监听 framereceived(收到的服务端帧即对话数据)。返回 collector。
function attachWsTrace(page) {
  const state = newState();
  const onWs = (ws) => {
    ws.on('framereceived', (ev) => {
      try {
        const payloadStr = typeof ev === 'string' ? ev : (ev && ev.payload);
        if (!payloadStr || typeof payloadStr !== 'string') return;
        if (payloadStr[0] !== '{') return; // 非 JSON 文本帧(如心跳)跳过
        handleFrame(state, JSON.parse(payloadStr));
      } catch (_) { /* 单帧解析失败不影响整体 */ }
    });
  };
  try { page.on('websocket', onWs); } catch (_) {}
  return {
    _state: state,
    reset() { const s = newState(); Object.assign(state, s); state.toolsById = s.toolsById; state.toolOrder = s.toolOrder; },
    buildTrace(runId) {
      const tool_calls = state.toolOrder.map(id => {
        const e = state.toolsById.get(id);
        return { tool_call_id: e.tool_call_id, name: e.name, original_tool_name: e.original_tool_name,
                 is_mcp: e.is_mcp, mcp_server: e.mcp_server, args: e.args, result_text: e.result_text, reached_result: e.reached_result };
      });
      return { session_id: state.sessionId, run_id: runId || state.runId,
               thinking: state.thinking, tool_calls, artifacts: state.artifacts,
               answer: state.answer, ws_captured: state.sawAny };
    },
  };
}

module.exports = { attachWsTrace, handleFrame, newState, _isMcp, _mcpServer };
```

- [ ] **Step 2: 验证脚本** — `_verify_ws_trace.js`(构造帧序列脱机验规整):
```javascript
const { handleFrame, newState, _isMcp } = require('./src/ws-trace');

const st = newState();
// 思考两帧累积
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'thinking', sessionId: 'S1', runId: 'R1', data: { text: '先分析' } } });
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'thinking', data: { text: '再规划' } } });
// 工具 start + result 聚合(mcp)
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'tool', data: { toolCallId: 'tc1', name: '网页搜索', originalToolName: 'mcp__serper__web_search', phase: 'start', args: { q: '天气' } } } });
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'tool', data: { toolCallId: 'tc1', phase: 'result', result: '晴' } } });
// 普通工具(非 mcp)
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'tool', data: { toolCallId: 'tc2', name: '读文件', originalToolName: 'read_file', phase: 'result', result: 'ok' } } });
// nami_panel 信封包思考
handleFrame(st, { type: 'event', event: 'agent', payload: { stream: 'nami_panel', data: { stream: 'assistant', data: { text: '最终答案' } } } });
// 非 event 帧忽略
handleFrame(st, { type: 'res', id: 9 });

const c = { _state: st };
const trace = require('./src/ws-trace').attachWsTrace({ on() {} }); // 拿 buildTrace,但用我们的 state
// 直接构造 buildTrace 等价:用内部 state
const out = (function build(state, runId){
  const tool_calls = state.toolOrder.map(id => { const e = state.toolsById.get(id); return { tool_call_id:e.tool_call_id, name:e.name, original_tool_name:e.original_tool_name, is_mcp:e.is_mcp, mcp_server:e.mcp_server, result_text:e.result_text, reached_result:e.reached_result }; });
  return { session_id: state.sessionId, run_id: runId||state.runId, thinking: state.thinking, tool_calls, answer: state.answer, ws_captured: state.sawAny };
})(st, 'RUN9');

const A = (c, m) => { if (!c) { console.error('FAIL:', m, JSON.stringify(out)); process.exit(1); } };
A(out.thinking === '先分析再规划', 'thinking 累积');
A(out.session_id === 'S1' && out.run_id === 'RUN9', 'session/run id');
A(out.tool_calls.length === 2, '工具聚合成2条');
A(out.tool_calls[0].is_mcp === true && out.tool_calls[0].mcp_server === 'serper', 'mcp 辨识');
A(out.tool_calls[0].reached_result === true && out.tool_calls[0].result_text === '晴', '同id跨帧聚合 start+result');
A(out.tool_calls[1].is_mcp === false, '普通工具非mcp');
A(out.answer === '最终答案', 'nami_panel 展开 assistant');
A(_isMcp('mcp__x__y') && !_isMcp('read_file'), '_isMcp');
console.log('OK: ws-trace 规整(思考累积/工具聚合/mcp辨识/nami_panel展开/非event忽略) 正常');
```

- [ ] **Step 3: 跑** — `cd /d/code/ai-eval-cli-yt && node _verify_ws_trace.js`,末行 `OK: ws-trace ...`。

- [ ] **Step 4: 删脚本** — `rm _verify_ws_trace.js`

- [ ] **Step 5: 提交**
```bash
cd /d/code/ai-eval-cli-yt
git add src/ws-trace.js
git commit -m "feat(platform): ws-trace 抓 work.n.cn WebSocket 帧规整会话轨迹

按协议规整思考/工具·mcp调用/产物:thinking累积、tool按toolCallId聚合多phase、
mcp靠originalToolName mcp__前缀辨识、nami_panel信封展开。纯函数可脱机验。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: CLI 平台模式编排

集成:拉平台→执行(desktop+WS抓)→回写。

**Files:**
- Modify: `D:\code\ai-eval-cli-yt\bin\ai-eval.js`(加 platform 子命令)
- Modify: `D:\code\ai-eval-cli-yt\src\desktop-pool.js`(挂 attachWsTrace)
- Modify: `D:\code\ai-eval-cli-yt\config\default.config.js` + `.env.example`(平台段)

**Interfaces:**
- Consumes: Task3 PlatformClient、Task4 attachWsTrace;现有 DesktopPool/DesktopRunner/DialogRunner。
- Produces: `node bin/ai-eval.js platform` 子命令。

- [ ] **Step 1: desktop-pool 挂 WS** — `src/desktop-pool.js`:顶部 require `const { attachWsTrace } = require('./ws-trace');`;在拿到 work.n.cn 主 page 处(约 :170,`page.on('dialog')` 附近 :180)加:
```javascript
    // 平台模式:抓会话 WebSocket 轨迹(思考/工具)。挂在主 page,收集器存到 pool 供编排取。
    this._wsTrace = attachWsTrace(page);
```
并加取用方法(class 内):
```javascript
  getWsTrace() { return this._wsTrace || null; }
```

- [ ] **Step 2: bin/ai-eval.js 加 platform 子命令** — 在 commander 定义区(参照 desktop 命令 :558)加:
```javascript
program
  .command('platform')
  .description('平台模式:从测试管理平台拉对话测评任务,执行并回写(需配 BASE_URL/RUNNER_TOKEN/RUNNER_ID)')
  .option('--limit <n>', '每轮拉取任务数', '5')
  .option('--once', '只跑一轮(默认常驻轮询)')
  .option('--exe <path>', '纳米Work 客户端 exe 路径(覆盖 config.desktop.exe)')
  .option('--cdp-port <port>', 'CDP 调试端口', '')
  .action(async (opts) => {
    const config = loadConfig();
    const PlatformClient = require('../src/platform-client');
    const DesktopPool = require('../src/desktop-pool');
    const DesktopRunner = require('../src/desktop-runner');
    const client = new PlatformClient(config.platform || {});
    const pollMs = (config.platform && config.platform.pollMs) || 5000;
    const runOnce = async () => {
      const pending = await client.fetchPending(parseInt(opts.limit, 10) || 5);
      if (!pending || !pending.length) { logger.info('平台无待执行任务'); return 0; }
      logger.info(`拉到 ${pending.length} 条待执行`);
      const pool = new DesktopPool(config.desktop, config.browser, logger, {
        exe: opts.exe, cdpPort: opts.cdpPort ? parseInt(opts.cdpPort, 10) : undefined });
      await pool.init();
      const wsTrace = pool.getWsTrace();
      const runner = new DesktopRunner(pool.getContext(), pool.getMainPage(), config.platform_platform_placeholder || config.platform, config.execution, logger);
      for (const item of pending) {
        try {
          await client.claim(item.run_id);
        } catch (e) { logger.warn(`claim ${item.run_id} 失败(可能被他机认领): ${e.message}`); continue; }
        const p = item.payload || {};
        const testCase = {
          caseId: `RUN-${item.run_id}`, row: item.run_id, question: p.prompt || '',
          attachments: p.attachments || [], attachmentPaths: [],
          conversationId: p.conversation_group || `__run_${item.run_id}`, turnIndex: p.turn_index || 0,
          account: 'desktop',
        };
        if (wsTrace && wsTrace.reset) wsTrace.reset();
        let result;
        try {
          result = await runner.runOne(testCase); // DesktopRunner 执行单条(见下 Step3 注)
        } catch (e) {
          result = { success: false, incomplete: true, completeReason: 'exception', answer: `[执行异常] ${e.message}` };
        }
        const trace = wsTrace ? wsTrace.buildTrace(item.run_id) : { ws_captured: false, tool_calls: [] };
        try {
          await client.report(item.run_id, {
            status: result.success ? 'done' : 'failed',
            share_link: result.shareLink || null, artifact_share_link: result.artifactShareLink || null,
            answer: result.answer || null, reported_duration: result.reportedDuration || null,
            bean_cost: result.beanCost || null, tokens: result.cost || null,
            session_id: trace.session_id || null,
            reason: result.success ? null : (result.completeReason || null),
            duration_ms: result.durationMs || null,
          });
          await client.uploadTrace(item.run_id, trace);
          logger.info(`✅ 回写 run ${item.run_id} (${result.success ? 'done' : 'failed'}, ws=${trace.ws_captured})`);
        } catch (e) { logger.error(`回写 run ${item.run_id} 失败: ${e.message}`); }
      }
      await pool.close();
      return pending.length;
    };
    if (opts.once) { await runOnce(); return; }
    logger.info('平台模式常驻轮询(Ctrl-C 退出)...');
    for (;;) { try { await runOnce(); } catch (e) { logger.error(`轮询异常: ${e.message}`); } await new Promise(r => setTimeout(r, pollMs)); }
  });
```
(注:上面 `config.platform_platform_placeholder` 是笔误占位——实现时删掉,DesktopRunner 第3参传 config.platform(平台页面选择器沿用 config.platform 段的 work.n.cn selector,即现有 platform 段)。**实现者注意**:现有 config 的 work.n.cn 选择器段就叫 `platform`,而"平台对接"配置我放在 `config.platform_api` 或 `config.platformApi`——避免与选择器段 `platform` 撞名。见 Step 4。故 DesktopRunner 第3参传选择器段 `config.platform`,PlatformClient 传 `config.platformApi`。修正:`new PlatformClient(config.platformApi || {})`、`new DesktopRunner(..., config.platform, ...)`。)

- [ ] **Step 2b: 修正命名撞车** — 承上:PlatformClient 用 `config.platformApi`(平台对接:baseUrl/token/runnerId/pollMs),DesktopRunner 用 `config.platform`(work.n.cn 选择器,现有)。把 Step2 action 里 `new PlatformClient(config.platform || {})` 改为 `new PlatformClient(config.platformApi || {})`;`config.platform_platform_placeholder || config.platform` 改为 `config.platform`。

- [ ] **Step 3: 确认 DesktopRunner 执行单条的方法** — 读 `src/desktop-runner.js`,确认执行单条对话的方法名与入参(现有编排 bin:683-688 怎么调它)。若现有是 `runBatch`/`runConcurrent` 而无 `runOne`,则在 platform action 里改用现有方法跑单条(传 [testCase]),取回第一条 result。**实现者据实际方法名对齐**(不要臆造 runOne;用 desktop-runner 已有的单条/批量入口,传一条、取一条结果)。若需要,给 DesktopRunner 加一个薄 `runOne(testCase)` 包装(内部调它已有的单条执行 + 返回 sendMessage 形状的 result)。

- [ ] **Step 4: config + .env** — `config/default.config.js` 加(与选择器段 platform 并列):
```javascript
  // 平台对接(测试管理平台 eval-queue):平台模式用。凭据优先环境变量。
  platformApi: {
    baseUrl: process.env.BASE_URL || '',
    token: process.env.RUNNER_TOKEN || '',
    runnerId: process.env.RUNNER_ID || 'mac-01',
    pollMs: 5000,
  },
```
`.env.example` 加:
```
# 平台模式(node bin/ai-eval.js platform):测试管理平台地址 + 设备 token(平台「我的设备」注册)
BASE_URL=
RUNNER_TOKEN=
RUNNER_ID=mac-01
```

- [ ] **Step 5: 冒烟(不连真客户端)** — 验 platform 子命令已注册、config 段在:
```bash
cd /d/code/ai-eval-cli-yt
node bin/ai-eval.js platform --help 2>&1 | head -5
node -e "const c=require('./config/default.config.js'); if(!('platformApi' in c)) {console.error('FAIL: config 缺 platformApi'); process.exit(1)} console.log('OK: platform 子命令 + config.platformApi 就位')"
```
Expected: `--help` 打出 platform 命令说明;末行 OK。
(真连客户端执行留到端到端环境;本步只验接线。)

- [ ] **Step 6: 提交**
```bash
cd /d/code/ai-eval-cli-yt
git add bin/ai-eval.js src/desktop-pool.js config/default.config.js .env.example
git commit -m "feat(platform): CLI 平台模式(拉 eval-queue→驱动对话+抓WS轨迹→回写)

新增 platform 子命令:PlatformClient 拉任务、DesktopPool 连客户端(挂 attachWsTrace)、
执行后 report+uploadTrace。config.platformApi 段(与选择器段 platform 分开)。飞书模式不变。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 前端最小下发入口

否则生成的 query 无从触发执行、链路不可端到端验。

**Files:**
- Modify: `frontend/src/api/index.js`(enqueueEvalQueries + 复用 listDevices)
- Modify: `frontend/src/views/AIEvalGen.vue`(结果表多选 + 下发按钮 + runner 选择)

**Interfaces:**
- Consumes: Task2 `POST /api/eval-queue/enqueue`;现有 `GET /api/devices`(我的设备)。
- Produces: 生成结果页可勾选 query → 选执行机 → 下发。

- [ ] **Step 1: api 封装** — `frontend/src/api/index.js` 加(参照现有导出风格):
```javascript
// 对话测评:下发选中的 query 到执行机(eval-queue)
export const enqueueEvalQueries = (payload) => http.post('/eval-queue/enqueue', payload)
// 我的执行设备(下发时选 runner);若已有 listDevices 复用之,勿重复定义
export const listMyDevices = () => http.get('/devices')
```
(注:先 grep `frontend/src/api/index.js` 是否已有 devices 封装——`api-env`/`devices` 可能已有;有则复用,不重复。http 实例名以文件实际为准。)

- [ ] **Step 2: AIEvalGen.vue 加下发** — 读现有 `frontend/src/views/AIEvalGen.vue` 结果表(queries 表格)。加:
  - `el-table` 开启多选列(`<el-table-column type="selection" />`)+ `@selection-change` 存 `selectedQueries`。
  - 结果卡片工具栏加:runner 选择 `el-select`(onMounted 调 listMyDevices 填 `devices`,option value=`runner_id`)+ "发送到执行机" 按钮(disabled 当 `!selectedQueries.length || !chosenRunner`)。
  - 按钮 handler:
```javascript
async function dispatchSelected() {
  if (!selectedQueries.value.length || !chosenRunner.value) return
  try {
    const res = await enqueueEvalQueries({
      project_id: projectId.value,
      runner: chosenRunner.value,
      target_engine: 'namiwork',
      eval_query_ids: selectedQueries.value.map(q => q.id),
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条到 ${chosenRunner.value}(批次 ${res.batch_id})`)
  } catch (e) { /* http 拦截器已提示 */ }
}
```
  完整实现参照 AIEvalGen 现有 script setup 风格(ref/import ElMessage/projectId 来源同页)。

- [ ] **Step 3: 构建验证** — `cd /d/code/test-management-platform/frontend && npm run build 2>&1 | tail -3`,须 `✓ built`。(无 node_modules 先 npm install。)

- [ ] **Step 4: 提交(仅源码,dist 收尾统一重建)**
```bash
cd /d/code/test-management-platform
git add frontend/src/api/index.js frontend/src/views/AIEvalGen.vue
git commit -m "feat(eval): 前端下发入口(勾选 query→选执行机→发送到 eval-queue)

AIEvalGen 结果表多选 + runner 选择(我的设备)+ 下发按钮,调 /api/eval-queue/enqueue。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage(对 spec §4-7,10):**
- §4 target_engine → Task 1 ✓
- §5 eval-queue 六端点 → Task 2 ✓
- §6.1 platform-client → Task 3 ✓
- §6.2 ws-trace → Task 4 ✓
- §6.3/6.4/6.5/6.6 平台模式编排+挂载+config+保留飞书 → Task 5 ✓
- §6.7 前端下发 → Task 6 ✓
- §7 迁移 → Task 1 ✓
- §3 决策3 trace 存磁盘 URL → Task 2 trace 端点 ✓

**2. Placeholder 扫描:** 后端 Task1/2 完整代码。CLI Task3/4 完整代码 + 脱机验。Task5 编排给了完整 action,但显式标注两处需实现者据实核对(DesktopRunner 单条方法名 Step3、命名撞车修正 Step2b)——这是"参照现有大文件集成"的诚实处理,非占位;给了明确的对齐指令与 fallback。Task6 前端参照现有 .vue 结构 + 给了 handler 完整代码。

**3. 类型一致性:** PlatformClient 方法名(fetchPending/claim/report/uploadTrace)Task3 定义 → Task5 调用一致;attachWsTrace/buildTrace(Task4)→ Task5 用一致;trace 结构(session_id/thinking/tool_calls/ws_captured)Task4 产 → Task2 端点存 → 子项3 将读一致;EvalReportIn 字段(Task2)↔ Task5 report body 一致;enqueueEvalQueries payload(Task6)↔ EvalEnqueueIn(Task2)一致。

**注**:Task5 是集成任务、依赖 CLI 现有 DesktopRunner/DesktopPool 的实际方法签名,实现时须先读这两个文件对齐(Step3 已明确)。这是本计划风险最高的任务,SDD 执行时给足上下文。

---

## Execution Handoff

计划已存 `docs/superpowers/plans/2026-08-22-eval-exec-dispatch.md`。
