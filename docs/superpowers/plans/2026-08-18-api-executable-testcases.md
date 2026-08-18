# 可执行 API 测试用例 — P1 实施计划(执行地基)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 api 类型用例走**结构化 script + 确定性执行器**(Node 原生 fetch),不再靠 LLM 即兴拼 curl;本计划交付执行地基,手写一条 api script 即可确定性跑通。

**Architecture:** 新增项目级 `api_env` 表(base_url/鉴权/契约);下发时把该项目 api 配置快照进 payload;runner 新增 `api-executor.mjs` 逐步执行请求-断言-提取原子(变量表在用例内传递、cleanup 尽力而为);`runner.mjs` 对 `kind==api` 且有合法 script 的用例改走确定性执行器,否则回落现有 LLM 兜底。不改数据库 `test_case` 结构(api script 复用现有 `script` TEXT 字段)、不改 exec_queue 调度。

**Tech Stack:** 后端 FastAPI + SQLAlchemy 2.0(Python);runner 为纯 Node(v18+ 内置 fetch/test,无第三方依赖);数据 TEXT 存 JSON(兼容 MySQL 5.6 无原生 JSON)。

**Spec:** `docs/superpowers/specs/2026-08-18-api-executable-testcases-design.md`

## Global Constraints

- **响应信封**:后端所有接口用 `app/schemas/common.py` 的 `ok(data)`/`fail(code,msg)`,`code==0` 成功;手写 `_to_out` 序列化(不用 response_model)。
- **两级 RBAC**:改配置需项目 admin,用 `assert_project_role(db, user, project_id, (ProjectRole.admin,))`;runner 拉取用 `require_runner_ctx`(runner token)。
- **三处 schema 手动同步**:新增/改表须同步 ①SQLAlchemy 模型 `app/models/` ②`backend/sql/schema.sql` ③`app/db/migrate.py` 的 `ensure_*`;模型须在 `app/models/__init__.py` 汇总导入,`create_all` 才建表。
- **JSON 存 TEXT**:所有结构化字段(auth/contract/script)以 JSON 字符串存 TEXT 列,兼容 MySQL 5.6。
- **无测试框架**:项目无 pytest/eslint。本计划纯函数测试用 **Node 内置 `node:test`**(零依赖,`node --test`);后端逻辑用**独立 Python 断言脚本**自测(不引 pytest);集成用手动端到端。**不臆造 pytest/eslint 命令**。
- **verdict 契约**:执行器返回对象须含 `{verdict:"pass"|"fail", reason, evidence, duration_ms}`,与 `runner.mjs` 现有回写零改动对齐;需降级时返回 `{needClaude:true, reason}`(镜像 `step-executor.mjs`)。
- **变量/路径语法**:模板占位 `{{var}}`;取值/断言路径用**点路径**(如 `data.list.0.id`),自实现,无 JSONPath 依赖。

---

## 文件结构(P1)

| 文件 | 职责 | 动作 |
|---|---|---|
| `backend/app/models/api_env.py` | `ApiEnv` 模型:项目级 api 环境(base_url/auth/contract) | Create |
| `backend/app/models/__init__.py` | 汇总导入 `ApiEnv` | Modify |
| `backend/app/db/migrate.py` | `ensure_api_env_table()` 幂等建表 | Modify |
| `backend/app/main.py` | startup 调 `ensure_api_env_table()`;注册 api_env 路由 | Modify |
| `backend/sql/schema.sql` | `api_env` DDL(MySQL/docker 初始化) | Modify |
| `backend/app/services/api_env.py` | `get_api_env(db, project_id)` 读配置 → dict/None | Create |
| `backend/app/api/api_env.py` | GET 读 / PUT 存 项目 api 配置(admin) | Create |
| `backend/app/api/exec_queue.py` | `_payload_of` 对 api 用例注入 api_env 快照 | Modify(38-62,128) |
| `tools/qalab-runner/api-executor.mjs` | 确定性 api 执行器(纯函数 + run) | Create |
| `tools/qalab-runner/api-executor.test.mjs` | 纯函数单测(node:test) | Create |
| `tools/qalab-runner/runner.mjs` | api 分流接入 | Modify(425-426) |

---

## Task 1: ApiEnv 数据模型 + 迁移 + schema

**Files:**
- Create: `backend/app/models/api_env.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/db/migrate.py`(仿 `ensure_selector_tables` 287-299)
- Modify: `backend/app/main.py`(startup 迁移调用处,仿第 35 行 `ensure_exec_run_kind()`)
- Modify: `backend/sql/schema.sql`
- Test: `backend/scripts/test_api_env_model.py`(独立断言脚本)

**Interfaces:**
- Produces: `ApiEnv` ORM(`__tablename__="api_env"`),列:`id, project_id(unique), base_url:str, auth_type:str('fixed'|'login'), auth_json:Text, contract:Text, updated_by:int|None, updated_at:datetime`。
- Produces: `ensure_api_env_table()` — 幂等建 `api_env` 表。

- [ ] **Step 1: 写模型**

`backend/app/models/api_env.py`(仿 `app/models/selector.py` 的 `SelectorScope`):

```python
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ApiEnv(Base):
    """项目级 api 测试环境（被测业务系统的 base_url + 鉴权 + 接口契约）。

    一个 project 一条（project_id 唯一）。auth_json/contract 用 TEXT 存 JSON 字符串
    （兼容 MySQL 5.6 无原生 JSON）。auth_type: fixed（固定 header/token，存 auth_json）
    或 login（token 由用例内登录步骤 extract，auth_json 存登录接口信息，可空）。
    contract: Swagger 导入结果 / 手写清单 / curl 解析累积，注入生成 prompt。
    """

    __tablename__ = "api_env"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_apienv_project"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    base_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    auth_type: Mapped[str] = mapped_column(String(16), default="fixed", server_default="fixed")
    auth_json: Mapped[str] = mapped_column(Text, default="{}")
    contract: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 2: 汇总导入**

`backend/app/models/__init__.py`:在 `from app.models.selector import ...` 后加一行,并加入 `__all__`:

```python
from app.models.api_env import ApiEnv
```
`__all__` 列表末尾加 `"ApiEnv",`。

- [ ] **Step 3: 加迁移函数**

`backend/app/db/migrate.py` 末尾追加(仿 `ensure_selector_tables` 287-299):

```python
def ensure_api_env_table(engine=None) -> None:
    """建 api_env 表(幂等)。create_all 已能建;此处显式 CREATE(checkfirst)保证
    老库无需依赖模型 import 时机也能补出该表(与 ensure_selector_tables 一致)。"""
    from app.db.session import engine as _default_engine
    from app.models.api_env import ApiEnv
    eng = engine if engine is not None else _default_engine
    ApiEnv.__table__.create(bind=eng, checkfirst=True)
```

- [ ] **Step 4: startup 调用迁移**

`backend/app/main.py`:在 import 迁移函数的那一行(第 14 行,`from app.db.migrate import ...`)末尾加 `, ensure_api_env_table`;并在 startup 里其它 `ensure_*()` 调用旁(第 35 行 `ensure_exec_run_kind()` 附近)加一行:

```python
    ensure_api_env_table()
```

- [ ] **Step 5: 同步 schema.sql**

`backend/sql/schema.sql`:在 selector 相关建表附近追加(风格对齐现有 CREATE TABLE):

```sql
CREATE TABLE IF NOT EXISTS api_env (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id   BIGINT NOT NULL,
  base_url     VARCHAR(255) NOT NULL DEFAULT '',
  auth_type    VARCHAR(16)  NOT NULL DEFAULT 'fixed',
  auth_json    TEXT,
  contract     TEXT,
  updated_by   BIGINT NULL,
  updated_at   DATETIME NULL,
  UNIQUE KEY uq_apienv_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 6: 写建表自测脚本**

`backend/scripts/test_api_env_model.py`(独立脚本,SQLite 内存库验证建表 + 唯一约束):

```python
"""api_env 模型自测:内存 SQLite 建表 + 唯一约束。运行: python -m scripts.test_api_env_model"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv

def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    assert "api_env" in Base.metadata.tables, "api_env 未注册到 metadata"
    with Session(eng) as s:
        s.add(ApiEnv(project_id=1, base_url="https://x", auth_type="fixed", auth_json="{}"))
        s.commit()
        row = s.query(ApiEnv).filter_by(project_id=1).one()
        assert row.base_url == "https://x"
        assert row.auth_type == "fixed"
    print("OK test_api_env_model")

if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 跑自测,确认通过**

Run: `cd backend && python -m scripts.test_api_env_model`
Expected: 打印 `OK test_api_env_model`,无 AssertionError。

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/api_env.py backend/app/models/__init__.py backend/app/db/migrate.py backend/app/main.py backend/sql/schema.sql backend/scripts/test_api_env_model.py
git commit -m "feat(api-exec): api_env 项目级环境表 + 迁移 + schema"
```

---

## Task 2: api_env 服务层 + 配置读写 API

**Files:**
- Create: `backend/app/services/api_env.py`
- Create: `backend/app/api/api_env.py`
- Modify: `backend/app/main.py`(注册路由)
- Test: `backend/scripts/test_api_env_service.py`

**Interfaces:**
- Consumes: `ApiEnv` 模型(Task 1)。
- Produces: `get_api_env(db, project_id) -> dict | None` — 返回 `{"base_url":str, "auth_type":str, "auth":dict, "contract":str|None}`,无配置返回 `None`。
- Produces: 路由 `GET /api/api-env?project_id=`(读)、`PUT /api/api-env`(admin 存),`{code,msg,data}` 信封。

- [ ] **Step 1: 写服务层读函数**

`backend/app/services/api_env.py`:

```python
"""api_env 服务:DB 单源读项目 api 环境。生成侧/下发侧/API 都经此层,口径一致。"""
import json
from sqlalchemy.orm import Session
from app.models import ApiEnv


def get_api_env(db: Session, project_id: int) -> dict | None:
    """读项目 api 环境。无配置返回 None。auth_json 解析失败按空 dict 兜底。"""
    row = db.query(ApiEnv).filter(ApiEnv.project_id == project_id).first()
    if not row:
        return None
    try:
        auth = json.loads(row.auth_json or "{}")
        if not isinstance(auth, dict):
            auth = {}
    except (json.JSONDecodeError, ValueError):
        auth = {}
    return {
        "base_url": row.base_url or "",
        "auth_type": row.auth_type or "fixed",
        "auth": auth,
        "contract": row.contract,
    }
```

- [ ] **Step 2: 写自测(服务层)**

`backend/scripts/test_api_env_service.py`:

```python
"""get_api_env 自测。运行: python -m scripts.test_api_env_service"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv
from app.services.api_env import get_api_env

def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        assert get_api_env(s, 1) is None, "无配置应返回 None"
        s.add(ApiEnv(project_id=1, base_url="https://x", auth_type="fixed",
                     auth_json=json.dumps({"headers": {"Authorization": "Bearer t"}})))
        s.commit()
        env = get_api_env(s, 1)
        assert env["base_url"] == "https://x"
        assert env["auth"]["headers"]["Authorization"] == "Bearer t"
        # auth_json 坏值兜底空 dict
        s.query(ApiEnv).filter_by(project_id=1).one().auth_json = "not-json"
        s.commit()
        assert get_api_env(s, 1)["auth"] == {}
    print("OK test_api_env_service")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 跑自测,确认失败(服务未写完时)→ 通过**

Run: `cd backend && python -m scripts.test_api_env_service`
Expected: 打印 `OK test_api_env_service`。

- [ ] **Step 4: 写 API 路由**

`backend/app/api/api_env.py`(仿 `app/api/selectors.py` 鉴权与信封):

```python
"""项目级 api 测试环境 API:读(runner/前端)、存(项目 admin)。
沿用 {code,msg,data} 信封、手写序列化。auth/contract 以 JSON 字符串存 TEXT。"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, require_runner_ctx, RunnerCtx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ApiEnv, User
from app.schemas.common import ok
from app.services.api_env import get_api_env

router = APIRouter(prefix="/api/api-env", tags=["api-env"])


@router.get("")
def read_env(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx | None = Depends(require_runner_ctx),
):
    """读项目 api 环境。runner token 或用户 JWT 均可读。无配置返回 null。"""
    return ok(get_api_env(db, project_id))


@router.put("")
def upsert_env(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """存项目 api 环境(项目 admin)。auth/contract 前端传对象/字符串,落库转 JSON 文本。"""
    project_id = int(body.get("project_id") or 0)
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    row = db.query(ApiEnv).filter(ApiEnv.project_id == project_id).first()
    auth = body.get("auth")
    auth_json = json.dumps(auth, ensure_ascii=False) if auth is not None else None
    now = datetime.utcnow()
    if row:
        if body.get("base_url") is not None:
            row.base_url = str(body.get("base_url"))
        if body.get("auth_type") is not None:
            row.auth_type = str(body.get("auth_type"))
        if auth_json is not None:
            row.auth_json = auth_json
        if body.get("contract") is not None:
            row.contract = str(body.get("contract"))
        row.updated_by = user.id
        row.updated_at = now
    else:
        row = ApiEnv(
            project_id=project_id,
            base_url=str(body.get("base_url") or ""),
            auth_type=str(body.get("auth_type") or "fixed"),
            auth_json=auth_json or "{}",
            contract=(str(body.get("contract")) if body.get("contract") is not None else None),
            updated_by=user.id, updated_at=now,
        )
        db.add(row)
    db.commit()
    return ok(get_api_env(db, project_id))
```

- [ ] **Step 5: 注册路由**

`backend/app/main.py`:找到其它 `app.include_router(...)` 处(如 selectors 路由注册旁),加:

```python
from app.api import api_env as api_env_router
app.include_router(api_env_router.router)
```
(按 main.py 现有 import/注册风格对齐;若采用集中 import 风格,则在对应位置追加。)

- [ ] **Step 6: 手动验证端点**

Run: `cd backend && uvicorn app.main:app --port 8000`(另开终端)
用管理员 JWT `PUT /api/api-env` 存一条(project_id/base_url/auth),再 `GET /api/api-env?project_id=` 读回,确认信封 `code==0` 且 data 正确。
(或在 Swagger `http://localhost:8000/docs` 操作。)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/api_env.py backend/app/api/api_env.py backend/app/main.py backend/scripts/test_api_env_service.py
git commit -m "feat(api-exec): api_env 服务层 + 配置读写 API"
```

---

## Task 3: 下发时注入 api_env 快照到 payload

**Files:**
- Modify: `backend/app/api/exec_queue.py`(`_payload_of` 38-62;调用处 128)
- Test: `backend/scripts/test_payload_api_env.py`

**Interfaces:**
- Consumes: `get_api_env(db, project_id)`(Task 2)、`_kind_of(tc)`(现有 30-35)。
- Produces: kind==api 的 payload 增加键 `"api_env": {"base_url","auth_type","auth"}`(不含 contract,执行不需要);非 api 用例不加此键。

- [ ] **Step 1: 改 `_payload_of` 签名并注入**

`backend/app/api/exec_queue.py`:把 `_payload_of(tc)` 改为 `_payload_of(tc, db)`,在 return 的 dict 后按 kind 追加 api_env 快照。替换 38-62 为:

```python
def _payload_of(tc: TestCase | None, db: Session) -> dict:
    """把用例快照成 runner/Claude 要用的 payload（steps/expected/title/params + 结构化 script）。

    api 用例额外带 api_env 快照（base_url/auth）——执行器确定性执行需要，
    且用"下发那一刻的配置快照"避免执行时配置漂移（见设计稿 §6.4）。
    """
    if not tc:
        return {}
    script = None
    raw = getattr(tc, "script", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                script = parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            script = None
    payload = {
        "test_case_id": tc.id,
        "title": tc.title,
        "category": tc.category,
        "steps": tc.steps,
        "expected": tc.expected,
        "priority": tc.priority,
        "script": script,
        "project_id": tc.project_id,
    }
    # 仅 api 用例注入 api_env 快照（省 payload 体积;执行不需要 contract）。
    if _kind_of(tc) == ExecKind.api:
        from app.services.api_env import get_api_env
        env = get_api_env(db, tc.project_id) or {}
        payload["api_env"] = {
            "base_url": env.get("base_url", ""),
            "auth_type": env.get("auth_type", "fixed"),
            "auth": env.get("auth", {}),
        }
    return payload
```

- [ ] **Step 2: 改调用处传 db**

`backend/app/api/exec_queue.py` 第 128 行 `payload=json.dumps(_payload_of(tc), ...)` 改为:

```python
            payload=json.dumps(_payload_of(tc, db), ensure_ascii=False),
```

- [ ] **Step 3: 写自测**

`backend/scripts/test_payload_api_env.py`:

```python
"""_payload_of 注入 api_env 自测。运行: python -m scripts.test_payload_api_env"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import Base
from app.models.api_env import ApiEnv
from app.models.ai import TestCase
from app.api.exec_queue import _payload_of

def main():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(ApiEnv(project_id=7, base_url="https://svc", auth_type="fixed",
                     auth_json=json.dumps({"headers": {"Authorization": "Bearer t"}})))
        # api 用例 → 带 api_env
        tc_api = TestCase(project_id=7, title="api 用例", exec_kind="api",
                          script=json.dumps([{"name": "x", "request": {"method": "GET", "path": "/a"},
                                              "asserts": [{"type": "status", "op": "eq", "value": 200}]}]))
        s.add(tc_api); s.flush()
        p = _payload_of(tc_api, s)
        assert p["api_env"]["base_url"] == "https://svc", p
        assert p["api_env"]["auth"]["headers"]["Authorization"] == "Bearer t"
        assert isinstance(p["script"], list) and p["script"], "script 应解析为非空数组"
        # gui 用例 → 不带 api_env
        tc_gui = TestCase(project_id=7, title="gui 用例", exec_kind="gui")
        s.add(tc_gui); s.flush()
        assert "api_env" not in _payload_of(tc_gui, s)
    print("OK test_payload_api_env")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑自测**

Run: `cd backend && python -m scripts.test_payload_api_env`
Expected: 打印 `OK test_payload_api_env`。
(注:若 `TestCase` 构造缺必填列导致报错,按 `app/models/ai.py` 实际列补最小字段;core 断言为 api_env 注入与 gui 不注入。)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/exec_queue.py backend/scripts/test_payload_api_env.py
git commit -m "feat(api-exec): 下发时给 api 用例注入 api_env 快照到 payload"
```

---

## Task 4: api-executor 纯函数(点路径提取 + 变量替换 + 断言判定)

**Files:**
- Create: `tools/qalab-runner/api-executor.mjs`(先只导出纯函数)
- Test: `tools/qalab-runner/api-executor.test.mjs`

**Interfaces:**
- Produces: `getPath(obj, path)` — 点路径取值(`"data.list.0.id"`),取不到返回 `undefined`。
- Produces: `substitute(value, vars)` — 深度替换字符串里 `{{var}}` 为 `vars[var]`(对象/数组递归;整串即单占位时保留原类型)。
- Produces: `checkAssert(a, statusCode, body)` — 判一条断言,返回 `{ok:boolean, actual}`;支持 `type∈{status,jsonpath}`、`op∈{eq,neq,exists,contains,gt,lt,regex,type}`。
- 注:变量引用闭环校验(`{{var}}` 须先 extract)是**生成侧 P2** 的职责;runner 侧只执行不校验(未定义变量替换为空串,见 `substitute`)。

- [ ] **Step 1: 写失败测试**

`tools/qalab-runner/api-executor.test.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { getPath, substitute, checkAssert } from "./api-executor.mjs";

test("getPath 点路径含数组下标", () => {
  const body = { data: { list: [{ id: 9 }] }, code: 0 };
  assert.equal(getPath(body, "data.list.0.id"), 9);
  assert.equal(getPath(body, "code"), 0);
  assert.equal(getPath(body, "data.missing"), undefined);
  assert.equal(getPath(body, "a.b.c"), undefined);
});

test("substitute 替换 {{var}}", () => {
  const vars = { token: "abc", pid: 3 };
  assert.equal(substitute("Bearer {{token}}", vars), "Bearer abc");
  assert.equal(substitute("/api/p/{{pid}}", vars), "/api/p/3");
  assert.deepEqual(substitute({ h: "{{token}}", n: 1 }, vars), { h: "abc", n: 1 });
  // 整串即单占位 → 保留原类型(数字)
  assert.equal(substitute("{{pid}}", vars), 3);
  // 未定义变量 → 替换为空串(执行期;闭环校验在生成侧 P2)
  assert.equal(substitute("x{{nope}}y", vars), "xy");
});

test("checkAssert status/jsonpath 各 op", () => {
  const body = { code: 0, msg: "ok", data: { id: 5, name: "n" } };
  assert.equal(checkAssert({ type: "status", op: "eq", value: 200 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "status", op: "eq", value: 200 }, 500, body).ok, false);
  assert.equal(checkAssert({ type: "jsonpath", path: "code", op: "eq", value: 0 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "exists" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.x", op: "exists" }, 200, body).ok, false);
  assert.equal(checkAssert({ type: "jsonpath", path: "msg", op: "contains", value: "o" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "gt", value: 3 }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "data.id", op: "type", value: "number" }, 200, body).ok, true);
  assert.equal(checkAssert({ type: "jsonpath", path: "msg", op: "regex", value: "^o" }, 200, body).ok, true);
});
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd tools/qalab-runner && node --test api-executor.test.mjs`
Expected: FAIL — `api-executor.mjs` 未导出这些函数(Cannot find / undefined)。

- [ ] **Step 3: 写纯函数实现**

`tools/qalab-runner/api-executor.mjs`(先只写纯函数,Task 5 再补 run):

```javascript
// api-executor —— 按结构化 api script 确定性执行(请求-断言-提取原子)。
// 纯 Node fetch,不经 LLM。镜像 step-executor.mjs 的返回契约。
// script 形状(设计稿 §5.1):[{ name, request:{method,path,headers?,query?,body?},
//   asserts:[{type,path?,op,value?}], extract?:{var:"点路径"}, cleanup?:bool }, ...]

// 点路径取值:"data.list.0.id" → 逐段下钻;任一段不存在返回 undefined。
export function getPath(obj, path) {
  if (obj == null || !path) return undefined;
  let cur = obj;
  for (const seg of String(path).split(".")) {
    if (cur == null) return undefined;
    cur = cur[seg];
  }
  return cur;
}

// 深度替换 {{var}}。整串恰为单个 {{var}} 时保留 vars 原类型(数字/布尔);
// 否则做字符串插值。未定义变量替换为空串(执行期宽松;闭环校验在生成侧)。
export function substitute(value, vars) {
  if (typeof value === "string") {
    const whole = value.match(/^\{\{(\w+)\}\}$/);
    if (whole) return vars[whole[1]] !== undefined ? vars[whole[1]] : "";
    return value.replace(/\{\{(\w+)\}\}/g, (_, k) => (vars[k] !== undefined ? String(vars[k]) : ""));
  }
  if (Array.isArray(value)) return value.map((v) => substitute(v, vars));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = substitute(v, vars);
    return out;
  }
  return value;
}

// 判一条断言。返回 {ok, actual}。type: status | jsonpath;op 见下。
export function checkAssert(a, statusCode, body) {
  const actual = a.type === "status" ? statusCode : getPath(body, a.path);
  const v = a.value;
  let ok = false;
  switch (a.op) {
    case "eq": ok = actual === v; break;
    case "neq": ok = actual !== v; break;
    case "exists": ok = actual !== undefined && actual !== null; break;
    case "contains":
      ok = typeof actual === "string" ? actual.includes(String(v))
         : Array.isArray(actual) ? actual.includes(v) : false;
      break;
    case "gt": ok = typeof actual === "number" && actual > v; break;
    case "lt": ok = typeof actual === "number" && actual < v; break;
    case "regex": { try { ok = new RegExp(v).test(String(actual)); } catch { ok = false; } break; }
    case "type": {
      const t = actual === null ? "null" : Array.isArray(actual) ? "array" : typeof actual;
      ok = t === v; break;
    }
    default: ok = false;
  }
  return { ok, actual };
}
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd tools/qalab-runner && node --test api-executor.test.mjs`
Expected: PASS(全部 3 个 test)。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/api-executor.mjs tools/qalab-runner/api-executor.test.mjs
git commit -m "feat(api-exec): api-executor 纯函数(点路径/变量替换/断言判定)"
```

---

## Task 5: api-executor 主执行流程(run + cleanup 语义)

**Files:**
- Modify: `tools/qalab-runner/api-executor.mjs`(补 `run`)
- Modify: `tools/qalab-runner/api-executor.test.mjs`(补 run 测试,用本地 httpServer 桩)

**Interfaces:**
- Consumes: `getPath/substitute/checkAssert`(Task 4)。
- Produces: `run(script, apiEnv, log=()=>{}, fetchImpl=fetch) -> Promise<{verdict, reason, evidence, duration_ms, steps} | {needClaude, reason}>`。
  - `apiEnv`: `{base_url, auth_type, auth}`;`fetchImpl` 可注入(测试用桩,默认全局 fetch)。
  - 语义:普通步失败即短路 → verdict=fail,但仍执行 cleanup 步;cleanup 逆序、尽力而为、断言不计入 verdict;无 base_url → 直接 fail;空 script → `{needClaude}`。

- [ ] **Step 1: 写失败测试(run,含桩 fetch)**

`tools/qalab-runner/api-executor.test.mjs` 追加:

```javascript
import { run } from "./api-executor.mjs";

// 桩 fetch:按 path 返回预设响应。记录调用顺序供断言。
function stubFetch(routes, calls) {
  return async (url, opts) => {
    const u = new URL(url);
    calls.push({ method: opts.method, path: u.pathname, headers: opts.headers, body: opts.body });
    const r = routes[opts.method + " " + u.pathname];
    if (!r) return { status: 404, json: async () => ({ code: 404, msg: "no route" }) };
    return { status: r.status, json: async () => r.body };
  };
}

test("run 链式:登录取token→创建→清理,变量传递", async () => {
  const calls = [];
  const routes = {
    "POST /api/auth/login": { status: 200, body: { code: 0, data: { token: "TK" } } },
    "POST /api/projects": { status: 200, body: { code: 0, data: { id: 42 } } },
    "DELETE /api/projects/42": { status: 200, body: { code: 0 } },
  };
  const script = [
    { name: "登录", request: { method: "POST", path: "/api/auth/login", body: { u: "qa" } },
      asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }], extract: { token: "data.token" } },
    { name: "创建", request: { method: "POST", path: "/api/projects", headers: { Authorization: "Bearer {{token}}" }, body: { name: "n" } },
      asserts: [{ type: "jsonpath", path: "data.id", op: "exists" }], extract: { pid: "data.id" } },
    { name: "清理", cleanup: true, request: { method: "DELETE", path: "/api/projects/{{pid}}", headers: { Authorization: "Bearer {{token}}" } },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
  ];
  const r = await run(script, { base_url: "https://svc", auth_type: "login", auth: {} }, () => {}, stubFetch(routes, calls));
  assert.equal(r.verdict, "pass", r.reason);
  // {{token}} / {{pid}} 已替换
  assert.equal(calls[1].headers.Authorization, "Bearer TK");
  assert.equal(calls[2].path, "/api/projects/42");
});

test("run 普通步失败即短路,但仍执行 cleanup", async () => {
  const calls = [];
  const routes = {
    "POST /api/projects": { status: 200, body: { code: 0, data: { id: 7 } } },
    "GET /api/projects/7": { status: 500, body: { code: 500, msg: "err" } }, // 断言失败
    "DELETE /api/projects/7": { status: 200, body: { code: 0 } },
  };
  const script = [
    { name: "创建", request: { method: "POST", path: "/api/projects", body: {} },
      asserts: [{ type: "status", op: "eq", value: 200 }], extract: { pid: "data.id" } },
    { name: "查询(将失败)", request: { method: "GET", path: "/api/projects/{{pid}}" },
      asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }] },
    { name: "本不该执行的普通步", request: { method: "GET", path: "/api/never" },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
    { name: "清理", cleanup: true, request: { method: "DELETE", path: "/api/projects/{{pid}}" },
      asserts: [{ type: "status", op: "eq", value: 200 }] },
  ];
  const r = await run(script, { base_url: "https://svc", auth_type: "fixed", auth: {} }, () => {}, stubFetch(routes, calls));
  assert.equal(r.verdict, "fail");
  assert.match(r.reason, /查询/);
  const paths = calls.map((c) => c.method + " " + c.path);
  assert.ok(!paths.includes("GET /api/never"), "失败后普通步不应执行");
  assert.ok(paths.includes("DELETE /api/projects/7"), "cleanup 应执行");
});

test("run 无 base_url 直接 fail", async () => {
  const r = await run([{ name: "x", request: { method: "GET", path: "/a" }, asserts: [{ type: "status", op: "eq", value: 200 }] }],
    { base_url: "", auth_type: "fixed", auth: {} }, () => {}, async () => ({ status: 200, json: async () => ({}) }));
  assert.equal(r.verdict, "fail");
  assert.match(r.reason, /未配置|api 环境|base_url/);
});

test("run 空 script → needClaude", async () => {
  const r = await run([], { base_url: "https://svc" }, () => {});
  assert.equal(r.needClaude, true);
});

test("run fixed 鉴权预置 header", async () => {
  const calls = [];
  const routes = { "GET /api/me": { status: 200, body: { code: 0 } } };
  const script = [{ name: "me", request: { method: "GET", path: "/api/me" },
                   asserts: [{ type: "jsonpath", path: "code", op: "eq", value: 0 }] }];
  await run(script, { base_url: "https://svc", auth_type: "fixed", auth: { headers: { Authorization: "Bearer FIX" } } }, () => {}, stubFetch(routes, calls));
  assert.equal(calls[0].headers.Authorization, "Bearer FIX");
});
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd tools/qalab-runner && node --test api-executor.test.mjs`
Expected: FAIL — `run` 未定义。

- [ ] **Step 3: 实现 run**

`tools/qalab-runner/api-executor.mjs` 追加:

```javascript
// 主执行:顺序跑普通步,失败即短路;最后逆序执行 cleanup 步(尽力而为,不计入 verdict)。
// 返回契约与 step-executor 对齐:{verdict, reason, evidence, duration_ms, steps} 或 {needClaude, reason}。
export async function run(script, apiEnv, log = () => {}, fetchImpl = fetch) {
  if (!Array.isArray(script) || script.length === 0) {
    return { needClaude: true, reason: "用例无结构化 api script,退回 claude 执行" };
  }
  const baseUrl = String((apiEnv && apiEnv.base_url) || "").replace(/\/$/, "");
  if (!baseUrl) {
    return { verdict: "fail", reason: "项目未配置 api 环境(base_url 为空),无法执行 api 用例", evidence: null, duration_ms: 0, steps: [] };
  }
  const started = Date.now();
  const sec = () => ((Date.now() - started) / 1000).toFixed(1);
  const vars = {};
  // fixed 鉴权:预置固定 header,注入每个请求(login 模式靠用例内登录步骤 extract token)。
  const fixedHeaders = (apiEnv.auth_type === "fixed" && apiEnv.auth && apiEnv.auth.headers) ? apiEnv.auth.headers : {};

  const steps = [];
  const normals = [];
  const cleanups = [];
  script.forEach((st, i) => (st && st.cleanup ? cleanups : normals).push({ st, i }));

  let failed = null;  // 首个失败的诊断
  // 执行单步:替换变量→发请求→判断言→提取。返回 {ok, reason?}。
  async function exec({ st, i }, isCleanup) {
    const name = st.name || `step${i + 1}`;
    const req = substitute(st.request || {}, vars);
    const headers = { "Content-Type": "application/json", ...fixedHeaders, ...(req.headers || {}) };
    let url = baseUrl + (req.path || "");
    if (req.query && typeof req.query === "object") {
      const qs = new URLSearchParams(Object.entries(req.query).map(([k, v]) => [k, String(v)])).toString();
      if (qs) url += (url.includes("?") ? "&" : "?") + qs;
    }
    const method = String(req.method || "GET").toUpperCase();
    const hasBody = req.body !== undefined && method !== "GET";
    log(`  [+${sec()}s] ▶ ${isCleanup ? "[cleanup] " : ""}${name} ${method} ${req.path}`);
    let statusCode, body;
    try {
      const res = await fetchImpl(url, { method, headers, body: hasBody ? JSON.stringify(req.body) : undefined });
      statusCode = res.status;
      try { body = await res.json(); } catch { body = null; }
    } catch (e) {
      return { ok: false, reason: `${name} 请求异常: ${e.message}` };
    }
    // 断言
    for (const a of st.asserts || []) {
      const { ok, actual } = checkAssert(a, statusCode, body);
      if (!ok) {
        const tgt = a.type === "status" ? "status" : `jsonpath ${a.path}`;
        return { ok: false, reason: `${name} 断言失败: ${tgt} 期望 ${a.op} ${a.value ?? ""},实际 ${JSON.stringify(actual)}` };
      }
    }
    // 提取
    if (st.extract && typeof st.extract === "object") {
      for (const [k, path] of Object.entries(st.extract)) {
        const val = getPath(body, path);
        if (val === undefined) return { ok: false, reason: `${name} 提取变量 ${k} 失败: 响应无路径 ${path}` };
        vars[k] = val;
      }
    }
    steps.push({ name, method, path: req.path, status: statusCode, cleanup: !!isCleanup });
    return { ok: true };
  }

  // 普通步:顺序,失败即短路
  for (const item of normals) {
    const r = await exec(item, false);
    if (!r.ok) { failed = r.reason; break; }
  }
  // cleanup:逆序,尽力而为(失败只告警,不计入 verdict)
  for (const item of cleanups.reverse()) {
    try { const r = await exec(item, true); if (!r.ok) log(`  [+${sec()}s] ⚠ cleanup 失败(忽略): ${r.reason}`); }
    catch (e) { log(`  [+${sec()}s] ⚠ cleanup 异常(忽略): ${e.message}`); }
  }

  const duration_ms = Date.now() - started;
  if (failed) return { verdict: "fail", reason: failed, evidence: null, duration_ms, steps };
  const checks = script.reduce((n, s) => n + ((s.asserts || []).length), 0);
  return { verdict: "pass", reason: `api 确定性执行通过: ${normals.length} 步, ${checks} 处断言全部满足`, evidence: null, duration_ms, steps };
}
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd tools/qalab-runner && node --test api-executor.test.mjs`
Expected: PASS(全部测试)。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/api-executor.mjs tools/qalab-runner/api-executor.test.mjs
git commit -m "feat(api-exec): api-executor run 主流程(链式变量/短路/cleanup 尽力而为)"
```

---

## Task 6: runner 分流接入 + 端到端验证

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`(顶部 import;分流 425-426)
- Test: 手动端到端(用平台自身后端当被测系统)

**Interfaces:**
- Consumes: `run`(Task 5,as `apiRun`);`item.payload.script`、`item.payload.api_env`(Task 3 注入)。
- Produces: kind==api 且有合法 script → 走 `apiRun`;否则回落 `runClaude`(现状)。

- [ ] **Step 1: 顶部 import**

`tools/qalab-runner/runner.mjs`:在 `import { createGuiCore } ...`(第 14 行)附近加:

```javascript
import { run as apiRun } from "./api-executor.mjs";
```

- [ ] **Step 2: 改 api 分流**

`tools/qalab-runner/runner.mjs` 第 425-426 行(`else if (item.kind === "api" || item.kind === "cli")` 分支)替换为:

```javascript
      } else if (item.kind === "api") {
        // api:有结构化 script → 确定性执行器(不经 LLM);无/降级 → claude(+Bash)兜底。
        const script = item.payload?.script;
        if (Array.isArray(script) && script.length) {
          const r = await apiRun(script, item.payload?.api_env || {}, (m) => log(m));
          if (r.needClaude) { log(`  api script 需降级:${r.reason}`); result = await runClaude(item.payload, item.kind); }
          else result = r;
        } else {
          result = await runClaude(item.payload, item.kind);
        }
      } else if (item.kind === "cli") {
        result = await runClaude(item.payload, item.kind);   // cli:仍走 claude(+Bash)
      } else {
```

- [ ] **Step 3: 端到端验证(用平台自身后端当被测系统)**

准备(平台后端即标准 `{code,msg,data}` + JWT,是理想被测目标):
1. 启动后端 `cd backend && uvicorn app.main:app --port 8000`。
2. 给某项目配 api_env:`PUT /api/api-env` 存 `{project_id, base_url:"http://localhost:8000", auth_type:"fixed", auth:{headers:{Authorization:"Bearer <一个有效 access token>"}}}`。
3. 手动造一条 api 用例:在该项目建一条 `test_case`,`exec_kind="api"`,`script` 存一段 JSON(登录/读取自身接口 + 断言 `code==0`);挂进某任务的验收清单。
4. 前端「发送到本地执行」下发到本机 runner。
5. 本机跑 `cd tools/qalab-runner && node runner.mjs`(配好 `.env` 的 BASE_URL/RUNNER_TOKEN/RUNNER_ID)。

预期:runner 日志显示 `▶ ... GET/POST ...` 逐步执行,**不启动 claude 子进程**;平台回写 verdict/reason,断言明细可读。

- [ ] **Step 4: 验证降级路径**

把上面用例的 `script` 清空(或设为非数组)再下发一次。
预期:runner 日志显示回落 `runClaude`(LLM 兜底),不报错。

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/runner.mjs
git commit -m "feat(api-exec): runner 对 api 用例分流到确定性执行器(回落 claude 兜底)"
```

---

## P1 完成标准(验收)

- [ ] `api_env` 表在新库(create_all)与老库(migrate)均能建出;`GET/PUT /api/api-env` 读写通(admin 校验生效)。
- [ ] 下发 api 用例时 payload 带 `api_env` 快照;gui/e2e 用例不带。
- [ ] `node --test api-executor.test.mjs` 全绿(点路径/变量替换/断言/链式/短路/cleanup/无base_url/降级 全覆盖)。
- [ ] 手动 e2e:一条 api script 用例 runner **不经 claude** 确定性执行,verdict/reason 正确;清空 script 后回落 claude 不报错。

---

## 后续阶段概览(P2–P4,执行验证 P1 后逐一展开为完整计划)

> 以下为 task 级概览,非最终计划。每阶段独立成 `docs/superpowers/plans/` 计划文件,沿用本计划的 TDD/自测/信封约定。

**P2 生成侧(让需求→可执行 api script):**
- T1 `claude_runner.py` 加 `_load_api_contract(project_id)`(读 `api_env.contract` 注入 prompt,仿 `_load_selector_keys`)。
- T2 `build_testcase_prompt` 增「api script 编写规范」段(变体 A 字段 + 正例 + 硬约束:每步 ≥1 断言、含写操作必带 cleanup、优先 `code==0` 断言、无契约改判 manual)。
- T3 新增 `_validate_api_script(script, ...)`(method/path/断言/op 合法;**变量引用闭环**:`{{var}}` 须先 extract;**写操作必带 cleanup** 否则非法)+ 自测。
- T4 `parse_testcases` 按 kind 分流:`api → _validate_api_script`,非法降级 manual。
- T5 `build_script_prompt`/`generate_script` 加 api 分支(单条重生对 api 可用)。
- 验收:一段带契约的需求 → AI 产出的 api script 过校验且能被 P1 执行器跑通。

**P3 契约录入(降低录入负担):**
- T1 前端「api 环境」设置页(base_url/鉴权 + contract 编辑;调 `PUT /api/api-env`)。
- T2 后端 Swagger 导入端点(贴 URL/上传 → 解析精简清单写 `contract`)。
- T3 `curl 解析器`(纯函数,`node`/py 均可;→ `{method,base_url,path,headers,body}`,剥离鉴权头)+ 自测。
- T4 curl 两入口:并入 contract / 转单步 script 种子。
- 验收:粘贴一条 curl 能解析、鉴权头剥离;Swagger 导入后契约进 prompt。

**P4 需求录入引导(提升生成质量):**
- T1 AITestGen 生成页展示项目「可选接口清单」(来自 contract)辅助圈定。
- T2 需求录入引导:关联接口 + 场景清单 + 业务判定(替代空文本框)。
- 验收:按引导录入的需求,api 用例生成命中率与可执行率提升。
