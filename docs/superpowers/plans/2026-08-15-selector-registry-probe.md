# 选择器注册表单源化 + 设备探测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把语义选择器注册表从分散的 `selectors.json` 文件改为后端 DB 单一事实来源 + API 下发,并让平台网页能触发在线设备探测当前页面元素、一键写回注册表。

**Architecture:** 注册表落 DB,按 `(project_id, sub_product)` 作用域存储(项目级共享 `''` ∪ 子产品覆盖的 merge)。后端 API 统一读写;生成侧进程内读 DB;runner 通过 API 拉注册表(带缓存,失败回落内置文件)。探测复用 exec-queue 那套"入队→设备轮询→回写"机制,单开 `probe_request` 表承载大结果。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Pydantic v2(后端);Vue3 + ElementPlus + Pinia(前端);Node 零依赖 runner;SQLite(dev)/ MySQL 5.6(prod)。

**Spec:** `docs/superpowers/specs/2026-08-15-selector-registry-probe-design.md`

## Global Constraints

- **无测试框架**:本仓库没有 pytest/lint,禁止臆造。后端任务的"测试"= 用 `backend/.venv/bin/python -c "..."`(或临时脚本)跑 TestClient/直接调用做冒烟;前端手动端到端。每个任务仍走"先写冒烟验证(此刻应失败)→ 实现 → 冒烟通过 → 提交"。
- **MySQL 5.6 无 JSON 类型**:所有 JSON 一律用 `TEXT` 列存 JSON 字符串(`candidates`/`params`/`result`)。不使用 JSON 函数/生成列。
- **响应信封 `{code,msg,data}`**:后端接口一律 `from app.schemas.common import ok`;`raise HTTPException(status, detail="中文")` 即可,`app/core/errors.py` 会转信封。
- **RBAC**:project_id 来自 query/body 时用 `assert_project_role(db, user, project_id, roles)`(非注入版);runner 接口用 `Depends(require_runner_ctx)`。角色枚举 `from app.core.enums import ProjectRole`。
- **两份 schema 手动同步**:改表要同时改 SQLAlchemy 模型(`app/models/`)与 `backend/sql/schema.sql`;老库增量靠 `app/db/migrate.py` 的 `ensure_*` 幂等 `ALTER/CREATE`,并在 `app/main.py::init_db` 调用。新模型必须在 `app/models/__init__.py` 汇总导入。
- **序列化**:不用 `response_model`,每个 router 手写 `_to_out(obj)->dict`(枚举取 `.value`、日期 `isoformat`)。
- **子产品枚举**:取值白名单 = `app/api/release.py::SUB_PRODUCTS`(`纳米Work云端版/纳米Work桌面版/360安全龙虾云端版/360安全龙虾WSL`);`''`(空串)= 项目级共享。
- **生成侧只用项目级共享 key**(sub_product='');生成页不加子产品控件。
- **前端**:`api/index.js` 薄封装返回已解包 data;`api/http.js` 拦截器 `code===0` 解包;需要项目 admin/member 的页面在 `router` 挂 `meta`;别名 `@`→`frontend/src`。
- **runner**:纯 Node(v18+ 内置 `fetch`),零外部依赖;`.env` 里 `BASE_URL`/`RUNNER_TOKEN`/`RUNNER_ID`。

---

# 阶段一:注册表单源(地基)

## Task 1: 数据模型 + 迁移 + schema.sql

**Files:**
- Create: `backend/app/models/selector.py`
- Modify: `backend/app/models/__init__.py`(汇总导入)
- Modify: `backend/app/db/migrate.py`(新增 `ensure_selector_tables`)
- Modify: `backend/app/main.py`(init_db 调用)
- Modify: `backend/sql/schema.sql`(补两张表)

**Interfaces:**
- Produces:
  - `SelectorKey`(表 `selector_key`):`id, project_id:int, sub_product:str, key:str, frame:str, desc:str, candidates:str(TEXT,JSON), updated_by:int|None, updated_at:datetime`
  - `SelectorScope`(表 `selector_scope`):`id, project_id:int, sub_product:str, vm_iframe:str, updated_at:datetime`
  - `ensure_selector_tables(engine) -> None`

- [ ] **Step 1: 写模型**

`backend/app/models/selector.py`(参照 `app/models/ai.py` 的写法):
```python
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SelectorKey(Base):
    __tablename__ = "selector_key"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", "key", name="uq_selkey_scope_key"),
        Index("idx_selkey_scope", "project_id", "sub_product"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    key: Mapped[str] = mapped_column(String(64))
    frame: Mapped[str] = mapped_column(String(8), default="auto", server_default="auto")
    desc: Mapped[str] = mapped_column(String(255), default="", server_default="")
    candidates: Mapped[str] = mapped_column(Text, default="[]")  # JSON 字符串
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SelectorScope(Base):
    __tablename__ = "selector_scope"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", name="uq_selscope"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    vm_iframe: Mapped[str] = mapped_column(String(255), default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```
> 注:确认 `Base` 的真实导入路径——看 `app/models/ai.py` 顶部照抄(可能是 `from app.db.base import Base` 或 `from app.models.base import Base`)。

- [ ] **Step 2: 汇总导入**

`backend/app/models/__init__.py` 加一行(与现有风格一致):
```python
from app.models.selector import SelectorKey, SelectorScope  # noqa: F401
```

- [ ] **Step 3: 迁移函数**

`backend/app/db/migrate.py` 末尾加(参照现有 `ensure_*` 用 `text()` 的写法;`create_all` 已能建新表,但显式 `CREATE TABLE IF NOT EXISTS` 便于老库无需依赖 import 时机):
```python
def ensure_selector_tables(engine) -> None:
    """建 selector_key / selector_scope(幂等)。create_all 已覆盖,此处保证老库顺序无关。"""
    from app.models.selector import SelectorKey, SelectorScope
    SelectorKey.__table__.create(bind=engine, checkfirst=True)
    SelectorScope.__table__.create(bind=engine, checkfirst=True)
```

- [ ] **Step 4: init_db 调用**

`backend/app/main.py` 的 `init_db` 里,`create_all` 之后、其它 `ensure_*` 旁边加:
```python
from app.db.migrate import ensure_selector_tables
ensure_selector_tables(engine)
```
> 按该文件现有 import/调用风格放置(和 `ensure_ai_provider_columns` 一致)。

- [ ] **Step 5: schema.sql 补表**

`backend/sql/schema.sql` 末尾加(MySQL,`candidates` 用 TEXT):
```sql
CREATE TABLE IF NOT EXISTS `selector_key` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `project_id` INT NOT NULL,
  `sub_product` VARCHAR(32) NOT NULL DEFAULT '',
  `key` VARCHAR(64) NOT NULL,
  `frame` VARCHAR(8) NOT NULL DEFAULT 'auto',
  `desc` VARCHAR(255) NOT NULL DEFAULT '',
  `candidates` TEXT,
  `updated_by` INT DEFAULT NULL,
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_selkey_scope_key` (`project_id`,`sub_product`,`key`),
  KEY `idx_selkey_scope` (`project_id`,`sub_product`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `selector_scope` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `project_id` INT NOT NULL,
  `sub_product` VARCHAR(32) NOT NULL DEFAULT '',
  `vm_iframe` VARCHAR(255) NOT NULL DEFAULT '',
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_selscope` (`project_id`,`sub_product`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 6: 冒烟验证(删表→启动建表→插查)**

Run:
```bash
cd backend && .venv/bin/python -c "
from app.db.session import engine, SessionLocal
from app.db.migrate import ensure_selector_tables
from app.models import SelectorKey
ensure_selector_tables(engine)
s = SessionLocal()
s.add(SelectorKey(project_id=1, key='navTasks', frame='shell', desc='任务菜单', candidates='[{\"by\":\"text\",\"value\":\"任务\"}]'))
s.commit()
row = s.query(SelectorKey).filter_by(project_id=1, sub_product='', key='navTasks').one()
print('OK', row.id, row.key, row.candidates)
s.delete(row); s.commit(); s.close()
" 2>&1 | grep -Ev "Watch|reload|INFO:"
```
Expected: 打印 `OK <id> navTasks [...]`。

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/selector.py backend/app/models/__init__.py backend/app/db/migrate.py backend/app/main.py backend/sql/schema.sql
git commit -m "feat(selectors): selector_key/selector_scope 模型+迁移+schema"
```

---

## Task 2: 注册表服务层(merge 解析 + CRUD 辅助)

**Files:**
- Create: `backend/app/services/selectors.py`
- Test: 冒烟脚本(inline)

**Interfaces:**
- Consumes: `SelectorKey`, `SelectorScope`(Task 1)
- Produces:
  - `resolved_registry(db, project_id:int, sub_product:str="") -> dict` — 返回 `{"vmIframe": str, "registry": {key: {frame, desc, candidates:list}}, "version": str}`,已按 merge(共享∪子产品覆盖)。
  - `shared_key_dicts(db, project_id:int) -> list[dict]` — 项目级共享 keys,形如 `[{"key","frame","desc"}]`(供生成侧 prompt 注入,轻量不含 candidates)。
  - `shared_key_set(db, project_id:int) -> set[str]` — 项目级共享 key 名集合(供生成侧校验)。

- [ ] **Step 1: 写服务**

`backend/app/services/selectors.py`:
```python
"""选择器注册表服务:DB 是唯一事实来源。merge 规则=项目级共享(sub_product='') ∪
子产品专属,同名 key 子产品覆盖共享。生成侧/API/runner 都经此层读,口径一致。"""
import json
from sqlalchemy.orm import Session
from app.models import SelectorKey, SelectorScope


def _cands(raw: str) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def resolved_registry(db: Session, project_id: int, sub_product: str = "") -> dict:
    """合并后有效注册表(供 runner 消费)。子产品专属覆盖同名共享 key。"""
    rows = (
        db.query(SelectorKey)
        .filter(SelectorKey.project_id == project_id,
                SelectorKey.sub_product.in_(["", sub_product] if sub_product else [""]))
        .all()
    )
    # 先铺共享,再用子产品覆盖(按 sub_product 非空优先)
    reg: dict = {}
    ver = 0
    for r in sorted(rows, key=lambda x: x.sub_product == ""):  # '' 排前,专属排后覆盖
        reg[r.key] = {"frame": r.frame, "desc": r.desc, "candidates": _cands(r.candidates)}
        ver = max(ver, int(r.updated_at.timestamp()) if r.updated_at else 0)
    # vmIframe:子产品专属优先,回落共享
    scope = (
        db.query(SelectorScope)
        .filter(SelectorScope.project_id == project_id,
                SelectorScope.sub_product.in_(["", sub_product] if sub_product else [""]))
        .all()
    )
    vm = ""
    for sc in sorted(scope, key=lambda x: x.sub_product == ""):
        if sc.vm_iframe:
            vm = sc.vm_iframe
    return {"vmIframe": vm, "registry": reg, "version": str(ver)}


def shared_key_dicts(db: Session, project_id: int) -> list[dict]:
    rows = (db.query(SelectorKey)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return [{"key": r.key, "frame": r.frame, "desc": r.desc} for r in rows]


def shared_key_set(db: Session, project_id: int) -> set[str]:
    rows = (db.query(SelectorKey.key)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {r[0] for r in rows}
```

- [ ] **Step 2: 冒烟(共享+覆盖 merge)**

Run:
```bash
cd backend && .venv/bin/python -c "
from app.db.session import SessionLocal
from app.models import SelectorKey
from app.services import selectors as S
s = SessionLocal()
# 造 1 个共享 + 1 个桌面版覆盖同名 + 1 个桌面版专属
s.query(SelectorKey).filter(SelectorKey.project_id==999).delete()
s.add_all([
  SelectorKey(project_id=999, sub_product='', key='navTasks', frame='shell', desc='共享', candidates='[{\"by\":\"text\",\"value\":\"任务\"}]'),
  SelectorKey(project_id=999, sub_product='纳米Work桌面版', key='navTasks', frame='shell', desc='桌面覆盖', candidates='[{\"by\":\"css\",\"value\":\".d\"}]'),
  SelectorKey(project_id=999, sub_product='纳米Work桌面版', key='onlyDesk', frame='vm', desc='仅桌面', candidates='[]'),
])
s.commit()
r = S.resolved_registry(s, 999, '纳米Work桌面版')
assert r['registry']['navTasks']['desc']=='桌面覆盖', r['registry']['navTasks']
assert 'onlyDesk' in r['registry']
r2 = S.resolved_registry(s, 999, '纳米Work云端版')
assert r2['registry']['navTasks']['desc']=='共享'  # 云端版无专属→拿共享
assert 'onlyDesk' not in r2['registry']
assert S.shared_key_set(s, 999) == {'navTasks'}
s.query(SelectorKey).filter(SelectorKey.project_id==999).delete(); s.commit(); s.close()
print('OK merge/覆盖/共享隔离 正确')
" 2>&1 | grep -Ev "Watch|reload|INFO:"
```
Expected: `OK merge/覆盖/共享隔离 正确`。

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/selectors.py
git commit -m "feat(selectors): 注册表服务层(merge 解析 + 生成侧读取辅助)"
```

---

## Task 3: 管理 API(增删改查 + scope)

**Files:**
- Create: `backend/app/schemas/selector.py`
- Create: `backend/app/api/selectors.py`
- Modify: `backend/app/main.py`(注册 router)

**Interfaces:**
- Consumes: `resolved_registry`/服务层(Task 2);`assert_project_role`
- Produces(路由,全部 `prefix="/api/selectors"`):
  - `GET /api/selectors/manage?project_id=` → `{shared:[...], by_sub:{sub_product:[...]}}`
  - `POST /api/selectors`(body:project_id, sub_product, key, frame, desc, candidates:list)→ 新 key dict
  - `PATCH /api/selectors/{id}`(body:frame?, desc?, candidates?)→ 更新后 dict
  - `DELETE /api/selectors/{id}` → `{deleted:id}`
  - `PUT /api/selectors/scope`(body:project_id, sub_product, vm_iframe)→ scope dict

- [ ] **Step 1: schema**

`backend/app/schemas/selector.py`:
```python
from pydantic import BaseModel, Field
from typing import Any


class SelectorKeyIn(BaseModel):
    project_id: int
    sub_product: str = ""
    key: str = Field(min_length=1, max_length=64)
    frame: str = "auto"
    desc: str = ""
    candidates: list[dict[str, Any]] = []


class SelectorKeyPatch(BaseModel):
    frame: str | None = None
    desc: str | None = None
    candidates: list[dict[str, Any]] | None = None


class SelectorScopeIn(BaseModel):
    project_id: int
    sub_product: str = ""
    vm_iframe: str = ""
```

- [ ] **Step 2: 路由**

`backend/app/api/selectors.py`(参照 `app/api/exec_queue.py` 的信封/rbac/`_to_out` 风格):
```python
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import SelectorKey, SelectorScope, User
from app.schemas.common import ok
from app.schemas.selector import SelectorKeyIn, SelectorKeyPatch, SelectorScopeIn
from app.api.release import SUB_PRODUCTS  # 复用子产品白名单

router = APIRouter(prefix="/api/selectors", tags=["selectors"])
_RW = (ProjectRole.admin, ProjectRole.member)


def _valid_sub(v: str) -> str:
    if v and v not in SUB_PRODUCTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="子产品取值非法")
    return v or ""


def _key_out(r: SelectorKey) -> dict:
    return {"id": r.id, "project_id": r.project_id, "sub_product": r.sub_product,
            "key": r.key, "frame": r.frame, "desc": r.desc,
            "candidates": json.loads(r.candidates or "[]"),
            "updated_by": r.updated_by,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/manage")
def manage(project_id: int = Query(...), db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, _RW)
    rows = db.query(SelectorKey).filter(SelectorKey.project_id == project_id).order_by(SelectorKey.key).all()
    shared, by_sub = [], {}
    for r in rows:
        (shared if r.sub_product == "" else by_sub.setdefault(r.sub_product, [])).append(_key_out(r))
    return ok({"shared": shared, "by_sub": by_sub})


@router.post("")
def create_key(body: SelectorKeyIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _RW)
    sub = _valid_sub(body.sub_product)
    exists = (db.query(SelectorKey)
              .filter(SelectorKey.project_id == body.project_id,
                      SelectorKey.sub_product == sub, SelectorKey.key == body.key).first())
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"该作用域下 key「{body.key}」已存在")
    r = SelectorKey(project_id=body.project_id, sub_product=sub, key=body.key.strip(),
                    frame=body.frame or "auto", desc=body.desc or "",
                    candidates=json.dumps(body.candidates, ensure_ascii=False),
                    updated_by=user.id, updated_at=datetime.utcnow())
    db.add(r); db.commit(); db.refresh(r)
    return ok(_key_out(r))


@router.patch("/{kid}")
def patch_key(kid: int, body: SelectorKeyPatch, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    if body.frame is not None: r.frame = body.frame
    if body.desc is not None: r.desc = body.desc
    if body.candidates is not None: r.candidates = json.dumps(body.candidates, ensure_ascii=False)
    r.updated_by = user.id; r.updated_at = datetime.utcnow()
    db.commit(); db.refresh(r)
    return ok(_key_out(r))


@router.delete("/{kid}")
def delete_key(kid: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    db.delete(r); db.commit()
    return ok({"deleted": kid})


@router.put("/scope")
def set_scope(body: SelectorScopeIn, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _RW)
    sub = _valid_sub(body.sub_product)
    sc = (db.query(SelectorScope)
          .filter(SelectorScope.project_id == body.project_id, SelectorScope.sub_product == sub).first())
    if not sc:
        sc = SelectorScope(project_id=body.project_id, sub_product=sub)
        db.add(sc)
    sc.vm_iframe = body.vm_iframe or ""
    sc.updated_at = datetime.utcnow()
    db.commit(); db.refresh(sc)
    return ok({"id": sc.id, "project_id": sc.project_id, "sub_product": sc.sub_product, "vm_iframe": sc.vm_iframe})
```

- [ ] **Step 3: 注册 router**

`backend/app/main.py` 里,和其它 `app.include_router(...)` 一起加:
```python
from app.api import selectors as selectors_api
app.include_router(selectors_api.router)
```

- [ ] **Step 4: 冒烟(TestClient 走 CRUD)**

Run:
```bash
cd backend && .venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import SessionLocal
from app.models import User
c = TestClient(app)
# 用种子管理员登录(SEED_ADMIN);按 auth 接口拿 token
r = c.post('/api/auth/login', json={'username':'lixueyuan','password':'lixueyuan123'})
tok = r.json()['data']['access_token']; H={'Authorization':f'Bearer {tok}'}
# 建 key
r = c.post('/api/selectors', json={'project_id':1,'key':'navTasks','frame':'shell','desc':'任务','candidates':[{'by':'text','value':'任务'}]}, headers=H)
print('create', r.status_code, r.json()['code'])
kid = r.json()['data']['id']
# manage 列表
r = c.get('/api/selectors/manage?project_id=1', headers=H); print('manage', r.json()['data']['shared'][0]['key'])
# patch
r = c.patch(f'/api/selectors/{kid}', json={'desc':'任务菜单'}, headers=H); print('patch', r.json()['data']['desc'])
# delete
r = c.delete(f'/api/selectors/{kid}', headers=H); print('delete', r.json()['data'])
" 2>&1 | grep -Ev "Watch|reload|INFO:"
```
Expected: `create 200 0` / `manage navTasks` / `patch 任务菜单` / `delete {'deleted': ...}`。
> 若登录账号/密码不同,改成 `.env` 的 `SEED_ADMIN_USERNAME/PASSWORD`。

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/selector.py backend/app/api/selectors.py backend/app/main.py
git commit -m "feat(selectors): 注册表管理 API(增删改查 + scope)"
```

---

## Task 4: runner 取用注册表 API + 导入旧注册表

**Files:**
- Modify: `backend/app/api/selectors.py`(加 2 个路由)

**Interfaces:**
- Consumes: `resolved_registry`(Task 2);`require_runner_ctx`
- Produces:
  - `GET /api/selectors?project_id=&sub_product=` (runner token) → `resolved_registry` 结果
  - `POST /api/selectors/import-legacy?project_id=` (user, 项目 admin) → `{imported:int, skipped:int}`

- [ ] **Step 1: 加导入路由 + runner 路由**

在 `backend/app/api/selectors.py` 顶部补导入:
```python
import os
from app.core.deps import require_runner_ctx, RunnerCtx
from app.services.selectors import resolved_registry
```
追加路由:
```python
@router.get("")
def resolved(project_id: int = Query(...), sub_product: str = Query(""),
             db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    """runner 拉合并后有效注册表(runner token 鉴权)。"""
    return ok(resolved_registry(db, project_id, _valid_sub(sub_product)))


# 内置旧注册表路径(仓库内 selectors.json),供一次性导入
_LEGACY = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "tools", "qalab-runner", "gui-mcp", "selectors.json"))


@router.post("/import-legacy")
def import_legacy(project_id: int = Query(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """把内置 selectors.json 导入为该项目【项目级共享】。幂等:同名 key 跳过。仅项目 admin。"""
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    try:
        with open(_LEGACY, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"读取内置注册表失败:{e}")
    reg = data.get("registry", {})
    have = {k[0] for k in db.query(SelectorKey.key).filter(
        SelectorKey.project_id == project_id, SelectorKey.sub_product == "").all()}
    imported = skipped = 0
    for k, v in reg.items():
        if k in have:
            skipped += 1; continue
        db.add(SelectorKey(project_id=project_id, sub_product="", key=k,
                           frame=v.get("frame", "auto"), desc=v.get("desc", ""),
                           candidates=json.dumps(v.get("candidates", []), ensure_ascii=False),
                           updated_by=user.id, updated_at=datetime.utcnow()))
        imported += 1
    # vmIframe 写入共享 scope
    vm = data.get("vmIframe", "")
    if vm:
        sc = (db.query(SelectorScope).filter(SelectorScope.project_id == project_id,
                                             SelectorScope.sub_product == "").first())
        if not sc:
            sc = SelectorScope(project_id=project_id, sub_product=""); db.add(sc)
        sc.vm_iframe = vm; sc.updated_at = datetime.utcnow()
    db.commit()
    return ok({"imported": imported, "skipped": skipped})
```
> **实现时删掉上面 `have` 的第一段错误写法**,只保留第二行正确的集合推导(计划里留痕提醒:第一段是笔误)。

- [ ] **Step 2: 冒烟(导入 57 key 幂等 + runner token 取回)**

Run:
```bash
cd backend && .venv/bin/python -c "
from fastapi.testclient import TestClient
from app.main import app
import os
c = TestClient(app)
r = c.post('/api/auth/login', json={'username':'lixueyuan','password':'lixueyuan123'})
tok = r.json()['data']['access_token']; H={'Authorization':f'Bearer {tok}'}
r = c.post('/api/selectors/import-legacy?project_id=1', headers=H)
print('import#1', r.json()['data'])   # imported=57, skipped=0
r = c.post('/api/selectors/import-legacy?project_id=1', headers=H)
print('import#2', r.json()['data'])   # imported=0, skipped=57(幂等)
# runner token 取合并注册表(RUNNER_TOKEN 见 .env;为空则该测试跳过)
rt = os.environ.get('RUNNER_TOKEN','')
if rt:
    r = c.get('/api/selectors?project_id=1', headers={'X-Runner-Token': rt})
    print('resolved keys#', len(r.json()['data']['registry']))
" 2>&1 | grep -Ev "Watch|reload|INFO:"
```
Expected: `import#1 {'imported': 57, 'skipped': 0}` → `import#2 {'imported': 0, 'skipped': 57}`。
> runner token 的请求头名以 `app/core/deps.py::require_runner_ctx` 实际读取为准(可能是 `X-Runner-Token` 或 query,照该处改)。

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/selectors.py
git commit -m "feat(selectors): runner 取注册表 API + 一键导入旧注册表"
```

---

## Task 5: 生成侧改读 DB(prompt 注入 + key 校验权威化)

**Files:**
- Modify: `backend/app/services/claude_runner.py`(`build_testcase_prompt`/`parse_testcases`/`_registered_keys` 加 `project_id`)
- Modify: `backend/app/services/generators/deepseek_runner.py`(透传)
- Modify: `backend/app/api/ai.py`(`gen_testcases`/`gen_script` 传 project_id)
- Modify: `backend/app/services/generators/__init__.py`(接口注释更新,可选)

**Interfaces:**
- Consumes: `shared_key_dicts`/`shared_key_set`(Task 2)
- Produces(签名变更):
  - `build_testcase_prompt(requirement:str, project_id:int|None=None) -> str`
  - `parse_testcases(raw:str, project_id:int|None=None) -> list[dict]`
  - `stream_generate(requirement, project_id=None, timeout=None)`(两引擎)
  - `generate_script(kind, title, steps, expected, project_id=None, timeout=None)`(两引擎)

- [ ] **Step 1: claude_runner 读 DB**

改 `_load_selector_keys()` → 接受 `project_id`,走服务层(需要 DB session:内部开 `SessionLocal`,因为生成器脱离请求 db)。`backend/app/services/claude_runner.py`:
```python
def _load_selector_keys(project_id: int | None = None) -> list[dict]:
    """项目级共享 key 清单(供 prompt 注入)。project_id 为空或读不到 → 空列表(不注入)。"""
    if not project_id:
        return []
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_dicts
    s = SessionLocal()
    try:
        return shared_key_dicts(s, project_id)
    except Exception:
        logger.warning("读注册表失败(project_id=%s),prompt 不注入 key 清单", project_id)
        return []
    finally:
        s.close()


def _registered_keys(project_id: int | None = None) -> set[str]:
    if not project_id:
        return set()
    from app.db.session import SessionLocal
    from app.services.selectors import shared_key_set
    s = SessionLocal()
    try:
        return shared_key_set(s, project_id)
    except Exception:
        return set()
    finally:
        s.close()
```
把 `build_testcase_prompt(requirement)` 改签名为 `build_testcase_prompt(requirement, project_id=None)`,内部 `keys = _load_selector_keys(project_id)`;`build_script_prompt(...)` 同理加 `project_id` 传给 `_load_selector_keys`。`parse_testcases(raw)` → `parse_testcases(raw, project_id=None)`,内部 `valid_keys = _registered_keys(project_id)`。`stream_generate`/`generate_script` 加 `project_id` 参数并向下透传(prompt 与 parse)。

- [ ] **Step 2: deepseek_runner 透传**

`backend/app/services/generators/deepseek_runner.py`:`stream_generate`/`generate_script` 加 `project_id` 参数;调用 `build_testcase_prompt(requirement, project_id)`、`parse_testcases`(注:deepseek 的落库解析在 ai.py 侧,见下)、`build_script_prompt(kind,...,project_id)`、`_validate_script(arr, _registered_keys(project_id))`。

- [ ] **Step 3: ai.py 传 project_id**

`backend/app/api/ai.py`:
- `gen_testcases` 的 SSE 生成器里 `engine.stream_generate(requirement)` → `engine.stream_generate(requirement, project_id=project_id)`;落库处 `engine.parse_testcases(raw)` → `engine.parse_testcases(raw, project_id=project_id)`。
- `gen_script` 里 `engine.generate_script(kind, tc.title, tc.steps or "", tc.expected or "")` → 末尾加 `project_id=tc.project_id`。

- [ ] **Step 4: 冒烟(DB 有 key 才注入/校验)**

Run:
```bash
cd backend && .venv/bin/python -c "
from app.services import claude_runner as cr
# project 1 已导入 57 共享 key(Task4);未传 project_id 时不注入
p0 = cr.build_testcase_prompt('x')            # 无 project → 无 key 清单
p1 = cr.build_testcase_prompt('x', 1)         # project1 → 注入清单
assert '可用语义 key 清单' not in p0
assert 'loginUserName' in p1                  # 57 key 里的
# 校验:project1 里未注册的 key → 降级 manual
raw = '[{\"title\":\"t\",\"kind\":\"gui\",\"category\":\"功能\",\"priority\":\"P1\",\"steps\":\"s\",\"expected\":\"e\",\"script\":[{\"action\":\"connect\"},{\"action\":\"assert_visible\",\"target\":{\"key\":\"__fake__\"}}]}]'
c = cr.parse_testcases(raw, 1)
assert c[0]['kind']=='manual'
print('OK 生成侧读DB:有project注入清单、未注册key降级')
" 2>&1 | grep -Ev "Watch|reload|INFO:"
```
Expected: `OK 生成侧读DB:有project注入清单、未注册key降级`。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/claude_runner.py backend/app/services/generators/deepseek_runner.py backend/app/api/ai.py
git commit -m "feat(selectors): 生成侧改读 DB 注册表(prompt 注入 + key 校验权威化)"
```

---

## Task 6: runner 从 API 拉注册表(回落内置文件)

**Files:**
- Modify: `tools/qalab-runner/gui-mcp/gui-core.mjs`(接受 registry 对象 + `setRegistry`)
- Modify: `tools/qalab-runner/runner.mjs`(fetchRegistry + 执行前 setRegistry)
- Modify: `backend/app/api/exec_queue.py`(payload 补 `project_id`)

**Interfaces:**
- Consumes: `GET /api/selectors`(Task 4)
- Produces:
  - `createGuiCore({..., registry?, vmIframe?})` — 传入则用之,否则 readFileSync 内置文件
  - `guiCore.setRegistry(registry:object, vmIframe:string)` — 就地换注册表
  - exec payload 增 `project_id`

- [ ] **Step 1: gui-core 支持注入 registry**

`tools/qalab-runner/gui-mcp/gui-core.mjs`:把 `const { registry: REGISTRY, vmIframe: VM_IFRAME } = JSON.parse(readFileSync(...))` 改为 `let`,并优先用 opts:
```javascript
let REGISTRY, VM_IFRAME;
if (opts.registry) {
  REGISTRY = opts.registry; VM_IFRAME = opts.vmIframe || "";
} else {
  const j = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8"));
  REGISTRY = j.registry; VM_IFRAME = j.vmIframe;
}
```
在返回对象里加方法:
```javascript
setRegistry(registry, vmIframe) { REGISTRY = registry || {}; VM_IFRAME = vmIframe || VM_IFRAME; },
```
> `resolveKey`/`isKeyVisible`/`scopesFor`/`contentFrame` 已闭包引用 `REGISTRY`/`VM_IFRAME`,改 `let` 后 setRegistry 生效。

- [ ] **Step 2: runner fetchRegistry + 执行前换入**

`tools/qalab-runner/runner.mjs`:加缓存拉取(靠近 `fetchPending` 等 api 封装处):
```javascript
const _regCache = new Map();  // `${project_id}|${sub}` -> {version, data}
async function fetchRegistry(projectId, sub = "") {
  if (!projectId) return null;
  const ck = `${projectId}|${sub}`;
  try {
    const res = await api("GET", `/api/selectors?project_id=${projectId}&sub_product=${encodeURIComponent(sub)}`);
    const data = res?.data || res;  // api() 若已解包则直接是 data
    _regCache.set(ck, data);
    return data;
  } catch (e) {
    log(`拉注册表失败(${ck}):${e.message};回落内置文件`);
    return _regCache.get(ck) || null;   // 有缓存用缓存,否则 null→gui-core 用内置文件
  }
}
```
在 gui/e2e 执行分支里、`runScript(guiCore, ...)` 之前:
```javascript
const reg = await fetchRegistry(item.payload.project_id, "");
if (reg && reg.registry) guiCore.setRegistry(reg.registry, reg.vmIframe);
```
> `api()` 是否自动解包信封,以 runner.mjs 现有 `fetchPending` 用法为准,`data` 取法对齐。

- [ ] **Step 3: exec payload 补 project_id**

`backend/app/api/exec_queue.py::_payload_of` 返回 dict 里加 `"project_id": tc.project_id`(tc 存在时)。

- [ ] **Step 4: 冒烟(gui-core setRegistry 生效)**

Run:
```bash
cd tools/qalab-runner && node -e "
import('./gui-mcp/gui-core.mjs').then(({createGuiCore})=>{
  const g = createGuiCore({ registry: { foo: {frame:'shell',desc:'x',candidates:[{by:'text',value:'A'}]} }, vmIframe:'iframe#x' });
  console.log('init has foo:', 'foo' in g.registry);
  g.setRegistry({ bar: {frame:'vm',desc:'y',candidates:[]} }, 'iframe#y');
  console.log('after setRegistry has bar:', 'bar' in g.registry, 'foo gone:', !('foo' in g.registry));
});
"
```
Expected: `init has foo: true` / `after setRegistry has bar: true foo gone: true`。
> 需要 gui-core 暴露 `get registry()`(已存在)。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/gui-mcp/gui-core.mjs tools/qalab-runner/runner.mjs backend/app/api/exec_queue.py
git commit -m "feat(selectors): runner 从 API 拉注册表(回落内置文件)+ exec payload 补 project_id"
```

---

## Task 7: 前端「选择器管理」页(不含探测)

**Files:**
- Create: `frontend/src/views/SelectorAdmin.vue`
- Modify: `frontend/src/api/index.js`(接口封装)
- Modify: `frontend/src/router/index.js`(路由 + meta)
- Modify: 菜单组件(参照 `ReleaseNotes` 菜单项加法)

**Interfaces:**
- Consumes: Task 3/4 的接口
- Produces: 页面 `/selectors`;`api/index.js` 导出 `listSelectors/createSelector/patchSelector/deleteSelector/setSelectorScope/importLegacySelectors`

- [ ] **Step 1: api 封装**

`frontend/src/api/index.js` 追加(沿用现有 `http` 用法,返回已解包 data):
```javascript
export const listSelectors = (project_id) => http.get('/selectors/manage', { params: { project_id } })
export const createSelector = (body) => http.post('/selectors', body)
export const patchSelector = (id, body) => http.patch(`/selectors/${id}`, body)
export const deleteSelector = (id) => http.delete(`/selectors/${id}`)
export const setSelectorScope = (body) => http.put('/selectors/scope', body)
export const importLegacySelectors = (project_id) => http.post('/selectors/import-legacy', null, { params: { project_id } })
```

- [ ] **Step 2: 页面**

`frontend/src/views/SelectorAdmin.vue`——顶部项目选择(复用 `useAppStore().fetchProjects`)+ 子产品选择(常量 `['','纳米Work云端版',...]`,`''` 显示「项目级共享」);表格列 key/frame/desc/候选数,行内编辑 desc/frame、删除;顶部「导入内置纳米Work注册表」按钮(仅项目 admin 可见,调 `importLegacySelectors`);「新增 key」弹窗(key/frame/desc/candidates JSON 文本域)。子产品枚举常量须与后端 `SUB_PRODUCTS` 一致(参照 `ReleaseNotes.vue` 的 `SUB_PRODUCTS`)。
> 结构参照现有 `frontend/src/views/CaseLibrary.vue`(项目选择 + 表格 + 弹窗)的写法,保持风格一致。

- [ ] **Step 3: 路由 + 菜单**

`frontend/src/router/index.js` 加:
```javascript
{ path: '/selectors', name: 'selectors', component: () => import('@/views/SelectorAdmin.vue'),
  meta: { title: '选择器管理' } }
```
菜单:参照 `ReleaseNotes` 菜单项的位置与写法加一项「选择器管理」。

- [ ] **Step 4: 构建校验**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: 构建成功(无语法错误)。

- [ ] **Step 5: 手动端到端**

启动前后端:选项目 → 点「导入」→ 列表出现 57 个共享 key → 新增一个 key → 去 AI 测试助手对同项目生成 gui 用例,确认新 key 能被采用(不再降级)。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/SelectorAdmin.vue frontend/src/api/index.js frontend/src/router/index.js frontend/src/<菜单文件>
git commit -m "feat(selectors): 前端选择器管理页(浏览/增删改 + 导入旧注册表)"
```

---

# 阶段二:设备探测

## Task 8: probe_request 模型 + 迁移 + schema

**Files:**
- Modify: `backend/app/models/selector.py`(加 `ProbeRequest`)
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/db/migrate.py`(`ensure_selector_tables` 里加建表)
- Modify: `backend/sql/schema.sql`

**Interfaces:**
- Produces: `ProbeRequest`(表 `probe_request`):`id, project_id, sub_product, runner, status, params:TEXT, result:TEXT|None, error:str|None, created_by, created_at, updated_at`

- [ ] **Step 1: 模型**（追加到 `selector.py`）
```python
class ProbeRequest(Base):
    __tablename__ = "probe_request"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    runner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    params: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 2**: `__init__.py` 加 `ProbeRequest`;`ensure_selector_tables` 里加 `ProbeRequest.__table__.create(bind=engine, checkfirst=True)`。

- [ ] **Step 3**: `schema.sql` 加 `probe_request` 表(`params`/`result` 用 TEXT,`status` 默认 'pending')。

- [ ] **Step 4: 冒烟**:`.venv/bin/python -c` 建表 + 插一行 pending + 查(同 Task1 Step6 模式)。

- [ ] **Step 5: Commit** `feat(probe): probe_request 模型+迁移+schema`

---

## Task 9: 探测 API(网页发起/轮询 + runner 拉取/回写)

**Files:**
- Create: `backend/app/api/probe.py`
- Create: `backend/app/schemas/probe.py`
- Modify: `backend/app/main.py`(注册 router)

**Interfaces:**
- Produces(`prefix="/api/probe"`):
  - `POST /api/probe`(user admin/member;body:project_id, sub_product, runner, params)→ `{id}`
  - `GET /api/probe/{id}`(user)→ probe dict(status/result/error)
  - `GET /api/probe/pending?runner=`(runner token)→ `[probe dict]`(pending,认领即置 running)
  - `PATCH /api/probe/{id}`(runner token;body:result?/error?)→ probe dict

- [ ] **Step 1: schema** `backend/app/schemas/probe.py`:
```python
from pydantic import BaseModel
from typing import Any
class ProbeStartIn(BaseModel):
    project_id: int
    sub_product: str = ""
    runner: str
    params: dict[str, Any] = {}
class ProbeReportIn(BaseModel):
    result: dict[str, Any] | None = None
    error: str | None = None
```

- [ ] **Step 2: 路由** `backend/app/api/probe.py`（信封/rbac 同 exec_queue;`_to_out` 手写,`params`/`result` `json.loads`）。核心:
  - `POST`:`assert_project_role(_RW)` → 建 `ProbeRequest(status='pending', params=json.dumps(...))` → `ok({'id':r.id})`。
  - `GET/{id}`:`assert_project_role`(按 r.project_id, all roles)→ `ok(_to_out(r))`。
  - `GET/pending`:`require_runner_ctx`;设备 token 锁 `runner=ctx.device.runner_id`;取该 runner 的 pending,置 running,返回 `[_to_out]`(带解析后的 params)。
  - `PATCH/{id}`:`require_runner_ctx` + 归属校验;写 result(`json.dumps`)/error,status→done/failed。

- [ ] **Step 3: 注册 router**(main.py)。

- [ ] **Step 4: 冒烟**(TestClient:user POST 建 probe → runner-token GET pending 拿到并转 running → runner-token PATCH 回写 result → user GET 见 done+result)。

- [ ] **Step 5: Commit** `feat(probe): 探测 API(发起/轮询/runner 拉取回写)`

---

## Task 10: runner 探测循环

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`(主循环并列轮询 probe)

**Interfaces:**
- Consumes: Task 9 接口;`fetchRegistry`(Task 6);`guiCore.probe`/`setRegistry`

- [ ] **Step 1: probe 拉取/回写封装 + 循环**（靠近 exec 轮询处）:
```javascript
const fetchProbes = () => api("GET", `/api/probe/pending?runner=${encodeURIComponent(RUNNER_ID)}`);
const reportProbe = (id, r) => api("PATCH", `/api/probe/${id}?runner=${encodeURIComponent(RUNNER_ID)}`, r);

async function handleProbes() {
  let list = [];
  try { const res = await fetchProbes(); list = res?.data || res || []; } catch { return; }
  for (const p of list) {
    try {
      const reg = await fetchRegistry(p.project_id, p.sub_product || "");
      if (reg && reg.registry) guiCore.setRegistry(reg.registry, reg.vmIframe);
      const out = await guiCore.probe(p.params || {});
      await reportProbe(p.id, { result: out });
    } catch (e) {
      await reportProbe(p.id, { error: String(e.message || e) });
    }
  }
}
```
在主循环里 exec 轮询之后调用 `await handleProbes();`。

- [ ] **Step 2: 冒烟**:`--dry` 或本地起 runner + 后端,POST 一个 probe,确认 runner 回写(需真实设备连 CDP 才有 groups;无设备时 gui.probe 抛错→回写 error,链路仍验证通)。

- [ ] **Step 3: Commit** `feat(probe): runner 探测循环(setRegistry + gui.probe 回写)`

---

## Task 11: 前端探测面板(并入选择器管理页)

**Files:**
- Modify: `frontend/src/views/SelectorAdmin.vue`(右侧探测面板)
- Modify: `frontend/src/api/index.js`(probe 封装)

**Interfaces:**
- Consumes: Task 9 接口;`listMyDevices`(现有)

- [ ] **Step 1: api 封装**
```javascript
export const startProbe = (body) => http.post('/probe', body)
export const getProbe = (id) => http.get(`/probe/${id}`)
```

- [ ] **Step 2: 面板**:选在线设备(`listMyDevices`)+ 可选关键词 → 「探测」调 `startProbe` 拿 id → `setInterval` 轮询 `getProbe(id)` 直到 status done/failed(设 60s 超时提示)→ 渲染 `result.groups`(按 shell/vm 分组,列元素 text + best 候选 by/value)→ 每个元素「加为 key」弹窗预填(key 名待填、frame=组名、candidates=[best])→ 调 `createSelector`(落当前所选作用域)→ 成功刷新左侧列表。

- [ ] **Step 3: 构建校验** `npm run build`。

- [ ] **Step 4: 手动端到端**:真实设备打开被测客户端(CDP 端口)→ 网页选该设备探测 → 看到当前页元素 → 加为 key → 左侧出现 → 生成用例可用。

- [ ] **Step 5: Commit** `feat(probe): 前端探测面板(选设备→探测→加为 key)`

---

## Self-Review 记录(计划自查)

- **Spec 覆盖**:§3 作用域→Task2;§4 模型→Task1/8;§5 API→Task3/4;§6 探测→Task8/9/10/11;§7 runner→Task6;§8 生成侧→Task5;§10 前端→Task7/11;§11 迁移 seed→Task1/4。全覆盖。
- **占位扫描**:Task4 Step1 的 `have` 有一段**故意留痕的笔误**,已在其下注明"实现时删第一段、留正确集合推导"——执行者须照做。其余无 TODO/占位。
- **类型一致**:`resolved_registry` 返回 `{vmIframe,registry,version}` 在 Task2/4/6/10 一致;`setRegistry(registry,vmIframe)` 在 Task6 定义、Task10 使用一致;`shared_key_set/shared_key_dicts` 在 Task2 定义、Task5 使用一致;payload `project_id` Task6 写入、Task6/10 读取一致。

## 关键风险与提醒

- **runner `api()` 解包**:多处 `res?.data || res` 是防御写法——落地时统一按 runner.mjs 现有 `fetchPending` 的真实返回(是否已解包)校准一次,避免 double-unwrap。
- **runner token 传法**:Task4/9 的 runner 接口鉴权头/参数以 `app/core/deps.py::require_runner_ctx` 实际读取为准。
- **生成器内开 SessionLocal**:Task5 生成侧脱离请求 db,内部自开 `SessionLocal` 并 `close()`,勿复用请求 session。
- **阶段边界**:阶段一(Task1-7)完成即"注册表单源"可独立发布(生成+执行双方都读 DB);阶段二(Task8-11)叠加探测。
