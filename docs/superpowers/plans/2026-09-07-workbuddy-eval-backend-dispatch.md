# WorkBuddy 对话测评 · 后端多产品派单 Implementation Plan（系列 2/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让测评任务能"任务级勾选多个被测产品"，一次下发对每题按勾选产品各 fan-out 一条 run（与 A/B 维度正交），并按 `target_engine` 把 run 派到声明支持该产品的执行机（分机跑）。

**Architecture:** 复用现有 A/B `variants` 展开模式（`dispatch_task_runs`）——把"产品(engine)"作为与 A/B 正交的新维度叠加进 fan-out。数据模型仅加两列（`EvalTask.target_engines` 存勾选的产品、`RunnerDevice.eval_engine` 声明机器支持的产品），判定/回收链路因 `eval_run.target_engine` 早已存在且透传而零改动。挑机在现有 `online_eval_runners` 上加 engine 过滤。

**Tech Stack:** FastAPI + SQLAlchemy 2.0；统一响应信封 `{code,msg,data}`；`Text` 存 JSON（兼容 MySQL 5.6）；手写 `_to_out` 序列化；手写 `migrate.py::ensure_*` 增量加列。

**Spec:** `docs/superpowers/specs/2026-09-07-eval-multi-product-workbuddy-design.md`（§4 数据模型、§6 派单挑机）

## Global Constraints

- **三处 schema 手动同步**（CLAUDE.md 铁律）:加列必须同时改 ①`app/models/*.py` 模型 ②`backend/sql/schema.sql` ③`app/db/migrate.py::ensure_*` + `app/main.py` 挂载（import 列表 + init_db 体内调用）。漏一处老库不更新。
- **响应信封**:所有端点用 `app/schemas/common.py` 的 `ok(data)`/`fail`；业务错误 `raise HTTPException(status, detail="中文提示")`。
- **不建统计表**:多产品统计沿用现算聚合（与 `/stats` 口径一致）。
- **向后兼容**:`target_engines` 为空/NULL → 退化为仅 `namiwork`（现有行为）；`RunnerDevice.eval_engine` 为空 → 视作支持 `namiwork`（老机不受影响）。
- **引擎取值**:合法引擎集中在 `app/services/eval_engines.py::EVAL_ENGINES`（`namiwork`/`workbuddy`）。所有校验以它为准，不散落硬编码。
- **两条下发路径都要改**:手动 `run_task`（`eval_task.py:368`）+ 定时 `run_eval_task_job`（`scheduler.py:127`，当前硬编码 `"namiwork"`）。
- **后端自测**:用 `backend/.venv` 的 `python -m scripts.xxx`（系统 python 无依赖）。本仓库无 pytest，验证=一次性脚本插数据跑函数断言。

---

## 文件结构

| 文件 | 责任 | 创建/修改 |
|---|---|---|
| `backend/app/services/eval_engines.py` | 被测产品注册表 `EVAL_ENGINES` + 校验辅助 `normalize_engines()`/`is_valid_engine()` | 创建 |
| `backend/app/api/ai_eval.py` | 新增 `GET /api/ai/eval-engines`（下发产品列表给前端，仿 eval-dimensions） | 修改 |
| `backend/app/models/ai_eval.py` | `EvalTask.target_engines` 新列（Text-JSON） | 修改 |
| `backend/app/models/runner_device.py` | `RunnerDevice.eval_engine` 新列（String，NULL=namiwork） | 修改 |
| `backend/app/db/migrate.py` | `ensure_eval_task_target_engines()` + `ensure_runner_device_eval_engine()` | 修改 |
| `backend/app/main.py` | import + init_db 挂载两个 ensure_* | 修改 |
| `backend/sql/schema.sql` | eval_task 加列 + runner_device 加列 | 修改 |
| `backend/app/services/dispatcher.py` | `online_eval_runners(db, engine=None)` 加 engine 过滤 | 修改 |
| `backend/app/api/eval_task.py` | `dispatch_task_runs` 加产品维度 fan-out + 按 engine 分机；`EvalTaskRunIn`/`EvalTaskCreateIn`/`EvalTaskUpdateIn` 收 target_engines；下发前校验在线机；`_to_out` 回显 | 修改 |
| `backend/app/services/scheduler.py` | `run_eval_task_job` 用 `task.target_engines` 而非硬编码 namiwork | 修改 |
| `backend/app/api/devices.py`（或 runner 心跳处） | 执行机上报心跳时记 `eval_engine` | 修改 |

---

## Task 1: 被测产品注册表 eval_engines.py + GET /eval-engines

**Files:**
- Create: `backend/app/services/eval_engines.py`
- Modify: `backend/app/api/ai_eval.py`（加端点）
- Test: `backend/scripts/test_eval_engines.py`

**Interfaces:**
- Produces:
  - `EVAL_ENGINES: dict[str, dict]` —— `{"namiwork": {"label","needs_device","device_kind"}, "workbuddy": {...}}`
  - `is_valid_engine(engine: str) -> bool`
  - `normalize_engines(engines: list[str] | None) -> list[str]` —— 去重、过滤非法、空则返回 `["namiwork"]`
  - `GET /api/ai/eval-engines` → `ok([{engine, label, needs_device, device_kind}, ...])`

- [ ] **Step 1: 写注册表 + 校验辅助**

```python
# backend/app/services/eval_engines.py
"""被测产品(引擎)注册表:多产品横评的合法引擎集中定义,校验以此为准。

与 eval_run.target_engine / eval_task.target_engines 的取值对齐。加新产品在此加一项 +
CLI 侧加执行器 + 一台声明该引擎的执行机即可,判定/统计/对比零改动(见 spec §12)。
"""

EVAL_ENGINES: dict[str, dict] = {
    "namiwork":  {"label": "纳米Work",  "needs_device": True,  "device_kind": "desktop"},
    "workbuddy": {"label": "WorkBuddy", "needs_device": False, "device_kind": "desktop"},
}

DEFAULT_ENGINE = "namiwork"


def is_valid_engine(engine: str) -> bool:
    return engine in EVAL_ENGINES


def normalize_engines(engines: list[str] | None) -> list[str]:
    """去重、剔非法、保序;空/全非法 → [DEFAULT_ENGINE](向后兼容)。"""
    if not engines:
        return [DEFAULT_ENGINE]
    out = list(dict.fromkeys(e.strip() for e in engines if e and e.strip() and is_valid_engine(e.strip())))
    return out or [DEFAULT_ENGINE]
```

- [ ] **Step 2: 写测试**

```python
# backend/scripts/test_eval_engines.py
from app.services.eval_engines import EVAL_ENGINES, is_valid_engine, normalize_engines

assert is_valid_engine("workbuddy") and is_valid_engine("namiwork")
assert not is_valid_engine("gpt")
assert normalize_engines(None) == ["namiwork"]
assert normalize_engines([]) == ["namiwork"]
assert normalize_engines(["workbuddy", "namiwork", "workbuddy"]) == ["workbuddy", "namiwork"]  # 去重保序
assert normalize_engines(["gpt", "workbuddy"]) == ["workbuddy"]                                 # 剔非法
assert normalize_engines(["gpt"]) == ["namiwork"]                                               # 全非法→默认
print("PASS: eval_engines")
```

- [ ] **Step 3: 跑测试确认失败→建文件→通过**

Run: `cd backend && .venv/bin/python -m scripts.test_eval_engines`
先 FAIL（模块不存在）→ 建 Step 1 文件 → 再 PASS。

- [ ] **Step 4: 加 GET /eval-engines 端点**

在 `backend/app/api/ai_eval.py` 的 `list_eval_dimensions`（约 213 行）附近仿写:

```python
@router.get("/eval-engines")
def list_eval_engines(user: User = Depends(get_current_user)):
    """下发被测产品(引擎)列表,供前端任务编辑时勾选。仿 eval-dimensions。"""
    from app.services.eval_engines import EVAL_ENGINES
    return ok([{"engine": k, **v} for k, v in EVAL_ENGINES.items()])
```

- [ ] **Step 5: 验证端点**

Run: `cd backend && .venv/bin/python -m scripts.test_eval_engines_api`（写一个用 TestClient 或直接调函数的小脚本，断言返回含 workbuddy）。或启动 uvicorn `curl localhost:8000/api/ai/eval-engines` 断言 code=0、data 含两引擎。

```python
# backend/scripts/test_eval_engines_api.py
from app.api.ai_eval import list_eval_engines
r = list_eval_engines(user=None)  # 若 Depends 挡住则改用 TestClient；此处直接调函数体
assert r["code"] == 0 and any(e["engine"] == "workbuddy" for e in r["data"])
print("PASS: eval-engines endpoint")
```

> 注:若 `list_eval_engines` 的 `Depends(get_current_user)` 使直接调用不便，改用 `from fastapi.testclient import TestClient` + `app.main.app` 带伪 token；或把注册表逻辑单测（Step 2 已覆盖），端点仅冒烟。二选一，不强求。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/eval_engines.py backend/app/api/ai_eval.py backend/scripts/test_eval_engines*.py
git commit -m "feat(eval): 被测产品注册表 + GET /eval-engines"
```

---

## Task 2: 数据模型加两列（EvalTask.target_engines + RunnerDevice.eval_engine）

**Files:**
- Modify: `backend/app/models/ai_eval.py`（EvalTask 加列）、`backend/app/models/runner_device.py`（RunnerDevice 加列）
- Modify: `backend/app/db/migrate.py`（两个 ensure_*）、`backend/app/main.py`（挂载）
- Modify: `backend/sql/schema.sql`（两表加列）
- Test: `backend/scripts/test_multi_engine_columns.py`

**Interfaces:**
- Produces: `EvalTask.target_engines: Mapped[str | None]`（Text-JSON 数组）；`RunnerDevice.eval_engine: Mapped[str | None]`（String(32)，NULL=namiwork）。

- [ ] **Step 1: EvalTask 加列**

`backend/app/models/ai_eval.py` 的 `EvalTask` 类内（`dialog_options` 列附近）加:

```python
    # 任务级勾选的被测产品集合 JSON 数组(如 ["namiwork","workbuddy"]);NULL/空=仅 namiwork(向后兼容)。
    # 执行/定时回归时对每题按此集合各 fan-out 一条 run(与 A/B 正交)。
    target_engines: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: RunnerDevice 加列**

`backend/app/models/runner_device.py` 的 `RunnerDevice` 类内（`platform` 列附近）加:

```python
    # 该执行机支持的被测引擎(测评分机跑):如 'namiwork' / 'workbuddy'。NULL=兼容老机,视作 namiwork。
    # 挑机时 online_eval_runners(engine=X) 只返回声明支持 X 的在线机。
    eval_engine: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
```

- [ ] **Step 3: 两个 migrate ensure_***

`backend/app/db/migrate.py` 仿 `ensure_eval_run_target_engine`（L532）加:

```python
def ensure_eval_task_target_engines() -> None:
    """eval_task 补 target_engines 列(任务级勾选的被测产品)。老库 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_task"):
        return
    if "target_engines" not in _columns("eval_task"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN target_engines TEXT NULL"))


def ensure_runner_device_eval_engine() -> None:
    """runner_device 补 eval_engine 列(该机支持哪个被测引擎)。NULL=兼容老机视作 namiwork。"""
    if not _columns("runner_device"):
        return
    if "eval_engine" not in _columns("runner_device"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE runner_device ADD COLUMN eval_engine VARCHAR(32) NULL"))
    _ensure_index("runner_device", "idx_runnerdev_eval_engine", ["eval_engine"])
```

- [ ] **Step 4: main.py 挂载**

`backend/app/main.py`:①L15 附近 import 列表加 `ensure_eval_task_target_engines, ensure_runner_device_eval_engine`；②init_db 体内（其他 ensure_* 之后）加两行调用。

- [ ] **Step 5: schema.sql 同步**

`backend/sql/schema.sql`:eval_task 段（约 L401 dialog_options 后）加 `target_engines TEXT NULL`；runner_device 段（约 L448 platform 后）加 `eval_engine VARCHAR(32) DEFAULT NULL` + 段末索引 `KEY idx_runnerdev_eval_engine (eval_engine)`。

- [ ] **Step 6: 验证建表/加列 + 读写**

```python
# backend/scripts/test_multi_engine_columns.py
from app.db.session import SessionLocal, engine
from app.db.migrate import ensure_eval_task_target_engines, ensure_runner_device_eval_engine
from app.db.migrate import _columns
ensure_eval_task_target_engines(); ensure_runner_device_eval_engine()
assert "target_engines" in _columns("eval_task"), "eval_task.target_engines 未建"
assert "eval_engine" in _columns("runner_device"), "runner_device.eval_engine 未建"
print("PASS: 两列已建")
```

Run: `cd backend && .venv/bin/python -m scripts.test_multi_engine_columns`
Expected: PASS（幂等，重跑不报错）。

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/ai_eval.py backend/app/models/runner_device.py backend/app/db/migrate.py backend/app/main.py backend/sql/schema.sql backend/scripts/test_multi_engine_columns.py
git commit -m "feat(eval): EvalTask.target_engines + RunnerDevice.eval_engine 两列(多产品派单地基)"
```

---

## Task 3: 挑机按 engine 过滤（online_eval_runners）

**Files:**
- Modify: `backend/app/services/dispatcher.py`（`online_eval_runners` 加参数）
- Test: `backend/scripts/test_online_eval_by_engine.py`

**Interfaces:**
- Consumes: `RunnerDevice.eval_engine`（Task 2）、`EVAL_ENGINES`（Task 1）。
- Produces: `online_eval_runners(db, engine: str | None = None) -> list[str]` —— engine 非空时只返回 `eval_engine==engine`（NULL 视作 namiwork）的在线机；engine 为空时保持原行为（全部在线 eval 机）。

- [ ] **Step 1: 改 online_eval_runners（保持无参调用向后兼容）**

`backend/app/services/dispatcher.py` L115:

```python
def online_eval_runners(db: Session, engine: str | None = None) -> list[str]:
    from app.api.devices import ONLINE_WINDOW_SEC
    devices = db.query(RunnerDevice).all()
    if not devices:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    exec_running = _exec_running_runners(db)
    eval_running = _eval_running_runners(db)

    def _supports(d) -> bool:
        if engine is None:
            return True
        dev_engine = (d.eval_engine or "namiwork")   # NULL 兼容老机=namiwork
        return dev_engine == engine

    online = [d.runner_id for d in devices
              if current_kind(d, cutoff, exec_running, eval_running) == "eval" and _supports(d)]
    return sorted(set(online))
```

- [ ] **Step 2: 写测试（构造两台机，各声明不同引擎）**

```python
# backend/scripts/test_online_eval_by_engine.py
# 构造：伪造两台 RunnerDevice（eval_engine=namiwork / workbuddy），刷 last_eval_at 使其"在线在跑 eval"，
# 断言 online_eval_runners(engine='workbuddy') 只返回 workbuddy 机。
from datetime import datetime
from app.db.session import SessionLocal
from app.models.runner_device import RunnerDevice
from app.services.dispatcher import online_eval_runners
db = SessionLocal()
# 用真实 owner_id（取任一 user）；测试后回滚，不污染库
from app.models.user import User
uid = db.query(User.id).first()[0]
now = datetime.utcnow()
d1 = RunnerDevice(owner_id=uid, runner_id="test-nami-01", name="nami机", token="tk-nami-test", eval_engine="namiwork", last_eval_at=now, last_seen_at=now)
d2 = RunnerDevice(owner_id=uid, runner_id="test-wb-01", name="wb机", token="tk-wb-test", eval_engine="workbuddy", last_eval_at=now, last_seen_at=now)
db.add_all([d1, d2]); db.flush()
try:
    wb = online_eval_runners(db, engine="workbuddy")
    nami = online_eval_runners(db, engine="namiwork")
    alle = online_eval_runners(db)
    assert "test-wb-01" in wb and "test-nami-01" not in wb, f"workbuddy 过滤错: {wb}"
    assert "test-nami-01" in nami and "test-wb-01" not in nami, f"namiwork 过滤错: {nami}"
    assert "test-wb-01" in alle and "test-nami-01" in alle, f"无参应全返回: {alle}"
    print("PASS: online_eval_runners 按 engine 过滤")
finally:
    db.rollback(); db.close()
```

- [ ] **Step 3: 跑测试**

Run: `cd backend && .venv/bin/python -m scripts.test_online_eval_by_engine`
Expected: PASS。（依赖 Task 2 的列已建；用 rollback 不留脏数据。）

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/dispatcher.py backend/scripts/test_online_eval_by_engine.py
git commit -m "feat(eval): online_eval_runners 支持按被测引擎过滤在线机"
```

---

## Task 4: dispatch_task_runs 加产品维度 fan-out + 按 engine 分机

**Files:**
- Modify: `backend/app/api/eval_task.py`（`dispatch_task_runs` 主改；`EvalTaskRunIn`/`EvalTaskCreateIn`/`EvalTaskUpdateIn` 加字段；`_to_out` 回显；下发前校验）
- Test: `backend/scripts/test_dispatch_multi_engine.py`

**Interfaces:**
- Consumes: `normalize_engines`（Task 1）、`online_eval_runners(db, engine)`（Task 3）、`EvalTask.target_engines`（Task 2）。
- Produces: `dispatch_task_runs` 新签名 —— `target_engine` 参数改为 `target_engines: list[str]`；每题对每个 engine × 每个 A/B variant 各建一条 run，run.target_engine 落对应值，按 engine 分别挑机，会话组 key 带 engine 前缀。

- [ ] **Step 1: EvalTaskRunIn 加 target_engines（保留旧 target_engine 兼容）**

`backend/app/api/eval_task.py` L195:

```python
class EvalTaskRunIn(BaseModel):
    runner: str | None = Field(None, max_length=64)
    runners: list[str] | None = Field(None, max_length=32)
    target_engine: str = Field("namiwork", max_length=32)     # 保留:单产品旧调用
    target_engines: list[str] | None = None                    # 新:多产品勾选;非空则优先
    target_device: str | None = Field(None, max_length=64)
    dialog_options: dict | None = None
    dialog_options_b: dict | None = None
    auto_pipeline: bool | None = None
```

- [ ] **Step 2: EvalTaskCreateIn/UpdateIn 加 target_engines（任务级持久化）**

L123/L130:

```python
class EvalTaskCreateIn(BaseModel):
    project_id: int
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    query_ids: list[int] = Field(default_factory=list)
    target_engines: list[str] | None = None    # 勾选的被测产品;空=仅 namiwork

class EvalTaskUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    description: str | None = None
    query_ids: list[int] | None = None
    target_engines: list[str] | None = None
```

同步:create/update 端点把 `target_engines` 存进 `task.target_engines`（`json.dumps(normalize_engines(body.target_engines))`），`_to_out(task)` 回显（`json.loads`）。

- [ ] **Step 3: 改 dispatch_task_runs 签名与 fan-out（核心）**

把 `target_engine: str` 换成 `target_engines: list[str]`，fan-out 三重循环（engine × variant × query），group_key 带 engine 前缀，按 engine 分机:

```python
def dispatch_task_runs(db: Session, task: EvalTask, runner, target_engines: list[str],
                       target_device: str | None, opts: dict, opts_b: dict | None,
                       user_id: int | None) -> tuple[list[int], str]:
    from app.api.eval_queue import _new_batch_id, _payload_of
    from app.core.enums import EvalDeviceKind
    from app.services.eval_engines import EVAL_ENGINES, normalize_engines
    from app.services.dispatcher import online_eval_runners

    engines = normalize_engines(target_engines)

    # 按 engine 解析候选执行机:auto→online_eval_runners(engine);显式指定→沿用 runner_list（不按 engine 拆，
    # 由调用方保证指定机匹配）。多产品 auto 时每 engine 独立挑机（分机跑）。
    qids = json.loads(task.query_ids) if task.query_ids else []
    if not qids:
        raise ValueError("任务内还没有用例,先添加用例再执行")
    qs = db.query(EvalQuery).filter(EvalQuery.id.in_(qids)).all()
    found = {q.id: q for q in qs}
    missing = [qid for qid in qids if qid not in found]
    if missing:
        raise ValueError(f"用例 {missing} 已不存在,请编辑任务移除")

    # 每 engine 的候选机:runner=="auto" → 按 engine 挑在线机;否则用显式 runner_list（须每台校验能力）。
    def _runners_for(engine: str) -> list[str]:
        if isinstance(runner, list):
            rl = runner
        elif runner == AUTO_RUNNER:
            rl = online_eval_runners(db, engine=engine)
            if not rl:
                raise ValueError(f"引擎「{EVAL_ENGINES.get(engine,{}).get('label',engine)}」当前无在线执行机")
            return rl
        else:
            rl = _resolve_runners(db, runner, None)
        for rid in rl:
            _check_eval_capability(db, rid)
        return rl

    batch_id = _new_batch_id()
    created = []
    variants = [("A", opts), ("B", opts_b)] if opts_b is not None else [(None, opts)]

    # planned 带 engine 维度;group_key 前缀 engine,避免不同产品同名组被误判同机连发。
    planned: list[tuple[str, str, dict, object]] = []   # [(engine, group_key, payload, q)]
    group_weight: dict[str, dict[str, int]] = {}        # engine -> {group_key: run 数}
    group_order: dict[str, list[str]] = {}              # engine -> group_key 出现顺序

    for engine in engines:
        group_weight.setdefault(engine, {}); group_order.setdefault(engine, [])
        for qid in qids:
            q = found[qid]
            for tag, vopts in variants:
                payload = _payload_of(q, vopts)
                if tag:
                    payload["compare_group"] = tag
                    if payload.get("conversation_group"):
                        payload["conversation_group"] = f"{payload['conversation_group']}#{tag}"
                base_group = payload.get("conversation_group") or f"q{qid}#{tag or ''}"
                group_key = f"{engine}::{base_group}"     # engine 前缀:跨产品同名组隔离
                planned.append((engine, group_key, payload, q))
                if group_key not in group_weight[engine]:
                    group_weight[engine][group_key] = 0
                    group_order[engine].append(group_key)
                group_weight[engine][group_key] += 1

    # 每 engine 独立按其候选机 LPT 分配
    group_runner: dict[str, str] = {}
    for engine in engines:
        rl = _runners_for(engine)
        gr = assign_groups_balanced([(gk, group_weight[engine][gk]) for gk in group_order[engine]], rl)
        group_runner.update(gr)

    for engine, group_key, payload, q in planned:
        assigned = group_runner[group_key]
        row = EvalRun(
            eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
            eval_task_id=task.id,
            runner=assigned, target_engine=engine,
            target_device=target_device,
            device_kind=EvalDeviceKind.desktop,
            status=EvalRunStatus.pending,
            payload=json.dumps(payload, ensure_ascii=False),
            enqueued_by=user_id,
        )
        db.add(row); db.flush(); created.append(row.id)
    task.last_batch_id = batch_id
    task.status = EvalTaskStatus.running
    task.summary_status = None
    task.pipeline_status = None
    return created, batch_id
```

> **注意**:`_check_eval_capability` 对显式指定机仍拦 func 冲突；auto 路径每 engine 各自 `online_eval_runners(engine)` 已过滤。会话组 key 从 `base_group` 变为 `engine::base_group`——CLI 侧 `groupIntoConversations` 按 `conversation_group`（payload 内，未加 engine 前缀）分组，故 **payload 里的 conversation_group 不变**，engine 前缀只用于 `assign_groups_balanced` 的分机 key，不写进 payload（同一 engine 内同组仍同机，跨 engine 天然不同机）。

- [ ] **Step 4: 改 run_task 调用点 + 下发前校验**

L385 调用改传 `engines`；下发前校验每个勾选 engine 有在线机（auto 时）:

```python
    engines = normalize_engines(body.target_engines if body.target_engines is not None else [body.target_engine])
    # auto 下发前校验每个 engine 有在线机(否则建一堆永不执行的 pending)
    if (body.runner or "").strip() == AUTO_RUNNER and not body.runners:
        from app.services.dispatcher import online_eval_runners
        for e in engines:
            if not online_eval_runners(db, engine=e):
                raise HTTPException(400, detail=f"引擎「{EVAL_ENGINES[e]['label']}」当前无在线执行机,无法下发")
    created, batch_id = dispatch_task_runs(db, task, runner_list, engines, body.target_device, opts, opts_b, user.id)
```

- [ ] **Step 5: 写 dispatch 测试（构造多引擎任务，断言 fan-out）**

```python
# backend/scripts/test_dispatch_multi_engine.py
# 构造一个含 2 题的 task + target_engines=[namiwork,workbuddy]，显式 runner 列表（跳过 auto 挑机），
# 跑 dispatch_task_runs，断言：生成 2题×2引擎=4 条 run，每引擎各 2 条，target_engine 落对。
import json
from app.db.session import SessionLocal
from app.models.ai_eval import EvalTask, EvalQuery, EvalRun
from app.api.eval_task import dispatch_task_runs
db = SessionLocal()
# ... 构造 project/2 个 eval_query/task（略，用现有项目 id）...
# 关键断言：
#   runs = db.query(EvalRun).filter(EvalRun.batch_id==batch_id).all()
#   assert len(runs) == 4
#   assert sorted(r.target_engine for r in runs) == ['namiwork','namiwork','workbuddy','workbuddy']
# 测试末 db.rollback() 不落库
print("PASS: dispatch 多引擎 fan-out")  # 实现时补全构造逻辑
```

> **实现注意**:此脚本需构造真实 project + eval_query（可复用库里已有的，或插入后 rollback）。构造逻辑按现有 `scripts/` 里其他 eval 测试脚本的模式补全（查 `backend/scripts/` 有无 eval 相关样板）。

- [ ] **Step 6: 跑测试**

Run: `cd backend && .venv/bin/python -m scripts.test_dispatch_multi_engine`
Expected: PASS —— 4 条 run，两引擎各 2 条。

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/eval_task.py backend/scripts/test_dispatch_multi_engine.py
git commit -m "feat(eval): dispatch_task_runs 多产品 fan-out + 按引擎分机挑机"
```

---

## Task 5: 定时 job + enqueue 直发支持多引擎

**Files:**
- Modify: `backend/app/services/scheduler.py`（`run_eval_task_job` 用 task.target_engines）
- Modify: `backend/app/api/eval_queue.py`（`enqueue` 支持 target_engines，可选）
- Test: `backend/scripts/test_scheduler_multi_engine.py`（或并入 Task 4 脚本）

**Interfaces:**
- Consumes: `EvalTask.target_engines`、改后的 `dispatch_task_runs`。

- [ ] **Step 1: 定时 job 读 task.target_engines**

`backend/app/services/scheduler.py` L160-162:

```python
    from app.services.eval_engines import normalize_engines
    engines = normalize_engines(json.loads(task.target_engines) if task.target_engines else None)
    created, batch_id = dispatch_task_runs(
        db, task, task.schedule_runner, engines, None, opts, opts_b, None)
```

- [ ] **Step 2: enqueue 直发支持 target_engines（可选，保持与任务级一致）**

`EvalEnqueueIn`（`schemas/eval_queue.py`）加 `target_engines: list[str] | None = None`；`enqueue` 若非空则对每 engine 建 run（简化版 fan-out，直发无 A/B）。若嫌复杂可跳过，直发仍走单 `target_engine`（YAGNI，任务级已覆盖对比场景）。**决策:本 plan 跳过 enqueue 多引擎，仅注释说明任务级是多产品入口。**

- [ ] **Step 3: 验证定时 job 路径**

Run: `cd backend && .venv/bin/python -m scripts.test_scheduler_multi_engine`（构造带 target_engines + schedule 的 task，调 `run_eval_task_job`，断言生成多引擎 run）。或人工核对 scheduler.py 改动正确 + Task 4 已覆盖 dispatch 核心。

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/scheduler.py backend/scripts/test_scheduler_multi_engine.py
git commit -m "feat(eval): 定时回归 job 支持任务级多产品(target_engines)"
```

---

## Task 6: 执行机心跳记录 eval_engine + _to_out 回显

**Files:**
- Modify: 执行机心跳/上报处（`backend/app/services/dispatcher.py::touch_runner_heartbeat` 或 `list_pending` 的设备 token 分支），让 runner 上报时能设 `eval_engine`
- Modify: `backend/app/api/eval_task.py::_to_out`（任务回显 target_engines）
- Test: 复用现有验证

**Interfaces:**
- Consumes: CLI 侧 `.env` 的 `EVAL_ENGINE`（Plan 1 已提及），随心跳上报。

- [ ] **Step 1: 心跳携带 eval_engine**

CLI 拉 `list_pending` 时可带 `?engine=workbuddy`；`list_pending`（`eval_queue.py:171`）在设备 token 分支把 `ctx.device.eval_engine = engine`（若传了且合法）。或更简：runner 注册/心跳端点加 eval_engine 字段落库。**决策:在 `list_pending` 加可选 query `engine`，设备 token 时落 `ctx.device.eval_engine`**（最小改动，随轮询自然刷新）:

```python
def list_pending(runner: str = Query("mac-01"), limit: int = Query(5, le=20),
                 engine: str | None = Query(None),
                 db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    if ctx.device is not None:
        runner = ctx.device.runner_id
        now = datetime.utcnow()
        ctx.device.last_seen_at = now
        ctx.device.last_eval_at = now
        if engine:
            from app.services.eval_engines import is_valid_engine
            if is_valid_engine(engine):
                ctx.device.eval_engine = engine    # 随轮询上报本机被测引擎
        db.commit()
    ...
```

- [ ] **Step 2: _to_out 回显 target_engines**

`backend/app/api/eval_task.py` 的任务 `_to_out`（约 L82-100）加:

```python
        "target_engines": json.loads(task.target_engines) if task.target_engines else [],
```

- [ ] **Step 3: CLI 侧上报 engine（Plan 1 衔接）**

在 `tools/qalab-runner/eval/src/platform-client.js` 的 fetchPending 请求加 `?engine=<EVAL_ENGINE>`（从 `.env` 读；WorkBuddy 机配 `EVAL_ENGINE=workbuddy`）。**此步跨到 Plan 1 的 CLI，记为衔接项**，在 Plan 1 分支上补。

- [ ] **Step 4: 验证 + Commit**

Run: 启动 uvicorn，用带 engine 的 list_pending 调用（伪 runner token）断言 device.eval_engine 落库；或核对代码。

```bash
git add backend/app/api/eval_queue.py backend/app/api/eval_task.py
git commit -m "feat(eval): 心跳上报 eval_engine + 任务回显 target_engines"
```

---

## Self-Review 记录

- **Spec 覆盖**:对应 spec §4.1/§4.2/§4.3（target_engine 启用/EvalTask.target_engines/引擎注册表 = Task 1/2）、§6.1（runner_device.eval_engine + online_eval_runners 按 engine = Task 2/3）、§6.2（dispatch fan-out 加产品维度 = Task 4）、§6.3（下发前校验在线机 = Task 4 Step 4）。§6.2 会话组隔离用 `engine::` 前缀作分机 key（不写进 payload，避免破坏 CLI 的 conversation_group 分组）。
- **占位符扫描**:Task 4 Step 5、Task 5 Step 3 的测试脚本构造逻辑标注"实现时补全"——因需真实 project/eval_query，构造依赖库现状，留给实现时按 `backend/scripts/` 现有样板补；断言逻辑已明确。非遗漏，是数据构造的合理延迟。
- **类型一致**:`dispatch_task_runs` 签名从 `target_engine: str` 改为 `target_engines: list[str]`——**两处调用点（run_task L385、scheduler L160）都在本 plan 同步改**（Task 4 Step 4、Task 5 Step 1），无遗漏调用旧签名。`normalize_engines`/`online_eval_runners(engine=)`/`is_valid_engine` 跨 Task 1/3/4/5/6 签名一致。
- **向后兼容检查**:`online_eval_runners(db)` 无参调用（现有代码 `_resolve_runners` L221 用）保持原行为（engine=None 全返回）；`target_engines` 空→[namiwork]；`eval_engine` NULL→namiwork。老库/老机/老调用不受影响。
