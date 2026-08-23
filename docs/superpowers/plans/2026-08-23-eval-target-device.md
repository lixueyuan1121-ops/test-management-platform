# 指定设备(vm)执行 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对话测评下发时可指定纳米 Work 客户端里的目标设备(vm),CLI 平台模式执行每条对话前先切到该设备再跑。

**Architecture:** 平台新增"设备快照上报/查询"(CLI 连客户端后注入 `window.clawDeviceService.getDeviceList` 读列表并上报,前端下发时下拉选)+ `eval_run.target_device` 列携带目标设备;CLI 执行前用 `location.assign('<label>.work.n.cn/claw?vm_id=<label>')` 切换(已本机实测 1.5s 到位)。分平台侧(git 分支 `spec/eval-target-device`)与 CLI 侧(`D:\code\ai-eval-cli-yt`,非 git,文件交付)。

**Tech Stack:** FastAPI + SQLAlchemy 2.0(Mapped/mapped_column)、Vue3 + ElementPlus、CLI 为 Node + Playwright(CDP)。

## Global Constraints

- 结构化数据一律用 `Text` 存 JSON(MySQL 5.6 兼容,不用原生 JSON 列)。
- 统一响应信封 `{code,msg,data}`,后端用 `app.schemas.common.ok()`;每 router 手写 `_to_out`。
- 两份 schema 手动同步:SQLAlchemy 模型(`app/models/`)与 `backend/sql/schema.sql`。加列还要在 `app/db/migrate.py` 补 `ensure_*` 并在 `main.py` startup 调用(老库兼容)。
- 新表须在 `app/models/__init__.py` 汇总导入,`create_all` 才建。
- runner 鉴权用 `require_runner_ctx`(设备 token 锁 `ctx.device.runner_id`);用户接口用 `get_current_user`。
- 本仓库**无测试框架**:平台侧"测试"走一次性 Python 验证脚本(`tmp_` 前缀,跑完删);前端走 `npm run build`;CLI 走本机真机验证(本机即 lili-win,客户端 CDP 在 127.0.0.1:9222)。
- 工作区无关既存改动(`tools/qalab-runner/run.cmd`、`tools/__MACOSX/`、`tools/qalab-runner.zip`)**全程不 add、不提交**。用精确 `git add <path>`,绝不 `git add -A`。
- 提交信息结尾:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- CLI 切换设备已实测参数:label = `device.url` 首段(带前缀,如 `p8a45…`);在线判据 `status ∈ {online,active}`;切换后就绪判据 = URL host 首段含 label + `currentDevice.id==vm_id` + `chat-compose-rich-textarea` 无 `disabled`。

---

## 平台侧(git 分支 spec/eval-target-device)

### Task 1: 数据模型 —— EvalClientDevice 新表 + eval_run.target_device 列

**Files:**
- Modify: `backend/app/models/ai_eval.py`(尾部加 EvalClientDevice 类 + EvalRun 加 target_device 列)
- Modify: `backend/app/models/__init__.py`(导入 + __all__)
- Modify: `backend/app/db/migrate.py`(加 ensure_eval_run_target_device)
- Modify: `backend/app/main.py`(startup 调用)
- Modify: `backend/sql/schema.sql`(eval_run 加列 + eval_client_device 建表)
- Test: `backend/tmp_verify_device_model.py`(一次性脚本)

**Interfaces:**
- Produces:
  - `EvalClientDevice` 模型,字段 `id/runner:str/vm_id:str/label:str/name:str/status:str/device_type:int/last_report_at:datetime`,唯一约束 `(runner, vm_id)`。
  - `EvalRun.target_device: Mapped[str | None]`(String(64))。
  - `migrate.ensure_eval_run_target_device() -> None`。

- [ ] **Step 1: EvalRun 加 target_device 列**

在 `backend/app/models/ai_eval.py` 的 `EvalRun` 类,`target_engine` 行下面加:

```python
    # 目标设备(纳米 Work 客户端里的 vm_id);空=不指定,CLI 用当前设备(向后兼容)。
    target_device: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 2: 加 EvalClientDevice 模型**

在 `backend/app/models/ai_eval.py` 文件**末尾**追加(注意顶部已 import 的 `String/Integer/DateTime/func/datetime/Mapped/mapped_column/Base` 均可复用;需补 import `UniqueConstraint`):

先在文件顶部 import 行把 `UniqueConstraint` 加入(第 9 行 `from sqlalchemy import ...`):

```python
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
```

再在文件末尾追加:

```python


class EvalClientDevice(Base):
    """执行机(runner)连上的纳米 Work 客户端里的可切换设备(vm)快照。

    CLI 平台模式连客户端后注入 window.clawDeviceService.getDeviceList 读到,上报到此表(按 runner+vm_id upsert),
    供前端下发时下拉选目标设备。区别于 runner_device(物理执行机):物理机 → 机上多个 vm。
    """

    __tablename__ = "eval_client_device"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    runner: Mapped[str] = mapped_column(String(64), index=True)  # 所属执行机 runner_id
    vm_id: Mapped[str] = mapped_column(String(64))               # 设备 32 位 hex 核(=device.id)
    label: Mapped[str | None] = mapped_column(String(96), nullable=True)  # 带前缀子域 label(=device.url 首段,切换用)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 显示名
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # online/offline/pending/...
    device_type: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0云/1本地/2盒子/3wsl/4elec
    last_report_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("runner", "vm_id", name="uk_eval_device_runner_vm"),)
```

- [ ] **Step 3: __init__.py 汇总导入**

`backend/app/models/__init__.py`:第 18 行改为

```python
from app.models.ai_eval import EvalQuery, EvalRun, EvalClientDevice
```

`__all__` 里 `"EvalRun",` 后加 `"EvalClientDevice",`。

- [ ] **Step 4: migrate 加列函数**

`backend/app/db/migrate.py` 末尾(在 `ensure_eval_run_payload` 之后)加:

```python
def ensure_eval_run_target_device() -> None:
    """eval_run 补 target_device 列(目标设备 vm_id)。老库已建表走 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_run"):
        return
    if "target_device" not in _columns("eval_run"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN target_device VARCHAR(64) NULL"))
```

- [ ] **Step 5: main.py startup 调用**

`backend/app/main.py`:第 14 行 import 末尾加 `, ensure_eval_run_target_device`;在 `init_db()` 里 `ensure_eval_run_payload()` 那行下面加一行:

```python
    ensure_eval_run_target_device()
```

(新表 `eval_client_device` 由 `create_all` 自动建,无需 migrate。)

- [ ] **Step 6: schema.sql 同步**

`backend/sql/schema.sql`:eval_run 表内 `target_engine` 行(339 行附近)下面加一行(放 payload 前也可,保持与模型顺序一致放 target_engine 后):

```sql
  `target_device` VARCHAR(64) NULL,
```

在 eval_run 表 `) ENGINE=InnoDB ...;` 结束之后、下一张表之前,加新表:

```sql
-- 执行机连上的纳米 Work 客户端可切换设备(vm)快照:CLI 上报,前端下发时下拉选
CREATE TABLE `eval_client_device` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `runner` VARCHAR(64) NOT NULL,
  `vm_id` VARCHAR(64) NOT NULL,
  `label` VARCHAR(96) NULL,
  `name` VARCHAR(128) NULL,
  `status` VARCHAR(16) NULL,
  `device_type` INT NULL,
  `last_report_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_eval_device_runner_vm` (`runner`,`vm_id`),
  KEY `idx_eval_device_runner` (`runner`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 7: 写验证脚本**

创建 `backend/tmp_verify_device_model.py`:

```python
"""一次性验证:模型可导入、建表、target_device 列存在、EvalClientDevice upsert 唯一约束。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db.session import Base, engine, SessionLocal
from app.models import EvalRun, EvalClientDevice  # 触发注册

# 建全表(内存/现有库均可;这里只验证 DDL 与模型)
Base.metadata.create_all(bind=engine, tables=[
    EvalClientDevice.__table__,
])
cols = {c.name for c in EvalRun.__table__.columns}
assert "target_device" in cols, f"eval_run 缺 target_device 列: {cols}"
print("OK: eval_run.target_device 存在")

dcols = {c.name for c in EvalClientDevice.__table__.columns}
expect = {"id","runner","vm_id","label","name","status","device_type","last_report_at"}
assert expect <= dcols, f"eval_client_device 列不全: 缺 {expect - dcols}"
print("OK: eval_client_device 列齐:", sorted(dcols))

uqs = [tuple(c.name for c in con.columns) for con in EvalClientDevice.__table__.constraints
       if con.__class__.__name__ == "UniqueConstraint"]
assert ("runner","vm_id") in uqs, f"缺 (runner,vm_id) 唯一约束: {uqs}"
print("OK: (runner,vm_id) 唯一约束存在")
print("ALL PASS")
```

- [ ] **Step 8: 跑验证脚本,确认通过**

Run: `cd backend && python tmp_verify_device_model.py`
Expected: 打印 `ALL PASS`(若 eval_run 已在库中且缺列,create_all 不改现有表——但本脚本只 create EvalClientDevice.__table__,target_device 检查针对模型定义,不依赖库;通过即模型正确)。

- [ ] **Step 9: 删验证脚本 + 提交**

```bash
rm backend/tmp_verify_device_model.py
git add backend/app/models/ai_eval.py backend/app/models/__init__.py backend/app/db/migrate.py backend/app/main.py backend/sql/schema.sql
git commit -m "feat(eval): eval_run 加 target_device + 新表 eval_client_device

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 后端设备端点 —— api/eval_devices.py

**Files:**
- Create: `backend/app/api/eval_devices.py`
- Create: `backend/app/schemas/eval_device.py`
- Modify: `backend/app/api/router.py`(注册)
- Test: `backend/tmp_verify_device_api.py`(一次性)

**Interfaces:**
- Consumes: `EvalClientDevice`(Task1)、`require_runner_ctx`/`RunnerCtx`、`get_current_user`、`ok`。
- Produces:
  - `POST /api/eval-devices/report`(runner 鉴权):body `{runner, devices:[{vm_id,label,name,status,device_type}]}` → `{reported:n}`。upsert 按 (runner, vm_id)。
  - `GET /api/eval-devices?runner=X`(用户 JWT):→ `[{vm_id,name,status,device_type,label,last_report_at}]`,在线优先+name 排序。

- [ ] **Step 1: schemas**

创建 `backend/app/schemas/eval_device.py`:

```python
from pydantic import BaseModel, Field


class EvalDeviceItem(BaseModel):
    vm_id: str = Field(..., max_length=64)
    label: str | None = Field(None, max_length=96)
    name: str | None = Field(None, max_length=128)
    status: str | None = Field(None, max_length=16)
    device_type: int | None = None


class EvalDeviceReportIn(BaseModel):
    runner: str = Field("mac-01", max_length=64)
    devices: list[EvalDeviceItem] = Field(default_factory=list)
```

- [ ] **Step 2: 端点实现**

创建 `backend/app/api/eval_devices.py`:

```python
"""对话测评客户端设备(vm)快照:CLI 上报 → 平台存 → 前端下发时下拉选目标设备。

区别于 devices.py(物理执行机 runner_device)与 eval_queue.py(执行队列)。
上报走 require_runner_ctx(设备 token 锁 runner_id);查询走用户 JWT。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.db.session import get_db
from app.models import EvalClientDevice, User
from app.schemas.common import ok
from app.schemas.eval_device import EvalDeviceReportIn

router = APIRouter(prefix="/api/eval-devices", tags=["eval-devices"])

# 在线状态排序权重(在线优先)
_ONLINE = {"online", "active"}


def _to_out(d: EvalClientDevice) -> dict:
    return {
        "vm_id": d.vm_id,
        "name": d.name,
        "status": d.status,
        "device_type": d.device_type,
        "label": d.label,
        "last_report_at": d.last_report_at.isoformat() if d.last_report_at else None,
    }


@router.post("/report")
def report_devices(body: EvalDeviceReportIn, db: Session = Depends(get_db),
                   ctx: RunnerCtx = Depends(require_runner_ctx)):
    runner = ctx.device.runner_id if ctx.device is not None else body.runner
    now = datetime.utcnow()
    reported = 0
    for item in body.devices:
        if not item.vm_id:
            continue
        row = (db.query(EvalClientDevice)
               .filter(EvalClientDevice.runner == runner, EvalClientDevice.vm_id == item.vm_id)
               .first())
        if row is None:
            row = EvalClientDevice(runner=runner, vm_id=item.vm_id)
            db.add(row)
        row.label = item.label
        row.name = item.name
        row.status = item.status
        row.device_type = item.device_type
        row.last_report_at = now
        reported += 1
    db.commit()
    return ok({"reported": reported})


@router.get("")
def list_devices(runner: str = Query(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    rows = (db.query(EvalClientDevice)
            .filter(EvalClientDevice.runner == runner)
            .all())
    # 在线优先,再按名称;Python 侧排序(数据量小)
    rows.sort(key=lambda d: (0 if (d.status or "") in _ONLINE else 1, d.name or d.vm_id))
    return ok([_to_out(d) for d in rows])
```

- [ ] **Step 3: 注册 router**

`backend/app/api/router.py`:import 行加 `eval_devices`;在 `api_router.include_router(eval_export.router)` 后加

```python
api_router.include_router(eval_devices.router)
```

- [ ] **Step 4: 写验证脚本**

创建 `backend/tmp_verify_device_api.py`:

```python
"""一次性验证:report upsert(同 vm_id 更新不重复)、list 在线优先排序。用内存 sqlite。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DATABASE_URL"] = "sqlite:///./tmp_devtest.db"
from app.db.session import Base, engine, SessionLocal
from app.models import EvalClientDevice
from datetime import datetime

Base.metadata.create_all(bind=engine, tables=[EvalClientDevice.__table__])
db = SessionLocal()
# 清干净
db.query(EvalClientDevice).delete(); db.commit()

def upsert(runner, vm_id, name, status):
    row = db.query(EvalClientDevice).filter_by(runner=runner, vm_id=vm_id).first()
    if row is None:
        row = EvalClientDevice(runner=runner, vm_id=vm_id); db.add(row)
    row.name = name; row.status = status; row.last_report_at = datetime.utcnow()
    db.commit()

upsert("lili-win", "vm1", "云龙虾A", "online")
upsert("lili-win", "vm2", "WSL-6135", "offline")
upsert("lili-win", "vm1", "云龙虾A改名", "online")  # 同 vm_id 再报=更新
rows = db.query(EvalClientDevice).filter_by(runner="lili-win").all()
assert len(rows) == 2, f"upsert 应 2 行,实得 {len(rows)}"
assert db.query(EvalClientDevice).filter_by(runner="lili-win", vm_id="vm1").first().name == "云龙虾A改名"
print("OK: upsert 按 (runner,vm_id) 去重更新")

_ONLINE = {"online", "active"}
rows.sort(key=lambda d: (0 if (d.status or "") in _ONLINE else 1, d.name or d.vm_id))
assert rows[0].status == "online", "在线应排前"
print("OK: 在线优先排序")
db.close()
os.remove("./tmp_devtest.db")
print("ALL PASS")
```

- [ ] **Step 5: 跑验证,确认通过**

Run: `cd backend && python tmp_verify_device_api.py`
Expected: `ALL PASS`。

- [ ] **Step 6: 删脚本 + 提交**

```bash
rm backend/tmp_verify_device_api.py
git add backend/app/api/eval_devices.py backend/app/schemas/eval_device.py backend/app/api/router.py
git commit -m "feat(eval): 设备快照上报/查询端点 /api/eval-devices

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: enqueue 携带 target_device

**Files:**
- Modify: `backend/app/schemas/eval_queue.py`(EvalEnqueueIn 加 target_device)
- Modify: `backend/app/api/eval_queue.py`(enqueue 落库 + _to_out 回显)
- Test: `backend/tmp_verify_enqueue_device.py`(一次性)

**Interfaces:**
- Consumes: `EvalRun.target_device`(Task1)。
- Produces: enqueue 入参多 `target_device: str|None`;`_to_out` 输出多 `"target_device"` 顶层字段(CLI fetchPending 读)。

- [ ] **Step 1: schema 加字段**

`backend/app/schemas/eval_queue.py` 的 `EvalEnqueueIn` 加一行:

```python
    target_device: str | None = Field(None, max_length=64)
```

- [ ] **Step 2: enqueue 落库**

`backend/app/api/eval_queue.py` 的 `enqueue` 函数,建 `EvalRun(...)` 时加 `target_device=body.target_device`:

```python
        row = EvalRun(
            eval_query_id=q.id, project_id=q.project_id, batch_id=batch_id,
            runner=body.runner, target_engine=body.target_engine,
            target_device=body.target_device,
            device_kind=EvalDeviceKind.desktop,
            status=EvalRunStatus.pending, payload=json.dumps(_payload_of(q), ensure_ascii=False),
            enqueued_by=user.id,
        )
```

- [ ] **Step 3: _to_out 回显**

`backend/app/api/eval_queue.py` 的 `_to_out`,在 `"target_engine": r.target_engine,` 下面加:

```python
        "target_device": r.target_device,
```

- [ ] **Step 4: 写验证脚本**

创建 `backend/tmp_verify_enqueue_device.py`:

```python
"""一次性验证:EvalRun 可带 target_device 落库并被 _to_out 回显。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DATABASE_URL"] = "sqlite:///./tmp_enq.db"
from app.db.session import Base, engine, SessionLocal
from app.models import EvalRun
from app.api.eval_queue import _to_out

Base.metadata.create_all(bind=engine, tables=[EvalRun.__table__])
db = SessionLocal()
r = EvalRun(project_id=1, runner="lili-win", target_engine="namiwork", target_device="8a4543de")
db.add(r); db.commit(); db.refresh(r)
out = _to_out(r)
assert out["target_device"] == "8a4543de", out
print("OK: target_device 落库 + _to_out 回显:", out["target_device"])
db.close(); os.remove("./tmp_enq.db")
print("ALL PASS")
```

- [ ] **Step 5: 跑验证,确认通过**

Run: `cd backend && python tmp_verify_enqueue_device.py`
Expected: `ALL PASS`。

- [ ] **Step 6: 删脚本 + 提交**

```bash
rm backend/tmp_verify_enqueue_device.py
git add backend/app/schemas/eval_queue.py backend/app/api/eval_queue.py
git commit -m "feat(eval): enqueue 携带 target_device + _to_out 回显

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 前端下发选设备(AIEvalGen.vue)

**Files:**
- Modify: `frontend/src/api/index.js`(加 listEvalDevices)
- Modify: `frontend/src/views/AIEvalGen.vue`(设备下拉 + payload 带 target_device)
- Test: `npm run build`

**Interfaces:**
- Consumes: `GET /api/eval-devices?runner=X`(Task2)、`enqueueEvalQueries`(现有,payload 加 target_device)。
- Produces: 前端下发区选执行机后可下拉选目标设备。

- [ ] **Step 1: api 封装**

`frontend/src/api/index.js` 的 `enqueueEvalQueries` 那行(110)下面加:

```javascript
// 对话测评:某执行机上报的客户端设备(vm)列表,供下发时选目标设备。
export const listEvalDevices = (runner) => http.get('/eval-devices', { params: { runner } })
```

- [ ] **Step 2: 引入 + 状态**

`frontend/src/views/AIEvalGen.vue` 的 import(第 152 行)把 `listEvalDevices` 加入:

```javascript
import { listTasks, aiStatus, streamEvalQueries, listMyDevices, enqueueEvalQueries, listEvalDevices } from '@/api'
```

在 `const chosenRunner = ref('')` 下面加:

```javascript
const clientDevices = ref([])       // 选中执行机上报的客户端设备(vm)列表
const chosenDevice = ref('')        // 选中的目标设备 vm_id(空=用执行机当前设备)
```

- [ ] **Step 3: 拉设备列表逻辑**

在 `<script setup>` 里加一个函数(放 `dispatchSelected` 之前):

```javascript
// 选中执行机后,拉该执行机上报的客户端设备(vm)供下拉选。执行机变了要重拉、重置已选设备。
async function loadClientDevices() {
  chosenDevice.value = ''
  clientDevices.value = []
  if (!chosenRunner.value) return
  try { clientDevices.value = await listEvalDevices(chosenRunner.value) || [] } catch { clientDevices.value = [] }
}
```

在 onMounted 里,设置默认 chosenRunner 之后调用一次。把 onMounted 末尾的

```javascript
  if (devices.value.length) chosenRunner.value = devices.value[0].runner_id
```

改为

```javascript
  if (devices.value.length) { chosenRunner.value = devices.value[0].runner_id; await loadClientDevices() }
```

- [ ] **Step 4: 模板加设备下拉 + runner 变化监听**

`frontend/src/views/AIEvalGen.vue` 的 dispatch-bar(第 97-108 行),给执行机 el-select 加 `@change="loadClientDevices"`,并在其后、发送按钮前插入设备下拉:

```html
            <el-select
              v-model="chosenRunner" size="small" style="width:180px"
              :placeholder="devices.length ? '选择执行机' : '未登记设备'"
              no-data-text="去『我的设备』注册"
              @change="loadClientDevices"
            >
              <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
            </el-select>
            <el-select
              v-model="chosenDevice" size="small" style="width:200px" clearable
              :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'"
              no-data-text="CLI platform 连客户端后自动上报"
            >
              <el-option
                v-for="dev in clientDevices" :key="dev.vm_id"
                :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`"
                :value="dev.vm_id"
              />
            </el-select>
```

- [ ] **Step 5: 下发带 target_device**

`dispatchSelected` 里 `enqueueEvalQueries({...})` 的入参加 `target_device`:

```javascript
    const res = await enqueueEvalQueries({
      project_id: pid.value,
      runner: chosenRunner.value,
      target_engine: 'namiwork',
      target_device: chosenDevice.value || null,
      eval_query_ids: selectedQueries.value.map((q) => q.id),
    })
```

- [ ] **Step 6: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功,无报错(dist 更新)。

- [ ] **Step 7: 提交**

```bash
git add frontend/src/api/index.js frontend/src/views/AIEvalGen.vue frontend/dist
git commit -m "feat(eval): 前端下发选目标设备(vm)下拉 + 带 target_device

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## CLI 侧(D:\code\ai-eval-cli-yt,非 git,文件交付 + 本机真机验证)

> 本机即 lili-win,纳米 Work 客户端 CDP 在 127.0.0.1:9222。CLI 改动无 git 提交,每任务用真机验证(注入读列表/切换已实测可行)。

### Task 5: desktop-pool.js 加设备读取与切换

**Files:**
- Modify: `D:\code\ai-eval-cli-yt\src\desktop-pool.js`
- Test: `D:\code\ai-eval-cli-yt\tmp_test_pool_devices.js`(一次性真机)

**Interfaces:**
- Consumes: 现有 `this.mainPage`、`_resolveMainPage`、`attachWsTrace`。
- Produces:
  - `async _deviceFrame()`:返回挂 clawDeviceService 的 frame(遍历 mainPage.frames());无则 null。
  - `async listDevices() -> [{vm_id,label,name,status,device_type}]`。
  - `async currentVmId() -> str|null`。
  - `async switchTo(vmId) -> {ok, url}`:切到目标 vm,等就绪;目标离线且非云→抛错;云→尝试 startCloudDevice;切换后重挂 wsTrace + 更新 mainPage。

- [ ] **Step 1: 加 _deviceFrame / listDevices / currentVmId**

在 `DesktopPool` 类里(getWsTrace 方法后)加:

```javascript
  // 找到挂着 window.clawDeviceService 的 frame(主文档或 work.n.cn iframe)
  async _deviceFrame() {
    if (!this.mainPage) return null;
    for (const f of this.mainPage.frames()) {
      const has = await f.evaluate(() => typeof window.clawDeviceService !== 'undefined').catch(() => false);
      if (has) return f;
    }
    return null;
  }

  // 读客户端设备(vm)列表。返回规整后的数组;读不到返回 []。
  async listDevices() {
    const frame = await this._deviceFrame();
    if (!frame) { this._warn('   未找到 clawDeviceService(读不到设备列表)'); return []; }
    const list = await frame.evaluate(async () => {
      const svc = window.clawDeviceService;
      let arr = [];
      try { arr = await svc.getDeviceList(true); } catch (e) { try { arr = svc._deviceList || []; } catch (_) {} }
      return (arr || []).map(d => ({
        vm_id: d.id, label: String(d.url || '').split('.')[0],
        name: d.name, status: d.status, device_type: d.type,
      }));
    }).catch(() => []);
    return list;
  }

  // 当前显示的设备 vm_id(优先 clawDeviceService.currentDevice,兜底 hostname 首段)
  async currentVmId() {
    const frame = await this._deviceFrame();
    if (frame) {
      const id = await frame.evaluate(() => {
        try { return window.clawDeviceService.currentDevice?.id || null; } catch (_) { return null; }
      }).catch(() => null);
      if (id) return id;
    }
    // 兜底:hostname 首段(33 位去首字符=32位核)
    try {
      const host = new URL(this.mainPage.url()).hostname;
      const seg = host.split('.')[0];
      return seg.length === 33 ? seg.slice(1) : seg;
    } catch (_) { return null; }
  }
```

- [ ] **Step 2: 加 switchTo**

在上面之后加:

```javascript
  // 切到指定 vm_id 的设备:已是当前则跳过;离线云设备尝试唤醒;切换后等就绪并重挂 wsTrace。
  async switchTo(vmId) {
    if (!vmId) return { ok: true, url: this.mainPage.url() };
    const cur = await this.currentVmId();
    if (cur === vmId) return { ok: true, url: this.mainPage.url() };

    let devices = await this.listDevices();
    let dev = devices.find(d => d.vm_id === vmId);
    if (!dev) throw new Error(`目标设备 ${vmId} 不在客户端列表`);

    const online = (s) => s === 'online' || s === 'active';
    // 离线处理:云设备(type=0)尝试 API 唤醒并轮询上线;其它类型无法远程唤醒 → 抛错(上层 fail-closed)
    if (!online(dev.status)) {
      if (dev.device_type === 0) {
        this._log(`   目标云设备离线,尝试唤醒 ${vmId} ...`);
        const frame = await this._deviceFrame();
        await frame.evaluate((id) => { try { window.clawDeviceService.startCloudDevice(id); } catch (_) {} }, vmId).catch(() => {});
        const deadline = Date.now() + 60000;
        while (Date.now() < deadline) {
          await this._sleep(3000);
          devices = await this.listDevices();
          dev = devices.find(d => d.vm_id === vmId) || dev;
          if (online(dev.status)) break;
        }
        if (!online(dev.status)) throw new Error(`云设备 ${vmId} 唤醒后仍未上线`);
      } else {
        throw new Error(`目标设备 ${vmId} 离线(type=${dev.device_type},无法远程唤醒)`);
      }
    }

    // 注入切换:location.assign 到 <label>.work.n.cn/claw?vm_id=<label>(实测 isInIframe=false 路径)
    const frame = await this._deviceFrame();
    const label = dev.label;
    await frame.evaluate((lb) => {
      const origin = location.origin.replace(/\/\/[^.]+\./, '//' + lb + '.');
      window.location.assign(`${origin}/claw?vm_id=${encodeURIComponent(lb)}`);
    }, label).catch(() => {});

    // 等 URL 切到目标 vm(host 首段含 label)
    const deadline = Date.now() + 30000;
    let ok = false;
    while (Date.now() < deadline) {
      await this._sleep(1000);
      try {
        const host = new URL(this.mainPage.url()).hostname;
        if (host.split('.')[0].includes(label)) { ok = true; break; }
      } catch (_) {}
    }
    if (!ok) throw new Error(`切换到 ${vmId} 后 URL 未到位(可能环境不同/走了父壳),当前 ${this.mainPage.url()}`);

    // 导航后重新 resolve 主 page + 等对话输入框就绪 + 重挂 wsTrace
    await this._sleep(2000);
    this._wsTrace = null; // 允许重挂(挂在新导航的 page 上,争取抓到切换后新建的对话 WS)
    this.mainPage = await this._resolveMainPage(this.readyTimeout);
    // 确认当前 vm 就是目标 + 输入框可用
    const nowVm = await this.currentVmId();
    if (nowVm !== vmId) throw new Error(`切换后当前设备 ${nowVm} != 目标 ${vmId}`);
    this._log(`   ✅ 已切到设备 ${dev.name || vmId} (${this.mainPage.url()})`);
    return { ok: true, url: this.mainPage.url() };
  }
```

> 注:`_resolveMainPage`(第 189-192 行)里 `if (!this._wsTrace)` 才挂 wsTrace;switchTo 前置 `this._wsTrace=null` 使其在新 page 上重挂。

- [ ] **Step 3: 真机验证脚本**

创建 `D:\code\ai-eval-cli-yt\tmp_test_pool_devices.js`:

```javascript
// 真机验证 DesktopPool 的 listDevices/currentVmId/switchTo(本机 lili-win,客户端 9222 开着)
const DesktopPool = require('./src/desktop-pool');
const Logger = require('./src/logger');
const config = require('./config/default.config.js');

(async () => {
  const logger = new Logger();
  const pool = new DesktopPool(config.desktop, config.platform, logger);
  await pool.init();
  console.log('当前 vm:', await pool.currentVmId());
  const list = await pool.listDevices();
  console.log('设备数:', list.length);
  list.forEach(d => console.log(`  ${d.status} ${d.name} vm_id=${d.vm_id} label=${d.label} type=${d.device_type}`));
  // 挑一个在线且非当前的切过去,再切回
  const cur = await pool.currentVmId();
  const online = list.filter(d => (d.status==='online'||d.status==='active') && d.vm_id !== cur);
  if (online.length) {
    const t = online[0];
    console.log(`\n切到 ${t.name} ...`);
    console.log(await pool.switchTo(t.vm_id));
    console.log('切回', cur, '...');
    console.log(await pool.switchTo(cur));
  } else {
    console.log('无其它在线设备可测切换');
  }
  await pool.close();
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
```

- [ ] **Step 4: 跑真机验证**

Run: `cd /d/code/ai-eval-cli-yt && node tmp_test_pool_devices.js`
Expected: 打印设备列表(≥1);切换到另一在线设备成功(打印 ✅ 已切到)、再切回成功。若报错按信息修 switchTo。

- [ ] **Step 5: 删验证脚本**

Run: `cd /d/code/ai-eval-cli-yt && rm -f tmp_test_pool_devices.js`
(CLI 非 git,不提交;改动为文件交付。)

---

### Task 6: platform-client.js 加设备上报

**Files:**
- Modify: `D:\code\ai-eval-cli-yt\src\platform-client.js`

**Interfaces:**
- Consumes: 现有 `_api`、`this.runnerId`。
- Produces: `async reportDevices(devices) -> {reported}`。

- [ ] **Step 1: 加 reportDevices**

`src/platform-client.js` 的 `report` 方法后加:

```javascript
  // 上报本执行机连上的客户端设备(vm)列表,供平台前端下发时下拉选。
  reportDevices(devices) {
    return this._api('POST', '/api/eval-devices/report', { runner: this.runnerId, devices: devices || [] });
  }
```

- [ ] **Step 2: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -e "require('./src/platform-client'); console.log('OK')"`
Expected: `OK`(模块可加载,无语法错)。

---

### Task 7: bin/ai-eval.js platform 命令编排(上报 + 切设备 + fail-closed)

**Files:**
- Modify: `D:\code\ai-eval-cli-yt\bin\ai-eval.js`(platform 命令的 runOnce)

**Interfaces:**
- Consumes: `pool.listDevices()`/`pool.switchTo()`/`pool.getMainPage()`(Task5)、`client.reportDevices()`(Task6)。
- Produces: platform 每轮上报设备;每条 run 执行前按 target_device 切设备,切换失败 fail-closed。

- [ ] **Step 1: runOnce 里上报设备**

`bin/ai-eval.js` 的 platform 命令 `runOnce` 内,`await pool.init();` 之后、`const wsTrace = pool.getWsTrace();` 之前加:

```javascript
      // 上报本机客户端设备(vm)列表,供平台前端下发时下拉选(失败不阻断执行)
      try {
        const devices = await pool.listDevices();
        if (devices.length) { await client.reportDevices(devices); logger.info(`已上报 ${devices.length} 个客户端设备`); }
      } catch (e) { logger.warn(`上报设备列表失败(不影响执行): ${e.message}`); }
```

- [ ] **Step 2: 每条 run 前切设备(重建 runner 绑新 page)**

`bin/ai-eval.js` platform 命令的 `for (const item of pending)` 循环里,`await client.claim(item.run_id);` 成功之后、构造 `testCase` 之前(或紧接 dialog_options 处理之前),加切设备逻辑。

找到 claim 后的位置,在附件检查之前加:

```javascript
        // 切到本题指定的目标设备(vm)。空=不切,用当前设备(向后兼容)。
        const targetDevice = item.target_device || null;
        if (targetDevice) {
          try {
            await pool.switchTo(targetDevice);
          } catch (e) {
            logger.warn(`run ${item.run_id} 切换设备失败,fail-closed: ${e.message}`);
            try {
              await client.report(item.run_id, { status: 'failed', reason: `切换目标设备失败: ${e.message}` });
            } catch (er) { logger.error(`report failed 失败 run ${item.run_id}: ${er.message}`); }
            continue;
          }
        }
```

- [ ] **Step 3: 切换后 runner 用新 mainPage**

因为 `switchTo` 会导航、更新 `pool.mainPage`,原 `runner`(在循环外构造,持旧 page 引用)会操作旧 page。改为:**每条 run 若切了设备,就重建 runner 绑新 page**。

在 Task7-Step2 的切换成功分支后(switchTo 成功、未 continue),重建 runner。把循环外那次 `const runner = new DesktopRunner(...)` 保留作默认(不切设备时用),并在切换成功后重建:

```javascript
        // 切换会导航、更新 pool.mainPage;重建 runner 绑定新主 page,避免操作陈旧 page。
        let curRunner = runner;
        if (targetDevice) {
          curRunner = new DesktopRunner(pool.getContext(), pool.getMainPage(), config.platform, config.execution, logger);
        }
```

并把该 run 后续用 `runner.runOne(...)` 的调用改为 `curRunner.runOne(...)`。同理 wsTrace 在切换后要重新取:切换分支后加

```javascript
        const curWsTrace = targetDevice ? pool.getWsTrace() : wsTrace;
```

并把该 run 的 `wsTrace.reset()` / `wsTrace.buildTrace(...)` 改用 `curWsTrace`。

> 完整顺序(claim 成功后):① 读 targetDevice → ② 若有则 switchTo(失败 fail-closed continue)→ ③ 重建 curRunner/curWsTrace → ④ 附件检查 → ⑤ dialog_options → ⑥ curWsTrace.reset() → ⑦ curRunner.runOne → ⑧ curWsTrace.buildTrace → ⑨ report + uploadTrace。

- [ ] **Step 4: 真机联调(platform --once)**

前置:平台后端在跑(11.120.81.7:4173)、CLI `.env` 已配 BASE_URL/RUNNER_TOKEN/RUNNER_ID;在平台前端下发一条指定"云龙虾A"(在线设备)的 eval_query 到 lili-win。

Run: `cd /d/code/ai-eval-cli-yt && node bin/ai-eval.js platform --once`
Expected 日志链:`已上报 N 个客户端设备` → `拉到 M 条待执行` → `✅ 已切到设备 云龙虾A` → 对话执行 → `✅ 回写 run X (done, ws=...)`。若切换失败会 `切换设备失败,fail-closed`;若对话跑通则 done。

- [ ] **Step 5: 语法自检**

Run: `cd /d/code/ai-eval-cli-yt && node -c bin/ai-eval.js && echo OK`
Expected: `OK`(无语法错)。

---

## Self-Review 结论

- **Spec 覆盖**:§4 数据模型→Task1;§5 端点→Task2;§6 enqueue→Task3;§7 CLI(desktop-pool/platform-client/bin)→Task5/6/7;§8 前端→Task4;§9 ws 重挂→Task5-Step2(switchTo 内重挂);§10 迁移→Task1。全覆盖。
- **类型一致**:`target_device`(vm_id, String(64))贯穿模型/schema/enqueue/_to_out/CLI item.target_device;`switchTo(vmId)`/`listDevices()` 返回 `{vm_id,label,name,status,device_type}` 全程一致;label=device.url 首段。
- **无占位**:每步含实际代码/命令/期望。
- **风险已在 spec §11 记录**:切换环境差异(isInIframe)→ switchTo 校验 URL 到位否则抛错 fail-closed;page 陈旧→Task7 重建 runner。
