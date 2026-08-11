# 测试点回流 Task 验收清单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把采纳的 AI 测试点回流成 Task 的验收清单，成员逐条勾"通过/失败/阻塞"，失败一键转遗留问题，串起"生成→采纳→执行→遗留"闭环。

**Architecture:** 新增 `checklist_item` 表（task↔test_case + 三态执行状态）；放宽 `remaining_issue`（report_id 可空 + 加 task_id/checklist_item_id 两列）让遗留问题可直挂任务；新增独立 router `app/api/checklist.py` 承载清单增删查改与失败转遗留；采纳测试点的现有 PATCH 增强为副作用式 upsert 清单项；`/stats/daily` 与 `/stats/overview` 的 `open_issues` 兼容 report + task 两条来源并按 id 去重；前端验收清单 UI 挂 `MyReports.vue`（成员视角）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（后端），Vue3 + ElementPlus + Pinia（前端）。无测试框架——验证是手动端到端（curl + 浏览器）。

## Global Constraints

- 统一响应信封：所有后端接口用 `app/schemas/common.py` 的 `ok(data)` 返回，`code==0` 表示成功；前端 `api/http.js` 拦截器已解包，`api/index.js` 函数返回值即 `data` 本身。
- 不用 `response_model`：每个 router 内手写 `_to_out(db, obj) -> dict`（枚举取 `.value`、日期 `.isoformat()`、关联名用 `_user_name(db, uid)` 之类辅助函数补）。
- 枚举集中在 `app/core/enums.py`（不放 models，避免 api/deps 循环导入）。
- 权限：路径参数 `{pid}`/`{tid}` 来源用 `assert_project_role(db, user, project_id, roles)`（非注入版，因 project_id 需先查对象拿到）；member/admin 可写，guest 只读。
- 两份 schema 手动同步：SQLAlchemy 模型（`app/models/`，`create_all` 用）+ `backend/sql/schema.sql`（MySQL 初始化用）。新表两处都要建。
- 新模型必须在 `app/models/__init__.py` 汇总导入，`create_all` 才能建全表。
- 增量改列走 `app/db/migrate.py` 手写 `ALTER TABLE`（参照 `ensure_task_columns`/`ensure_testcase_columns`：先 `_columns(table)` 探列，`if not cols: return` 守卫，`with engine.begin() as conn` 执行），在 `main.py::init_db` 里 `create_all` 之后调用。
- 时间戳统一 UTC naive：用 `datetime.utcnow()`（与 `issues.py` 的 `resolved_at`、`ai.py` 的 `reviewed_at` 对齐）。
- 前端 `dist/` 提交进 git（服务器无 Node，同源提供预构建静态）：改前端源码后必须 `cd frontend && npm run build`，dist 与源码一起提交。
- `MyReports.vue` 是成员视角挂载点；三态执行按钮配色沿用 Dashboard `.dtag` 口径：通过=`--tech-signal`(#00b386 青绿)、失败=`--tech-danger`(#e05561)、阻塞=`--tech-warn`(#e6a23c) 灰/warn。

---
## File Structure

**后端**
- `app/core/enums.py`（改）：新增 `ChecklistStatus(str, Enum)`（pending/passed/failed/blocked）。
- `app/models/checklist.py`（新建）：`ChecklistItem` 模型。单一职责=清单项表定义。
- `app/models/issue.py`（改）：`RemainingIssue` 的 `report_id` 放宽可空 + 加 `task_id`/`checklist_item_id` 两列。
- `app/models/__init__.py`（改）：汇总导入 `ChecklistItem`。
- `backend/sql/schema.sql`（改）：新增 `checklist_item` 建表 + `remaining_issue` 三处改动。
- `app/db/migrate.py`（改）：新增 `ensure_issue_columns()`（给老库 remaining_issue 补列 + report_id 放宽）。
- `app/main.py`（改）：`init_db` 里调用 `ensure_issue_columns()`。
- `app/schemas/checklist.py`（新建）：请求体 schema（`AttachChecklistIn`/`ChecklistTickIn`/`ChecklistToIssueIn`）。单一职责=清单接口的入参校验。
- `app/api/checklist.py`（新建）：清单增删查改 + 失败转遗留 5 个端点。挂到 `app/main.py` 的 router 注册。
- `app/api/ai.py`（改）：`review_testcase` PATCH 增强——采纳带 task_id 时 upsert 清单项，取消采纳时删仍 pending 的清单项。
- `app/api/stats.py`（改）：`/overview` 与 `/daily` 的 `open_issues` 兼容 report + task 两来源，按 id 去重。

**前端**
- `frontend/src/api/index.js`（改）：4 个薄封装 `getTaskChecklist`/`attachChecklist`/`updateChecklistItem`/`checklistItemToIssue`。
- `frontend/src/views/MyReports.vue`（改）：验收清单表格 + 三态勾选 + 手动补挂弹窗 + 失败转遗留弹窗。
- `frontend/dist/`（改）：`npm run build` 重建，随源码提交。

---

### Task 1: 数据模型层（枚举 + 模型 + schema.sql + 迁移）

一次交付完整的 schema 层：新枚举、新表模型、issue 表放宽、两份 schema 同步、老库迁移。交付后可通过"启动后端 → checklist_item 建出、remaining_issue 补列且 report_id 可空"独立验证。

**Files:**
- Modify: `backend/app/core/enums.py`（末尾加枚举）
- Create: `backend/app/models/checklist.py`
- Modify: `backend/app/models/issue.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/sql/schema.sql`
- Modify: `backend/app/db/migrate.py`
- Modify: `backend/app/main.py:14,31-33`

**Interfaces:**
- Consumes: `Base`（`app.db.session`）、`engine`（`app.db.session`）、`_columns(table)`（`app.db.migrate`，已存在）。
- Produces:
  - 枚举 `ChecklistStatus` 值：`pending`/`passed`/`failed`/`blocked`。
  - 模型 `ChecklistItem`，列：`id`、`task_id:int`、`test_case_id:int`、`project_id:int`、`exec_status:ChecklistStatus`、`executed_by:int|None`、`executed_at:datetime|None`、`created_at:datetime`；表级 `UniqueConstraint("task_id","test_case_id")`。
  - `RemainingIssue` 新增可空列：`task_id:int|None`、`checklist_item_id:int|None`；`report_id` 改 `int|None`。
  - `ensure_issue_columns() -> None`（无参，用模块级 engine）。

- [ ] **Step 1: 加枚举 `ChecklistStatus`**

在 `backend/app/core/enums.py` 末尾（`ReviewStatus` 类之后、`ALL_PROJECT_ROLES` 之前）加：

```python
class ChecklistStatus(str, enum.Enum):
    """验收清单项的执行状态（成员逐条勾选）。"""
    pending = "pending"    # 待执行
    passed = "passed"      # 通过
    failed = "failed"      # 失败
    blocked = "blocked"    # 阻塞
```

- [ ] **Step 2: 新建 `ChecklistItem` 模型**

创建 `backend/app/models/checklist.py`（参照 `app/models/issue.py` 的写法，枚举列用 `Enum(ChecklistStatus, length=16)` + `server_default`；唯一约束用 `__table_args__`）：

```python
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ChecklistStatus
from app.db.session import Base


class ChecklistItem(Base):
    """验收清单项：把采纳的测试点(test_case)挂到任务(task)下，带执行状态。

    采纳测试点时自动 upsert（若测试点有 task_id）；也可手动补挂。
    (task_id, test_case_id) 唯一，防重复挂。执行失败可一键转 RemainingIssue。
    """

    __tablename__ = "checklist_item"
    __table_args__ = (UniqueConstraint("task_id", "test_case_id", name="uq_checklist_task_case"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    exec_status: Mapped[ChecklistStatus] = mapped_column(
        Enum(ChecklistStatus, length=16), default=ChecklistStatus.pending, server_default="pending"
    )
    executed_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- [ ] **Step 3: 放宽 `RemainingIssue`（加列 + report_id 可空）**

改 `backend/app/models/issue.py`。`report_id` 那行改为可空，并在 `report_id` 之后加两列：

```python
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("daily_report.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    checklist_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("checklist_item.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

（其余列不动。一条 issue 要么挂 report 旧路径，要么挂 task 新路径。）

- [ ] **Step 4: 汇总导入新模型**

改 `backend/app/models/__init__.py`：在 `from app.models.ai import AiTask, TestCase` 之后加一行导入，并把 `ChecklistItem` 加进 `__all__`：

```python
from app.models.ai import AiTask, TestCase
from app.models.checklist import ChecklistItem
```

`__all__` 列表末尾（`"TestCase",` 之后）加 `"ChecklistItem",`。

- [ ] **Step 5: 同步 `schema.sql`**

改 `backend/sql/schema.sql`。

(a) `remaining_issue` 建表段：`report_id` 由 `BIGINT NOT NULL` 改 `BIGINT DEFAULT NULL`；在 `report_id` 行之后加两列：

```sql
  `report_id` BIGINT DEFAULT NULL,
  `task_id` BIGINT DEFAULT NULL,
  `checklist_item_id` BIGINT DEFAULT NULL,
```

并在约束区（`fk_issue_owner` 之后）加两个 index（不加 FK 约束到 checklist_item，避免建表顺序依赖；task 的 FK 可加）：

```sql
  KEY `idx_issue_task` (`task_id`),
  KEY `idx_issue_checklist` (`checklist_item_id`),
```

(b) 在 `remaining_issue` 建表段之后、集成层注释之前，新增 `checklist_item` 建表：

```sql
-- ---------- 验收清单（测试点回流任务） ----------
CREATE TABLE `checklist_item` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `task_id` BIGINT NOT NULL,
  `test_case_id` BIGINT NOT NULL,
  `project_id` BIGINT NOT NULL,
  `exec_status` ENUM('pending','passed','failed','blocked') NOT NULL DEFAULT 'pending',
  `executed_by` BIGINT DEFAULT NULL,
  `executed_at` DATETIME DEFAULT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_checklist_task_case` (`task_id`,`test_case_id`),
  KEY `idx_checklist_task` (`task_id`),
  KEY `idx_checklist_project` (`project_id`),
  CONSTRAINT `fk_checklist_task` FOREIGN KEY (`task_id`) REFERENCES `task`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_checklist_case` FOREIGN KEY (`test_case_id`) REFERENCES `test_case`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_checklist_project` FOREIGN KEY (`project_id`) REFERENCES `project`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_checklist_user` FOREIGN KEY (`executed_by`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 6: 加迁移 `ensure_issue_columns`**

在 `backend/app/db/migrate.py` 末尾（`migrate_task_status` 之后）加。注意 report_id 放宽：SQLite 列约束宽松无需 DDL，MySQL 需 `MODIFY COLUMN ... NULL`（沿用 `migrate_task_status` 的方言分支模式）：

```python
def ensure_issue_columns() -> None:
    """remaining_issue 表补列 task_id / checklist_item_id（如缺失），并放宽 report_id 可空。

    放宽 report_id：SQLite 列约束宽松，NOT NULL 不阻塞新路径的 NULL 插入无需 DDL；
    MySQL 需 MODIFY COLUMN 去掉 NOT NULL。加列/放宽都幂等：ADD 前探列，MODIFY 重复执行安全。
    """
    cols = _columns("remaining_issue")
    if not cols:
        return  # 表尚未建，交给 create_all
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "task_id" not in cols:
            conn.execute(text("ALTER TABLE remaining_issue ADD COLUMN task_id BIGINT NULL"))
        if "checklist_item_id" not in cols:
            conn.execute(text("ALTER TABLE remaining_issue ADD COLUMN checklist_item_id BIGINT NULL"))
        if dialect == "mysql":
            # 放宽 report_id 为可空（旧库是 NOT NULL）；SQLite 无需此步
            conn.execute(text(
                "ALTER TABLE remaining_issue MODIFY COLUMN `report_id` BIGINT NULL"
            ))
```

- [ ] **Step 7: init_db 里调用迁移**

改 `backend/app/main.py`：第 14 行的 import 补 `ensure_issue_columns`；`init_db` 里（`migrate_task_status()` 之后，第 33 行下方）加调用。

第 14 行改为：
```python
from app.db.migrate import ensure_issue_columns, ensure_task_columns, ensure_testcase_columns, migrate_task_status
```

`init_db` 里加：
```python
    migrate_task_status()
    ensure_issue_columns()
```

- [ ] **Step 8: 验证建表与迁移（手动，本仓库无测试框架）**

对全新 SQLite 库（默认）验证 create_all 建出新表 + issue 新列：

```bash
cd backend
rm -f test_platform.db
python -c "from app.main import init_db; init_db()"
python -c "
from sqlalchemy import inspect
from app.db.session import engine
insp = inspect(engine)
assert 'checklist_item' in insp.get_table_names(), 'checklist_item 未建'
ci = {c['name'] for c in insp.get_columns('checklist_item')}
assert {'task_id','test_case_id','project_id','exec_status','executed_by','executed_at','created_at'} <= ci, ci
ri = {c['name'] for c in insp.get_columns('remaining_issue')}
assert {'task_id','checklist_item_id'} <= ri, ri
rid = next(c for c in insp.get_columns('remaining_issue') if c['name']=='report_id')
assert rid['nullable'] is True, 'report_id 应可空'
print('OK: checklist_item 建出, remaining_issue 补列且 report_id 可空')
"
```

Expected: 打印 `OK: checklist_item 建出, remaining_issue 补列且 report_id 可空`

再验证老库迁移幂等（模拟 remaining_issue 已存在旧结构，重复 init 不报错）——直接对上面已初始化的库再跑一次 init_db，应无异常：

```bash
python -c "from app.main import init_db; init_db(); init_db(); print('OK: 迁移幂等')"
```

Expected: 打印 `OK: 迁移幂等`（无 traceback）

- [ ] **Step 9: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/core/enums.py backend/app/models/checklist.py backend/app/models/issue.py backend/app/models/__init__.py backend/sql/schema.sql backend/app/db/migrate.py backend/app/main.py
git commit -m "feat(checklist): 数据模型层 — checklist_item 表 + remaining_issue 放宽直挂任务"
```

---
### Task 2: 清单 schema + 查询/补挂/勾选端点

新建入参 schema 与 `app/api/checklist.py`，实现清单的 GET（查任务清单）、POST（手动补挂）、PATCH（勾执行结果）三个端点，并挂进 main。交付后可独立验证：对一条已采纳同项目 test_case 补挂→GET 出现→PATCH 勾 passed 回写 executed_by/at。

**Files:**
- Create: `backend/app/schemas/checklist.py`
- Create: `backend/app/api/checklist.py`
- Modify: `backend/app/main.py`（注册 router）

**Interfaces:**
- Consumes: `assert_project_role(db, user, project_id, roles)`、`get_current_user`（`app.core.deps`）；`ProjectRole`、`ChecklistStatus`（`app.core.enums`）；`get_db`（`app.db.session`）；模型 `ChecklistItem`、`TestCase`、`Task`、`User`（`app.models`）；`ok`（`app.schemas.common`）。`TestCase.review_status` 为 `ReviewStatus` 枚举，采纳态是 `ReviewStatus.adopted`。
- Produces（供 Task 3/5 复用）：
  - `_to_out(db, item) -> dict`（清单项 + 关联 test_case 字段 + executed_by 名）。
  - 端点 `GET /api/tasks/{tid}/checklist`、`POST /api/tasks/{tid}/checklist`、`PATCH /api/checklist/{item_id}`。
  - schema：`AttachChecklistIn{test_case_ids:list[int]}`、`ChecklistTickIn{exec_status:ChecklistStatus}`。
  - 返回的清单项 dict 字段：`id`、`task_id`、`test_case_id`、`project_id`、`exec_status`、`executed_by`、`executed_by_name`、`executed_at`、`created_at`、`title`、`category`、`steps`、`expected`、`priority`。

- [ ] **Step 1: 新建请求体 schema**

创建 `backend/app/schemas/checklist.py`（`ChecklistToIssueIn` 也在此定义，供 Task 4 用，一次写全避免二次改文件）：

```python
from pydantic import BaseModel, Field

from app.core.enums import ChecklistStatus, IssueSeverity


class AttachChecklistIn(BaseModel):
    """手动补挂：把若干已采纳的 test_case 加入某任务清单。"""
    test_case_ids: list[int] = Field(..., min_length=1)


class ChecklistTickIn(BaseModel):
    """勾执行结果。"""
    exec_status: ChecklistStatus


class ChecklistToIssueIn(BaseModel):
    """失败转遗留问题。title 缺省用 test_case.title。"""
    title: str | None = None
    severity: IssueSeverity = IssueSeverity.major
    owner: int | None = None
    external_ref: str | None = None
```

- [ ] **Step 2: 新建 checklist router 骨架 + `_to_out`**

创建 `backend/app/api/checklist.py`（沿用手写 `_to_out` 序列化风格；`_WRITE_ROLES`/`_ALL_ROLES` 仿 `ai.py`）：

```python
"""验收清单路由：任务清单查询、手动补挂、勾执行结果、失败转遗留。

清单项由采纳测试点自动挂载（见 ai.py review_testcase 副作用）或手动补挂。
权限：清单项所属项目 member/admin 可写（不限 assigned_to，本版放开协作），guest 只读。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ChecklistStatus, IssueStatus, ProjectRole, ReviewStatus
from app.db.session import get_db
from app.models import ChecklistItem, RemainingIssue, Task, TestCase, User
from app.schemas.checklist import AttachChecklistIn, ChecklistToIssueIn, ChecklistTickIn
from app.schemas.common import ok

router = APIRouter(prefix="/api", tags=["checklist"])

_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _user_name(db: Session, uid: int | None) -> str:
    if not uid:
        return ""
    u = db.get(User, uid)
    return u.name if u else ""


def _to_out(db: Session, item: ChecklistItem) -> dict:
    tc = db.get(TestCase, item.test_case_id)
    return {
        "id": item.id,
        "task_id": item.task_id,
        "test_case_id": item.test_case_id,
        "project_id": item.project_id,
        "exec_status": item.exec_status.value,
        "executed_by": item.executed_by,
        "executed_by_name": _user_name(db, item.executed_by),
        "executed_at": item.executed_at.isoformat() if item.executed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        # 关联 test_case 展示字段（补挂/采纳的来源测试点）
        "title": tc.title if tc else "",
        "category": tc.category if tc else None,
        "steps": tc.steps if tc else None,
        "expected": tc.expected if tc else None,
        "priority": tc.priority if tc else None,
    }
```

- [ ] **Step 3: GET 任务清单**

在 `checklist.py` 追加。权限：任务所在项目 member/admin/guest 可看：

```python
@router.get("/tasks/{tid}/checklist")
def get_task_checklist(
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取任务的验收清单（清单项 + 关联 test_case 展示字段）。"""
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _ALL_ROLES)
    rows = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.task_id == tid)
        .order_by(ChecklistItem.id)
        .all()
    )
    return ok([_to_out(db, it) for it in rows])
```

- [ ] **Step 4: POST 手动补挂（整体校验，非法则整体拒绝）**

在 `checklist.py` 追加。每个 test_case 必须已采纳（review_status==adopted）且属同项目；upsert 幂等（已挂的跳过、不报错）：

```python
@router.post("/tasks/{tid}/checklist")
def attach_checklist(
    tid: int,
    body: AttachChecklistIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动补挂：把已采纳、同项目的 test_case 批量加入任务清单。

    整体校验：任一 test_case 未采纳或跨项目 → 400，整批拒绝。
    幂等：已存在的 (task_id, test_case_id) 跳过，不报错。
    """
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _WRITE_ROLES)

    ids = list(dict.fromkeys(body.test_case_ids))  # 去重保序
    cases = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    for cid in ids:
        c = found.get(cid)
        if c is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 不存在")
        if c.project_id != task.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 不属于该任务的项目")
        if c.review_status != ReviewStatus.adopted:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"测试点 {cid} 未采纳，不能加入清单")

    existing = {
        it.test_case_id
        for it in db.query(ChecklistItem.test_case_id)
        .filter(ChecklistItem.task_id == tid, ChecklistItem.test_case_id.in_(ids))
        .all()
    }
    for cid in ids:
        if cid in existing:
            continue
        db.add(ChecklistItem(task_id=tid, test_case_id=cid, project_id=task.project_id))
    db.commit()

    rows = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.task_id == tid, ChecklistItem.test_case_id.in_(ids))
        .order_by(ChecklistItem.id)
        .all()
    )
    return ok([_to_out(db, it) for it in rows])
```

（注：`db.query(ChecklistItem.test_case_id)...all()` 返回的是 Row 元组序列，`it.test_case_id` 可直接取——SQLAlchemy 具名列访问。）

- [ ] **Step 5: PATCH 勾执行结果**

在 `checklist.py` 追加。写 `executed_by`=当前用户、`executed_at`=`datetime.utcnow()`；回 pending 时清空。权限：项目 member/admin：

```python
@router.patch("/checklist/{item_id}")
def tick_checklist(
    item_id: int,
    body: ChecklistTickIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """勾执行结果 passed/failed/blocked/pending。回 pending 时清空 executed_by/at。"""
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="清单项不存在")
    assert_project_role(db, user, item.project_id, _WRITE_ROLES)
    item.exec_status = body.exec_status
    if body.exec_status == ChecklistStatus.pending:
        item.executed_by = None
        item.executed_at = None
    else:
        item.executed_by = user.id
        item.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return ok(_to_out(db, item))
```

- [ ] **Step 6: 注册 router 到 main**

改 `backend/app/main.py`：仿现有 router 注册（找到 `app.include_router(...)` 那批），加 checklist。先看现有 import 与注册块：

Run: `grep -n "include_router\|from app.api" backend/app/main.py`

然后在 ai router 注册之后补一行（import 与 include 各一处）：

```python
from app.api import checklist
```
```python
app.include_router(checklist.router)
```

（若现有写法是 `from app.api.xxx import router as xxx_router` 风格，则沿用该风格：`from app.api.checklist import router as checklist_router` + `app.include_router(checklist_router)`。以文件里实际风格为准。）

- [ ] **Step 7: 手动端到端验证**

启动后端后，用一条已采纳、带项目的 test_case 做冒烟（下面用 python 直连 DB 造数据 + TestClient 打接口，避免依赖 AI 生成）：

```bash
cd backend
python -c "
from fastapi.testclient import TestClient
from app.main import app, init_db
from app.db.session import SessionLocal
from app.models import User, Project, ProjectMember, Task, AiTask, TestCase
from app.core.enums import ReviewStatus, AiInputType, AiTaskStatus, TaskStatus, ProjectRole, TaskPriority
from app.core.security import create_access_token
init_db()
s = SessionLocal()
# 造：平台管理员 / 项目 / 任务 / 一条已采纳 test_case
admin = s.query(User).filter_by(is_platform_admin=True).first()
p = Project(name='CL冒烟', status='active'); s.add(p); s.commit(); s.refresh(p)
t = Task(project_id=p.id, assigned_by=admin.id, assigned_to=admin.id, title='验收任务',
         priority=TaskPriority.p1, assigned_date='2026-08-11', status=TaskStatus.pending)
s.add(t); s.commit(); s.refresh(t)
at = AiTask(project_id=p.id, user_id=admin.id, input_type=AiInputType.text, status=AiTaskStatus.done)
s.add(at); s.commit(); s.refresh(at)
tc = TestCase(ai_task_id=at.id, project_id=p.id, task_id=t.id, title='登录边界', category='边界',
              priority='P1', review_status=ReviewStatus.adopted)
s.add(tc); s.commit(); s.refresh(tc)
tok = create_access_token(str(admin.id))
c = TestClient(app); H = {'Authorization': f'Bearer {tok}'}
# 补挂
r = c.post(f'/api/tasks/{t.id}/checklist', json={'test_case_ids':[tc.id]}, headers=H); print('attach', r.status_code, r.json())
item_id = r.json()['data'][0]['id']
# 再补挂一次（幂等，应仍成功且不重复）
r2 = c.post(f'/api/tasks/{t.id}/checklist', json={'test_case_ids':[tc.id]}, headers=H); print('attach2', r2.status_code, len(r2.json()['data']))
# GET
r3 = c.get(f'/api/tasks/{t.id}/checklist', headers=H); print('get', r3.status_code, r3.json()['data'][0]['title'], r3.json()['data'][0]['exec_status'])
# 勾 passed
r4 = c.patch(f'/api/checklist/{item_id}', json={'exec_status':'passed'}, headers=H); d=r4.json()['data']; print('tick', r4.status_code, d['exec_status'], d['executed_by']==admin.id, bool(d['executed_at']))
# 回 pending 清空
r5 = c.patch(f'/api/checklist/{item_id}', json={'exec_status':'pending'}, headers=H); d=r5.json()['data']; print('untick', r5.status_code, d['exec_status'], d['executed_by'], d['executed_at'])
s.close()
"
```

Expected:
- `attach 200 ...` data 含 1 项，`exec_status='pending'`
- `attach2 200 1`（幂等，仍 1 项）
- `get 200 登录边界 pending`
- `tick 200 passed True True`
- `untick 200 pending None None`

- [ ] **Step 8: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/schemas/checklist.py backend/app/api/checklist.py backend/app/main.py
git commit -m "feat(checklist): 清单查询/补挂/勾选端点 + 入参 schema"
```

---
### Task 3: 采纳自动挂载 / 取消删除（改 ai.py PATCH）

增强现有 `PATCH /api/ai/testcases/{cid}`：采纳带 task_id 的测试点时 upsert 清单项；从采纳改为 rejected/pending 时删除对应清单项（仅当仍 pending 未执行，已执行的保留）。这是采纳流程的副作用。交付后独立验证：采纳带 task_id 的测试点→其任务清单出现该项；取消采纳→pending 项消失、已执行项保留。

**Files:**
- Modify: `backend/app/api/ai.py:270-291`（`review_testcase` 函数体）
- Modify: `backend/app/api/ai.py`（顶部 import 补 `ChecklistItem`、`ChecklistStatus`）

**Interfaces:**
- Consumes: 模型 `ChecklistItem`（`app.models`）；枚举 `ChecklistStatus`（`app.core.enums`）；已有 `ReviewStatus`、`db`、`tc`（当前 test_case 对象，含 `tc.task_id`/`tc.project_id`）。
- Produces: 无新签名；`review_testcase` 行为增强（幂等 upsert / 条件删除）。

- [ ] **Step 1: 补 import**

改 `backend/app/api/ai.py` 顶部。第 16 行的 enums import 加 `ChecklistStatus`；第 18 行的 models import 加 `ChecklistItem`：

```python
from app.core.enums import AiTaskStatus, ChecklistStatus, ProjectRole, ReviewStatus
```
```python
from app.models import AiTask, ChecklistItem, Project, TestCase, User
```

- [ ] **Step 2: 在 review_testcase 里加副作用**

改 `backend/app/api/ai.py` 的 `review_testcase`。在 `tc.adopted = (...)` 之后、`db.commit()` 之前插入自动挂载/删除逻辑：

```python
    tc.adopted = (body.review_status == ReviewStatus.adopted)

    # ---- 采纳回流副作用：带 task_id 的测试点采纳→upsert 清单项；取消采纳→删仍 pending 的清单项 ----
    if tc.task_id is not None:
        existing = (
            db.query(ChecklistItem)
            .filter(ChecklistItem.task_id == tc.task_id,
                    ChecklistItem.test_case_id == tc.id)
            .first()
        )
        if body.review_status == ReviewStatus.adopted:
            if existing is None:
                db.add(ChecklistItem(
                    task_id=tc.task_id, test_case_id=tc.id, project_id=tc.project_id,
                ))
            # 已存在则幂等跳过（保留其执行状态）
        else:
            # 取消采纳：仅删仍 pending 未执行的清单项，已执行过的保留（避免丢执行记录）
            if existing is not None and existing.exec_status == ChecklistStatus.pending:
                db.delete(existing)

    db.commit()
    db.refresh(tc)
    return ok(_to_case_out(tc))
```

- [ ] **Step 3: 手动端到端验证**

```bash
cd backend
python -c "
from fastapi.testclient import TestClient
from app.main import app, init_db
from app.db.session import SessionLocal
from app.models import User, Project, Task, AiTask, TestCase, ChecklistItem
from app.core.enums import ReviewStatus, AiInputType, AiTaskStatus, TaskStatus, TaskPriority, ChecklistStatus
from app.core.security import create_access_token
init_db()
s = SessionLocal()
admin = s.query(User).filter_by(is_platform_admin=True).first()
p = Project(name='CL采纳冒烟', status='active'); s.add(p); s.commit(); s.refresh(p)
t = Task(project_id=p.id, assigned_by=admin.id, assigned_to=admin.id, title='T', priority=TaskPriority.p1, assigned_date='2026-08-11', status=TaskStatus.pending); s.add(t); s.commit(); s.refresh(t)
at = AiTask(project_id=p.id, user_id=admin.id, input_type=AiInputType.text, status=AiTaskStatus.done); s.add(at); s.commit(); s.refresh(at)
tc = TestCase(ai_task_id=at.id, project_id=p.id, task_id=t.id, title='自动挂', review_status=ReviewStatus.pending); s.add(tc); s.commit(); s.refresh(tc)
tok = create_access_token(str(admin.id)); c = TestClient(app); H={'Authorization':f'Bearer {tok}'}
# 采纳 → 清单出现
c.patch(f'/api/ai/testcases/{tc.id}', json={'review_status':'adopted'}, headers=H)
n1 = s.query(ChecklistItem).filter_by(task_id=t.id, test_case_id=tc.id).count(); print('adopt->item', n1)  # 1
# 再采纳一次（幂等，不重复）
c.patch(f'/api/ai/testcases/{tc.id}', json={'review_status':'adopted'}, headers=H)
n2 = s.query(ChecklistItem).filter_by(task_id=t.id, test_case_id=tc.id).count(); print('adopt2->item', n2)  # 1
# 取消采纳(pending 项) → 消失
c.patch(f'/api/ai/testcases/{tc.id}', json={'review_status':'rejected'}, headers=H)
n3 = s.query(ChecklistItem).filter_by(task_id=t.id, test_case_id=tc.id).count(); print('reject->item', n3)  # 0
# 重新采纳 + 勾 passed，再取消采纳 → 已执行项保留
c.patch(f'/api/ai/testcases/{tc.id}', json={'review_status':'adopted'}, headers=H)
it = s.query(ChecklistItem).filter_by(task_id=t.id, test_case_id=tc.id).first()
it.exec_status = ChecklistStatus.passed; s.commit()
c.patch(f'/api/ai/testcases/{tc.id}', json={'review_status':'pending'}, headers=H)
n4 = s.query(ChecklistItem).filter_by(task_id=t.id, test_case_id=tc.id).count(); print('reject-executed->keep', n4)  # 1
s.close()
"
```

Expected: `adopt->item 1`、`adopt2->item 1`、`reject->item 0`、`reject-executed->keep 1`

- [ ] **Step 4: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/api/ai.py
git commit -m "feat(checklist): 采纳测试点自动 upsert 清单项，取消采纳删仍 pending 的项"
```

---

### Task 4: 失败转遗留端点（POST to-issue）

在 `checklist.py` 加 `POST /api/checklist/{item_id}/to-issue`：前置该项 exec_status==failed，创建 RemainingIssue（report_id=None、task_id=清单项 task、checklist_item_id=item、project_id 带上、status=open）。交付后独立验证：failed 项转遗留→issue 生成且字段对；非 failed→400。

**Files:**
- Modify: `backend/app/api/checklist.py`（追加端点；顶部已 import `RemainingIssue`/`IssueStatus`/`ChecklistToIssueIn`）

**Interfaces:**
- Consumes: `RemainingIssue`（`app.models`）、`IssueStatus`（`app.core.enums`）、`ChecklistToIssueIn`（`app.schemas.checklist`，Task 2 已建）、`ChecklistStatus.failed`。
- Produces: 端点 `POST /api/checklist/{item_id}/to-issue`，返回新建 issue dict（字段：`id`、`title`、`severity`、`status`、`project_id`、`task_id`、`checklist_item_id`、`report_id`、`owner`、`external_ref`、`created_at`）。

- [ ] **Step 1: 加 to-issue 端点**

在 `backend/app/api/checklist.py` 末尾追加。title 缺省用 test_case.title：

```python
@router.post("/checklist/{item_id}/to-issue")
def checklist_to_issue(
    item_id: int,
    body: ChecklistToIssueIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """失败清单项一键转遗留问题。前置：exec_status==failed。

    创建 RemainingIssue：report_id=None（走任务直挂新路径），task_id/checklist_item_id
    指向来源，status=open。title 缺省用来源 test_case.title。
    """
    item = db.get(ChecklistItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="清单项不存在")
    assert_project_role(db, user, item.project_id, _WRITE_ROLES)
    if item.exec_status != ChecklistStatus.failed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅失败的清单项可转遗留问题")

    tc = db.get(TestCase, item.test_case_id)
    title = (body.title or "").strip() or (tc.title if tc else "未命名遗留问题")
    issue = RemainingIssue(
        report_id=None,
        task_id=item.task_id,
        checklist_item_id=item.id,
        project_id=item.project_id,
        title=title[:255],
        severity=body.severity,
        status=IssueStatus.open,
        owner=body.owner,
        external_ref=body.external_ref,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return ok({
        "id": issue.id,
        "title": issue.title,
        "severity": issue.severity.value,
        "status": issue.status.value,
        "project_id": issue.project_id,
        "task_id": issue.task_id,
        "checklist_item_id": issue.checklist_item_id,
        "report_id": issue.report_id,
        "owner": issue.owner,
        "external_ref": issue.external_ref,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
    })
```

- [ ] **Step 2: 手动端到端验证**

```bash
cd backend
python -c "
from fastapi.testclient import TestClient
from app.main import app, init_db
from app.db.session import SessionLocal
from app.models import User, Project, Task, AiTask, TestCase, ChecklistItem, RemainingIssue
from app.core.enums import ReviewStatus, AiInputType, AiTaskStatus, TaskStatus, TaskPriority, ChecklistStatus
from app.core.security import create_access_token
init_db()
s = SessionLocal()
admin = s.query(User).filter_by(is_platform_admin=True).first()
p = Project(name='CL转遗留冒烟', status='active'); s.add(p); s.commit(); s.refresh(p)
t = Task(project_id=p.id, assigned_by=admin.id, assigned_to=admin.id, title='T', priority=TaskPriority.p1, assigned_date='2026-08-11', status=TaskStatus.pending); s.add(t); s.commit(); s.refresh(t)
at = AiTask(project_id=p.id, user_id=admin.id, input_type=AiInputType.text, status=AiTaskStatus.done); s.add(at); s.commit(); s.refresh(at)
tc = TestCase(ai_task_id=at.id, project_id=p.id, task_id=t.id, title='并发下单丢单', review_status=ReviewStatus.adopted); s.add(tc); s.commit(); s.refresh(tc)
item = ChecklistItem(task_id=t.id, test_case_id=tc.id, project_id=p.id); s.add(item); s.commit(); s.refresh(item)
tok = create_access_token(str(admin.id)); c = TestClient(app); H={'Authorization':f'Bearer {tok}'}
# 非 failed → 400
r0 = c.post(f'/api/checklist/{item.id}/to-issue', json={'severity':'major'}, headers=H); print('not-failed', r0.status_code)  # 400
# 勾 failed 再转
c.patch(f'/api/checklist/{item.id}', json={'exec_status':'failed'}, headers=H)
r1 = c.post(f'/api/checklist/{item.id}/to-issue', json={'severity':'blocker'}, headers=H); d=r1.json()['data']
print('to-issue', r1.status_code, d['title'], d['report_id'], d['task_id']==t.id, d['checklist_item_id']==item.id, d['severity'])
s.close()
"
```

Expected: `not-failed 400`；`to-issue 200 并发下单丢单 None True True blocker`

- [ ] **Step 3: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/api/checklist.py
git commit -m "feat(checklist): 失败清单项一键转遗留问题(直挂任务)"
```

---
### Task 5: 统计口径兼容（改 stats.py open_issues）

`/stats/overview` 与 `/stats/daily` 的 `open_issues` 从"仅 report 路径"改为兼容 report + task 两来源，按 issue id 去重防双算。交付后独立验证：任务直挂一条 open issue 后，overview 计入且不与 report 路径重复；daily 口径按"该日报下 + 该项目该日任务直挂"计。

**Files:**
- Modify: `backend/app/api/stats.py:67-73`（overview 的 open_issues 聚合）
- Modify: `backend/app/api/stats.py:150-156`（daily 的 open_issues 聚合）
- Modify: `backend/app/api/stats.py`（顶部 import 补 `ChecklistItem`）

**Interfaces:**
- Consumes: 模型 `ChecklistItem`（`app.models`）、已有 `RemainingIssue`/`Task`/`IssueStatus`、`pids`（可见项目 id 列表）、`report_ids`（daily 里该日已交日报 id）。
- Produces: 无新签名；overview 返回的 `open_issues` 计跨项目 open issue（report 路径 ∪ task 路径，按 id 去重）；daily 返回的 `open_issues` 计"该日报下 ∪ 该项目该日任务直挂"的 open issue（按 id 去重）。
- **口径定义（定死 spec §4.6 歧义）**：daily 的 task 路径 = `RemainingIssue.status==open` 且 `RemainingIssue.task_id` 指向的 Task 满足 `Task.project_id==该项目 且 Task.assigned_date==该 date`。与 report 路径按 issue id 取并集去重。

- [ ] **Step 1: 补 import**

改 `backend/app/api/stats.py` 第 10 行的 models import，加 `ChecklistItem`（虽本任务聚合主要用 `RemainingIssue.task_id`，但保持与模型层一致；若最终未直接引用 `ChecklistItem` 可不加——以是否 lint 报未用为准，本仓库无 lint，加了无害）。实际本任务只需 `RemainingIssue` + `Task`，两者已在 import 中，**无需改 import**。跳过此步除非引用了新符号。

（说明：此步是留白提示，实际实现按下面 Step 2/3 的代码，若未引入新符号则不改 import。）

- [ ] **Step 2: 改 overview 的 open_issues（report 路径 ∪ task 路径，去重）**

改 `backend/app/api/stats.py` 的 `overview_stats`。把原来的单一 `open_issues` 查询（现约 67-73 行）替换为并集去重。原代码：

```python
    # ---- 未解决遗留问题（跨项目存量，不限今日）----
    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .scalar() or 0
    )
```

替换为（按 id 收集两路径再取并集大小，避免 SQL 层 OR 与去重的方言差异）：

```python
    # ---- 未解决遗留问题（跨项目存量，不限今日）----
    # 两条来源：report 路径（project_id 命中）与 task 直挂路径（task_id 指向可见项目的任务）。
    # RemainingIssue.project_id 两路径都会带上，故 project_id.in_(pids) 已覆盖大部分；
    # 但为兼容历史 task 直挂 issue 的 project_id 与其 task 项目一致的约定，按 id 取并集去重防双算。
    open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .filter(RemainingIssue.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    task_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .join(Task, Task.id == RemainingIssue.task_id)
        .filter(Task.project_id.in_(pids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    open_issues = len(open_ids | task_open_ids)
```

- [ ] **Step 3: 改 daily 的 open_issues（该日报下 ∪ 该项目该日任务直挂，去重）**

改 `backend/app/api/stats.py` 的 `daily_stats`。原代码（现约 150-156 行）：

```python
    report_ids = [r.id for r in submitted_rows]
    open_issues = (
        db.query(func.count(RemainingIssue.id))
        .filter(RemainingIssue.report_id.in_(report_ids),
                RemainingIssue.status == IssueStatus.open)
        .scalar() if report_ids else 0
    )
```

替换为（report 路径按 report_ids；task 路径按"该项目该日任务直挂"；并集去重）：

```python
    report_ids = [r.id for r in submitted_rows]
    # open_issues 两条来源，按 id 去重：
    # (1) report 路径：该日已交日报下挂的 open issue；
    # (2) task 路径：task_id 指向"本项目、assigned_date==该日"的任务的 open issue。
    report_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .filter(RemainingIssue.report_id.in_(report_ids),
                RemainingIssue.status == IssueStatus.open)
        .all()
    } if report_ids else set()
    task_open_ids = {
        iid for (iid,) in
        db.query(RemainingIssue.id)
        .join(Task, Task.id == RemainingIssue.task_id)
        .filter(Task.project_id == project_id,
                Task.assigned_date == date,
                RemainingIssue.status == IssueStatus.open)
        .all()
    }
    open_issues = len(report_open_ids | task_open_ids)
```

（`new_issues` 保持不变——仍只计 report 路径的当日新增，task 直挂的遗留不计入"当日新报数"，语义更清晰。）

- [ ] **Step 4: 手动端到端验证**

```bash
cd backend
python -c "
from fastapi.testclient import TestClient
from app.main import app, init_db
from app.db.session import SessionLocal
from app.models import User, Project, ProjectMember, Task, AiTask, TestCase, ChecklistItem, RemainingIssue
from app.core.enums import ReviewStatus, AiInputType, AiTaskStatus, TaskStatus, TaskPriority, ChecklistStatus, IssueStatus, IssueSeverity
from app.core.security import create_access_token
init_db()
s = SessionLocal()
admin = s.query(User).filter_by(is_platform_admin=True).first()
p = Project(name='CL统计冒烟', status='active'); s.add(p); s.commit(); s.refresh(p)
t = Task(project_id=p.id, assigned_by=admin.id, assigned_to=admin.id, title='T', priority=TaskPriority.p1, assigned_date='2026-08-11', status=TaskStatus.pending); s.add(t); s.commit(); s.refresh(t)
at = AiTask(project_id=p.id, user_id=admin.id, input_type=AiInputType.text, status=AiTaskStatus.done); s.add(at); s.commit(); s.refresh(at)
tc = TestCase(ai_task_id=at.id, project_id=p.id, task_id=t.id, title='X', review_status=ReviewStatus.adopted); s.add(tc); s.commit(); s.refresh(tc)
item = ChecklistItem(task_id=t.id, test_case_id=tc.id, project_id=p.id, exec_status=ChecklistStatus.failed); s.add(item); s.commit(); s.refresh(item)
# 任务直挂一条 open issue（report_id=None）
iss = RemainingIssue(report_id=None, task_id=t.id, checklist_item_id=item.id, project_id=p.id, title='X', severity=IssueSeverity.major, status=IssueStatus.open); s.add(iss); s.commit()
tok = create_access_token(str(admin.id)); c = TestClient(app); H={'Authorization':f'Bearer {tok}'}
# overview 计入 task 直挂
ro = c.get('/api/stats/overview', headers=H, params={'date':'2026-08-11'}).json()['data']; print('overview open_issues>=1', ro['open_issues'] >= 1)
# daily 计入 task 直挂（该项目该日）
rd = c.get('/api/stats/daily', headers=H, params={'project_id':p.id,'date':'2026-08-11'}).json()['data']; print('daily open_issues>=1', rd['open_issues'] >= 1)
# 去重：不重复造，此处验证同一 issue 不会被两路径双算——overview 再查一次值稳定
ro2 = c.get('/api/stats/overview', headers=H, params={'date':'2026-08-11'}).json()['data']; print('overview stable', ro['open_issues']==ro2['open_issues'])
s.close()
"
```

Expected: `overview open_issues>=1 True`、`daily open_issues>=1 True`、`overview stable True`

- [ ] **Step 5: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/api/stats.py
git commit -m "feat(checklist): 统计 open_issues 兼容 report+task 两来源并按 id 去重"
```

---
### Task 6: 前端验收清单 UI（api 封装 + MyReports.vue + dist）

api/index.js 加 4 个薄封装；MyReports.vue 在每行任务下加可展开的验收清单（三态勾选 + 失败转遗留），并加"添加测试点"手动补挂弹窗与"转遗留"弹窗。改完 `npm run build` 重建 dist 一起提交。交付后独立验证：成员打开任务→展开清单→勾选即时回写→补挂弹窗只列可选项→转遗留弹窗预填 title→空态占位。

**Files:**
- Modify: `frontend/src/api/index.js`（加 4 个封装）
- Modify: `frontend/src/views/MyReports.vue`（整文件替换，见下）
- Modify: `frontend/dist/**`（`npm run build` 产物）

**Interfaces:**
- Consumes: 后端端点 `GET/POST /api/tasks/{tid}/checklist`、`PATCH /api/checklist/{item_id}`、`POST /api/checklist/{item_id}/to-issue`；已有 `listProjects`/`listTasks`/`listReports`/`upsertReport`、`listAiCases`（用于补挂弹窗列已采纳测试点——但更准确的可选源见 Step 3 说明）。
- Produces: `getTaskChecklist(tid)`、`attachChecklist(tid, testCaseIds)`、`updateChecklistItem(itemId, exec_status)`、`checklistItemToIssue(itemId, payload)`。

- [ ] **Step 1: api 薄封装**

改 `frontend/src/api/index.js`，在 `reviewTestcase` 那行之后（QA Copilot 段内）追加：

```javascript
// ===== 验收清单（测试点回流任务）=====
export const getTaskChecklist = (tid) => http.get(`/tasks/${tid}/checklist`)
export const attachChecklist = (tid, testCaseIds) => http.post(`/tasks/${tid}/checklist`, { test_case_ids: testCaseIds })
export const updateChecklistItem = (itemId, exec_status) => http.patch(`/checklist/${itemId}`, { exec_status })
export const checklistItemToIssue = (itemId, payload) => http.post(`/checklist/${itemId}/to-issue`, payload)
```

- [ ] **Step 2: 后端补一个"项目下可补挂的已采纳测试点"查询端点**

补挂弹窗需要"该项目已采纳、且未进本任务清单"的 test_case 列表。现有接口 `listAiCases(aid)` 是按单次 AI 生成查，不满足"按项目查全部已采纳"。加一个轻量端点。

改 `backend/app/api/checklist.py`，追加（放 to-issue 之后）：

```python
@router.get("/tasks/{tid}/adoptable-cases")
def list_adoptable_cases(
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出该任务所在项目、已采纳、且尚未进本任务清单的 test_case（供手动补挂弹窗选择）。"""
    task = db.get(Task, tid)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, task.project_id, _ALL_ROLES)
    attached = {
        cid for (cid,) in
        db.query(ChecklistItem.test_case_id).filter(ChecklistItem.task_id == tid).all()
    }
    rows = (
        db.query(TestCase)
        .filter(TestCase.project_id == task.project_id,
                TestCase.review_status == ReviewStatus.adopted)
        .order_by(TestCase.id.desc())
        .all()
    )
    out = [
        {"id": tc.id, "title": tc.title, "category": tc.category, "priority": tc.priority}
        for tc in rows if tc.id not in attached
    ]
    return ok(out)
```

并在 `frontend/src/api/index.js` 的验收清单段加：

```javascript
export const listAdoptableCases = (tid) => http.get(`/tasks/${tid}/adoptable-cases`)
```

- [ ] **Step 3: 整文件替换 MyReports.vue**

用下面完整内容替换 `frontend/src/views/MyReports.vue`。在原日报表格每行加"验收清单"操作按钮（展开抽屉），抽屉内三态勾选 + 失败转遗留 + 手动补挂：

```vue
<template>
  <div class="my-reports">
    <el-card>
      <template #header>
        <div class="header">
          <span>我的日报</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="loading" size="small" empty-text="该日没有指派给你的任务">
        <el-table-column prop="title" label="任务名称" min-width="150" />
        <el-table-column prop="developer" label="开发" width="100" />
        <el-table-column label="需求" width="80">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">需求</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="已报进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="row._progress ?? 0" :status="row._progress >= 100 ? 'success' : ''" />
          </template>
        </el-table-column>
        <el-table-column label="日报状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row._reported" type="success" size="small">已提交</el-tag>
            <el-tag v-else type="warning" size="small">未提交</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReport(row)">填报</el-button>
            <el-button link type="primary" @click="openChecklist(row)">验收清单</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 填报日报弹窗 -->
    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="`填报日报 · ${form.title || ''}`" width="620px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="测试进度">
          <el-slider v-model="form.progress_pct" :max="100" show-input style="padding-right:8px" />
        </el-form-item>
        <el-form-item label="是否上线">
          <el-switch v-model="form.is_online" /> <span class="tip">{{ form.is_online ? '今日已上线' : '未上线' }}</span>
        </el-form-item>
        <el-form-item label="工作量(人时)">
          <el-input-number v-model="form.workload_hours" :min="0" :max="24" :step="0.5" />
        </el-form-item>
        <el-form-item label="今日小结"><el-input v-model="form.summary" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="遗留问题">
          <div v-for="(it, i) in form.issues" :key="i" class="issue-row">
            <el-input v-model="it.title" placeholder="问题标题" style="flex:1" />
            <el-select v-model="it.severity" style="width:90px">
              <el-option label="blocker" value="blocker" />
              <el-option label="major" value="major" />
              <el-option label="minor" value="minor" />
            </el-select>
            <el-button link type="danger" @click="form.issues.splice(i,1)">删</el-button>
          </div>
          <el-button size="small" @click="form.issues.push({ title: '', severity: 'minor', status: 'open' })">+ 添加遗留问题</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">提交日报</el-button>
      </template>
    </el-dialog>

    <!-- 验收清单抽屉 -->
    <el-drawer v-model="cl.visible" :title="`验收清单 · ${cl.taskTitle}`" size="640px">
      <div class="cl-toolbar">
        <span class="cl-sum">共 {{ cl.items.length }} 项 · 通过 {{ clStat.passed }} · 失败 {{ clStat.failed }} · 阻塞 {{ clStat.blocked }} · 待执行 {{ clStat.pending }}</span>
        <el-button size="small" type="primary" plain @click="openAttach">添加测试点</el-button>
      </div>
      <el-table :data="cl.items" v-loading="cl.loading" size="small" empty-text="暂无验收项，点右上角「添加测试点」补挂已采纳的测试点">
        <el-table-column prop="title" label="测试点" min-width="160" show-overflow-tooltip />
        <el-table-column prop="category" label="维度" width="70" />
        <el-table-column prop="expected" label="预期" min-width="140" show-overflow-tooltip />
        <el-table-column label="结果" width="200">
          <template #default="{ row }">
            <el-button-group>
              <el-button size="small" :type="row.exec_status==='passed' ? 'success' : ''" :plain="row.exec_status!=='passed'" @click="tick(row,'passed')">通过</el-button>
              <el-button size="small" :type="row.exec_status==='failed' ? 'danger' : ''" :plain="row.exec_status!=='failed'" @click="tick(row,'failed')">失败</el-button>
              <el-button size="small" :type="row.exec_status==='blocked' ? 'warning' : ''" :plain="row.exec_status!=='blocked'" @click="tick(row,'blocked')">阻塞</el-button>
            </el-button-group>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="row.exec_status==='failed'" link type="danger" @click="openToIssue(row)">转遗留</el-button>
            <span v-else class="cl-dim">-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 手动补挂弹窗 -->
    <el-dialog v-model="attach.visible" title="添加测试点到验收清单" width="560px">
      <div v-if="!attach.options.length" class="cl-dim" style="padding:12px 0">该项目暂无「已采纳、且未加入本清单」的测试点。</div>
      <el-checkbox-group v-else v-model="attach.selected">
        <div v-for="o in attach.options" :key="o.id" class="attach-row">
          <el-checkbox :value="o.id">
            <span>{{ o.title }}</span>
            <el-tag v-if="o.category" size="small" style="margin-left:6px">{{ o.category }}</el-tag>
            <el-tag v-if="o.priority" size="small" type="info" style="margin-left:4px">{{ o.priority }}</el-tag>
          </el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="attach.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!attach.selected.length" :loading="attach.saving" @click="doAttach">
          添加 {{ attach.selected.length ? `(${attach.selected.length})` : '' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 失败转遗留弹窗 -->
    <el-dialog v-model="toIssue.visible" title="转为遗留问题" width="520px">
      <el-form :model="toIssue.form" label-width="80px">
        <el-form-item label="标题"><el-input v-model="toIssue.form.title" placeholder="缺省用测试点标题" /></el-form-item>
        <el-form-item label="严重度">
          <el-select v-model="toIssue.form.severity" style="width:140px">
            <el-option label="blocker" value="blocker" />
            <el-option label="major" value="major" />
            <el-option label="minor" value="minor" />
          </el-select>
        </el-form-item>
        <el-form-item label="外部缺陷"><el-input v-model="toIssue.form.external_ref" placeholder="Jira/Tapd 缺陷ID（可选）" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="toIssue.visible = false">取消</el-button>
        <el-button type="primary" :loading="toIssue.saving" @click="doToIssue">创建遗留问题</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listProjects, listTasks, listReports, upsertReport,
  getTaskChecklist, attachChecklist, updateChecklistItem, checklistItemToIssue, listAdoptableCases,
} from '@/api'

const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const tasks = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, saving: false })
const form = reactive({
  task_id: null, title: '', report_date: '', progress_pct: 0, is_online: false,
  workload_hours: 0, summary: '', issues: [],
})

// 验收清单抽屉
const cl = reactive({ visible: false, loading: false, taskId: null, taskTitle: '', items: [] })
const clStat = computed(() => {
  const s = { passed: 0, failed: 0, blocked: 0, pending: 0 }
  cl.items.forEach((it) => { s[it.exec_status] = (s[it.exec_status] || 0) + 1 })
  return s
})
// 手动补挂
const attach = reactive({ visible: false, saving: false, options: [], selected: [] })
// 失败转遗留
const toIssue = reactive({ visible: false, saving: false, itemId: null, form: { title: '', severity: 'major', external_ref: '' } })

onMounted(async () => {
  projects.value = await listProjects()
  if (projects.value.length) { pid.value = projects.value[0].id; await load() }
})

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    const [myTasks, reports] = await Promise.all([
      listTasks({ project_id: pid.value, date: date.value, mine: true }),
      listReports(pid.value, date.value),
    ])
    const repByTask = {}
    reports.forEach((r) => { repByTask[r.task_id] = r })
    tasks.value = myTasks.map((t) => {
      const r = repByTask[t.id]
      return { ...t, _reported: !!r, _progress: r?.progress_pct ?? 0, _report: r }
    })
  } finally { loading.value = false }
}

function openReport(row) {
  form.task_id = row.id
  form.title = row.title
  form.report_date = date.value
  const r = row._report
  form.progress_pct = r?.progress_pct ?? 0
  form.is_online = r?.is_online ?? false
  form.workload_hours = r?.workload_hours ?? 0
  form.summary = r?.summary ?? ''
  form.issues = (r?.issues?.map((x) => ({ title: x.title, severity: x.severity, status: x.status })) || [])
  dialog.visible = true
}

async function submit() {
  if (!form.task_id) return
  dialog.saving = true
  try {
    await upsertReport({
      task_id: form.task_id, report_date: form.report_date,
      progress_pct: form.progress_pct, is_online: form.is_online,
      workload_hours: form.workload_hours, summary: form.summary,
      issues: form.issues.filter((i) => i.title),
    })
    ElMessage.success('日报已提交')
    await load()
  } finally { dialog.saving = false; dialog.visible = false }
}

// ---- 验收清单 ----
async function openChecklist(row) {
  cl.taskId = row.id
  cl.taskTitle = row.title
  cl.visible = true
  cl.loading = true
  try {
    cl.items = await getTaskChecklist(row.id)
  } finally { cl.loading = false }
}

async function tick(row, exec_status) {
  const prev = row.exec_status
  row.exec_status = exec_status  // 乐观更新
  try {
    const data = await updateChecklistItem(row.id, exec_status)
    Object.assign(row, data)  // 用返回 data 回写（executed_by/at）
  } catch {
    row.exec_status = prev
    ElMessage.error('操作失败，请重试')
  }
}

async function openAttach() {
  attach.selected = []
  attach.options = await listAdoptableCases(cl.taskId)
  attach.visible = true
}

async function doAttach() {
  attach.saving = true
  try {
    await attachChecklist(cl.taskId, attach.selected)
    ElMessage.success('已添加到验收清单')
    attach.visible = false
    cl.items = await getTaskChecklist(cl.taskId)
  } finally { attach.saving = false }
}

function openToIssue(row) {
  toIssue.itemId = row.id
  toIssue.form = { title: row.title || '', severity: 'major', external_ref: '' }
  toIssue.visible = true
}

async function doToIssue() {
  toIssue.saving = true
  try {
    await checklistItemToIssue(toIssue.itemId, {
      title: toIssue.form.title || undefined,
      severity: toIssue.form.severity,
      external_ref: toIssue.form.external_ref || undefined,
    })
    ElMessage.success('已创建遗留问题')
    toIssue.visible = false
  } finally { toIssue.saving = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.tip { color: #999; font-size: 12px; margin-left: 8px; }
.issue-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.cl-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.cl-sum { font-size: 12px; color: var(--tech-muted, #6b7280); }
.cl-dim { color: var(--tech-dim, #9aa3b2); }
.attach-row { padding: 4px 0; }
</style>
```

- [ ] **Step 4: 重建 dist**

```bash
cd frontend && npm run build
```

Expected: 构建成功，`dist/` 更新（无报错）。

- [ ] **Step 5: 手动验证（浏览器）**

启动后端（`cd backend && uvicorn app.main:app --reload --port 8000`），前端 dev（`cd frontend && npm run dev`）或直接访问后端同源静态。以成员身份登录 → 我的日报 → 某任务点"验收清单":
- 抽屉打开，若有采纳且带该任务的测试点则列出（pending）；否则空态提示。
- 点"添加测试点"→ 弹窗列该项目已采纳、未在本清单的测试点；勾选添加 → 抽屉刷新出现。
- 点"通过/失败/阻塞"→ 即时高亮，刷新后保持。
- 失败项点"转遗留"→ 弹窗预填测试点标题 → 创建 → 成功提示。

- [ ] **Step 6: 提交（含 dist）**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add frontend/src/api/index.js frontend/src/views/MyReports.vue frontend/dist backend/app/api/checklist.py
git commit -m "feat(checklist): 前端验收清单 UI(三态勾选/补挂/转遗留) + adoptable-cases 端点 + 重建 dist"
```

---

## Self-Review 记录

**Spec 覆盖**（对 `docs/superpowers/specs/2026-08-11-checklist-reflow-design.md` 逐节）：
- §3 数据模型（checklist_item / issue 放宽 / 迁移 / schema.sql / __init__ 导入）→ Task 1 全覆盖。
- §4.1 采纳自动挂载 → Task 3。§4.2 GET checklist → Task 2 Step3。§4.3 POST 补挂 → Task 2 Step4。§4.4 PATCH 勾选 → Task 2 Step5。§4.5 to-issue → Task 4。§4.6 统计兼容 → Task 5。
- §5 前端（api 封装 / MyReports UI / 补挂弹窗 / 转遗留弹窗 / 重建 dist）→ Task 6。补充：spec 未显式提"可补挂源查询端点"，实现时发现 `listAiCases` 不满足"按项目查已采纳"，故 Task 6 Step2 增补 `GET /tasks/{tid}/adoptable-cases`（属 §5.3 补挂弹窗的必要支撑，非范围扩张）。
- §7 边界：补挂非法整体拒绝(Task2 S4)、to-issue 非 failed 400(Task4 S1)、取消采纳只删 pending(Task3 S2)、唯一约束幂等(Task1 模型 + Task2 upsert)、权限复用 assert_project_role(全程)、UTC 时间戳(Task2 S5)、统计去重(Task5)。

**占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码块；验证步骤含可跑命令与 Expected。Task 5 Step1 是"是否需改 import"的说明性留白，非占位（已注明实际不改 import）。

**类型/签名一致性**：`_to_out(db, item)` 字段贯穿 Task2/6；`ChecklistStatus` 值 pending/passed/failed/blocked 贯穿模型/schema/前端；端点路径 `/api/tasks/{tid}/checklist`、`/api/checklist/{item_id}`、`/api/checklist/{item_id}/to-issue`、`/api/tasks/{tid}/adoptable-cases` 前后端一致；`attachChecklist(tid, testCaseIds)` → body `{test_case_ids}` 对应后端 `AttachChecklistIn`。
