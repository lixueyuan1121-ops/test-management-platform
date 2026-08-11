# 管理员任务验收进度汇总 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给管理员任务分配页（Tasks.vue）加一列只读的验收进度汇总（迷你进度条 + 通过/总数 + 失败飘红）和可展开的只读明细，补齐验收清单的管理员视角。

**Architecture:** 后端新增一个只读批量聚合端点 `GET /api/tasks/checklist-summary`（对 checklist_item 按 task_id+exec_status 现算 GROUP BY，返回 map），加进现有 `app/api/checklist.py`；前端 Tasks.vue 加「验收进度」列 + `type="expand"` 可展开行，展开时懒加载复用已有的 `GET /api/tasks/{tid}/checklist` 明细端点。纯只读，不新增写路径。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（后端），Vue3 + ElementPlus（前端）。无测试框架——验证是手动端到端（TestClient 冒烟 + 浏览器）。

## Global Constraints

- 统一响应信封：所有后端接口用 `app/schemas/common.py` 的 `ok(data)` 返回，`code==0` 成功；前端 `api/http.js` 拦截器已解包，`api/index.js` 函数返回值即 `data` 本身。
- 不用 `response_model`：手写返回 dict（枚举取 `.value`）。
- 权限：路径/query 来源的 project_id 用非注入版 `assert_project_role(db, user, project_id, roles)`；只读端点用 `_ALL_ROLES`（admin/member/guest）。
- 聚合现算，不建统计表（与 stats.py 一致）。
- 前端 `dist/` 提交进 git（服务器无 Node，同源预构建静态）：改前端源码后必须 `cd frontend && npm run build`，dist 与源码一起提交。
- 三态结果配色沿用既有口径：通过=success(青绿 `--tech-signal`)、失败=danger(`--tech-danger`)、阻塞=warning(`--tech-warn`)、待测=info(灰)。
- 构建用 node v22：`export PATH="/Users/lixueyuan/.nvm/versions/node/v22.3.0/bin:$PATH"`。
- 验证脚本用仓库 venv 解释器 `/Users/lixueyuan/code/test-management-platform-fresh/backend/.venv/bin/python`（系统 python 3.9 不可用）；造数据注意 `Project` 必填 `code`（NOT NULL unique）、`Task.assigned_date` 用 `date` 对象（`from datetime import date`）非字符串。

---

## File Structure

**后端**
- `app/api/checklist.py`（改）：末尾追加只读端点 `list_checklist_summary`（`GET /api/tasks/checklist-summary`）。复用文件内已有的 `_ALL_ROLES`、`assert_project_role`、`ok`、模型 `ChecklistItem`/`Task`。

**前端**
- `frontend/src/api/index.js`（改）：加薄封装 `getChecklistSummary(project_id, date)`；`getTaskChecklist(tid)` 已存在，复用。
- `frontend/src/views/Tasks.vue`（改）：加「验收进度」列 + `type="expand"` 展开行 + 懒加载明细逻辑。
- `frontend/dist/`（改）：`npm run build` 重建，随源码提交。

---

### Task 1: 后端批量聚合端点

在现有 `app/api/checklist.py` 末尾加只读端点 `GET /api/tasks/checklist-summary`，一次返回该项目该日各任务的验收汇总。交付后可独立验证：造带清单/不带清单的任务，调端点返回 map 只含有清单项的任务、四态计数正确。

**Files:**
- Modify: `backend/app/api/checklist.py`（末尾追加端点）

**Interfaces:**
- Consumes（本文件已有，无需新增 import；`func` 见下）：`assert_project_role`、`get_current_user`、`get_db`、`ok`、`_ALL_ROLES`、模型 `ChecklistItem`/`Task`、枚举 `ChecklistStatus`。
- Produces：端点 `GET /api/tasks/checklist-summary?project_id={int}&date={YYYY-MM-DD}`，返回 `ok({ "<task_id>": {"total":int,"passed":int,"failed":int,"blocked":int,"pending":int}, ... })`，key 为 task_id 字符串，只含有清单项的任务。

- [ ] **Step 1: 确认 import（func / date / Query）**

打开 `backend/app/api/checklist.py`，确认顶部 import 含 `Query`（来自 fastapi）、`func`（来自 sqlalchemy）、`date`（来自 datetime）。若缺则补：

- fastapi 那行确保有 `Query`：`from fastapi import APIRouter, Depends, HTTPException, Query, status`
- 顶部加（若无）：`from datetime import date` 和 `from sqlalchemy import func`

（注：文件已 import `datetime`（用于 `datetime.utcnow()`），但聚合端点需要 `date` 类型注解与 `func`；按实际缺失补，已有则不动。）

- [ ] **Step 2: 加聚合端点**

在 `backend/app/api/checklist.py` 末尾追加：

```python
@router.get("/tasks/checklist-summary")
def list_checklist_summary(
    project_id: int = Query(...),
    date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """管理员任务表用：某项目某日各任务的验收进度汇总（只读，SQL 现算聚合）。

    返回 map：{ "<task_id>": {total, passed, failed, blocked, pending} }，
    只含有清单项的任务；无清单项的任务不出现（前端据此显示 —）。
    """
    assert_project_role(db, user, project_id, _ALL_ROLES)
    task_ids = [
        tid for (tid,) in
        db.query(Task.id)
        .filter(Task.project_id == project_id, Task.assigned_date == date)
        .all()
    ]
    if not task_ids:
        return ok({})
    rows = (
        db.query(ChecklistItem.task_id, ChecklistItem.exec_status, func.count(ChecklistItem.id))
        .filter(ChecklistItem.task_id.in_(task_ids))
        .group_by(ChecklistItem.task_id, ChecklistItem.exec_status)
        .all()
    )
    summary: dict[str, dict] = {}
    for tid, st, cnt in rows:
        key = str(tid)
        rec = summary.setdefault(key, {"total": 0, "passed": 0, "failed": 0, "blocked": 0, "pending": 0})
        # st 是 ChecklistStatus 枚举；取 .value 作为 key（pending/passed/failed/blocked）
        rec[st.value] = rec.get(st.value, 0) + int(cnt)
        rec["total"] += int(cnt)
    return ok(summary)
```

**注意端点路径顺序**：本端点路径 `/api/tasks/checklist-summary` 与已有 `/api/tasks/{tid}/checklist` 同前缀。FastAPI 按声明顺序匹配，`checklist-summary` 是静态段、`{tid}` 是路径参数——静态路由需在参数路由**之前**声明才不会被 `{tid}` 吞掉。检查文件内 `@router.get("/tasks/{tid}/checklist")` 的位置：**把本 `checklist-summary` 端点放在 `get_task_checklist`（`/tasks/{tid}/checklist`）之前**。若追加到末尾会排在其后，则 `GET /api/tasks/checklist-summary` 可能被 `/tasks/{tid}/checklist` 以 `tid="checklist-summary"` 误匹配再 404。因此：将本端点函数定义**插入到 `get_task_checklist` 函数定义的正上方**，而非文件末尾。

- [ ] **Step 3: 手动端到端验证**

在 `backend/` 下用仓库 venv 解释器跑（造：一个项目、同日 3 个任务——A 挂清单含 passed/failed/blocked/pending 各态、B 挂清单全 pending、C 不挂清单；验证 map 只含 A/B，计数对，C 不出现）：

```bash
cd backend
rm -f test_platform.db
.venv/bin/python -c "
from datetime import date
from fastapi.testclient import TestClient
from app.main import app, init_db
from app.db.session import SessionLocal
from app.models import User, Project, Task, AiTask, TestCase, ChecklistItem
from app.core.enums import ReviewStatus, AiInputType, AiTaskStatus, TaskStatus, TaskPriority, ChecklistStatus
from app.core.security import create_access_token
init_db()
s = SessionLocal()
admin = s.query(User).filter_by(is_platform_admin=True).first()
p = Project(name='验收进度冒烟', code='CLPROG', status='active'); s.add(p); s.commit(); s.refresh(p)
D = date(2026,8,11)
def mk_task(title):
    t = Task(project_id=p.id, assigned_by=admin.id, assigned_to=admin.id, title=title, priority=TaskPriority.p1, assigned_date=D, status=TaskStatus.pending)
    s.add(t); s.commit(); s.refresh(t); return t
at = AiTask(project_id=p.id, user_id=admin.id, input_type=AiInputType.text, status=AiTaskStatus.done); s.add(at); s.commit(); s.refresh(at)
def mk_case():
    tc = TestCase(ai_task_id=at.id, project_id=p.id, title='tc', review_status=ReviewStatus.adopted); s.add(tc); s.commit(); s.refresh(tc); return tc
tA = mk_task('A'); tB = mk_task('B'); tC = mk_task('C')  # C 不挂清单
# A: 4 项 各态
for stt in [ChecklistStatus.passed, ChecklistStatus.failed, ChecklistStatus.blocked, ChecklistStatus.pending]:
    s.add(ChecklistItem(task_id=tA.id, test_case_id=mk_case().id, project_id=p.id, exec_status=stt))
# B: 2 项 全 pending
for _ in range(2):
    s.add(ChecklistItem(task_id=tB.id, test_case_id=mk_case().id, project_id=p.id, exec_status=ChecklistStatus.pending))
s.commit()
tok = create_access_token(str(admin.id)); c = TestClient(app); H={'Authorization':f'Bearer {tok}'}
r = c.get('/api/tasks/checklist-summary', headers=H, params={'project_id':p.id,'date':'2026-08-11'})
d = r.json()['data']
print('status', r.status_code)
print('A in map:', str(tA.id) in d, d.get(str(tA.id)))
print('B in map:', str(tB.id) in d, d.get(str(tB.id)))
print('C not in map:', str(tC.id) not in d)
a = d[str(tA.id)]
print('A total==4:', a['total']==4, 'passed==1:', a['passed']==1, 'failed==1:', a['failed']==1, 'blocked==1:', a['blocked']==1, 'pending==1:', a['pending']==1)
b = d[str(tB.id)]
print('B total==2 pending==2:', b['total']==2 and b['pending']==2)
# 空任务集：换个没任务的日期
r2 = c.get('/api/tasks/checklist-summary', headers=H, params={'project_id':p.id,'date':'2020-01-01'})
print('empty day -> {}:', r2.json()['data']=={})
s.close()
"
rm -f test_platform.db
```

Expected:
- `status 200`
- `A in map: True {...}`、`B in map: True {...}`、`C not in map: True`
- `A total==4: True passed==1: True failed==1: True blocked==1: True pending==1: True`
- `B total==2 pending==2: True`
- `empty day -> {}: True`

- [ ] **Step 4: 提交**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add backend/app/api/checklist.py
git commit -m "feat(checklist): 管理员任务验收进度批量聚合端点 /tasks/checklist-summary"
```

---

### Task 2: 前端进度列 + 可展开只读明细 + dist

`api/index.js` 加 `getChecklistSummary`；`Tasks.vue` 加「验收进度」列（进度条 + 通过/总数 + 失败飘红，无清单 `—`）+ `type="expand"` 展开行懒加载只读明细。改完 `npm run build` 重建 dist 一起提交。交付后浏览器验证：管理员任务表出现进度列、展开出只读明细。

**Files:**
- Modify: `frontend/src/api/index.js`（加 1 个封装）
- Modify: `frontend/src/views/Tasks.vue`
- Modify: `frontend/dist/**`（`npm run build` 产物）

**Interfaces:**
- Consumes：后端 `GET /api/tasks/checklist-summary?project_id&date`（Task 1）返回 map；已有 `GET /api/tasks/{tid}/checklist`（返回清单项数组，每项含 `title`/`category`/`exec_status` 等）。
- Produces：`getChecklistSummary(project_id, date)`。

- [ ] **Step 1: api 薄封装**

改 `frontend/src/api/index.js`，在验收清单相关封装附近（`getTaskChecklist` 那几行旁）加一行：

```javascript
export const getChecklistSummary = (project_id, date) => http.get('/tasks/checklist-summary', { params: { project_id, date } })
```

- [ ] **Step 2: Tasks.vue 引入 api + 状态**

改 `frontend/src/views/Tasks.vue` 的 `<script setup>`：

(a) import 那行（现为 `import { listProjects, listMembers, listTasks, createTask, updateTask, deleteTask, copyYesterday } from '@/api'`）末尾加 `getChecklistSummary, getTaskChecklist`：

```javascript
import { listProjects, listMembers, listTasks, createTask, updateTask, deleteTask, copyYesterday, getChecklistSummary, getTaskChecklist } from '@/api'
```

(b) 加一个三态结果标签映射（放在 `STATUS_META` 之后）：

```javascript
// 验收清单项三态结果配色（沿用全局口径：通过青绿/失败红/阻塞warn/待测灰）
const EXEC_META = {
  passed: { label: '通过', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  blocked: { label: '阻塞', type: 'warning' },
  pending: { label: '待测', type: 'info' },
}
```

- [ ] **Step 3: Tasks.vue 的 load() 合并汇总**

改 `frontend/src/views/Tasks.vue` 的 `load()` 函数。现有实现：

```javascript
async function load() {
  if (!pid.value) return
  await loadMembers()
  loading.value = true
  try { tasks.value = await listTasks({ project_id: pid.value, date: date.value }) }
  finally { loading.value = false }
}
```

替换为（取任务后并行拉汇总、merge 进各行 `_summary`；汇总失败不阻断表格）：

```javascript
async function load() {
  if (!pid.value) return
  await loadMembers()
  loading.value = true
  try {
    const rows = await listTasks({ project_id: pid.value, date: date.value })
    let summary = {}
    try { summary = await getChecklistSummary(pid.value, date.value) } catch { summary = {} }
    tasks.value = rows.map((t) => ({ ...t, _summary: summary[String(t.id)] || null, _items: null, _itemsLoading: false }))
  } finally { loading.value = false }
}
```

- [ ] **Step 4: Tasks.vue 加展开明细的懒加载函数**

在 `load()` 之后加一个展开回调（首次展开某行时拉明细缓存到 `_items`）：

```javascript
async function onExpandChange(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.id === row.id)
  if (!isExpanded) return
  if (!row._summary) return          // 无清单项：不请求，展开区显示占位
  if (row._items !== null) return    // 已缓存：不重复请求
  row._itemsLoading = true
  try { row._items = await getTaskChecklist(row.id) }
  catch { row._items = [] }
  finally { row._itemsLoading = false }
}
```

- [ ] **Step 5: Tasks.vue 表格加 row-key + 展开列 + 进度列**

改 `frontend/src/views/Tasks.vue` 的 `<template>`。

(a) 给 `<el-table>` 加 `row-key="id"` 和 `@expand-change`。现有开头：

```html
<el-table :data="tasks" v-loading="loading" size="small" empty-text="该日无任务">
```

改为：

```html
<el-table :data="tasks" v-loading="loading" size="small" empty-text="该日无任务" row-key="id" @expand-change="onExpandChange">
```

(b) 在 `<el-table>` 内**第一列位置**（`prop="title"` 那列之前）加展开列：

```html
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="cl-expand">
            <div v-if="!row._summary" class="cl-empty">该任务暂无验收清单</div>
            <el-table v-else :data="row._items || []" v-loading="row._itemsLoading" size="small"
                      empty-text="加载中或无验收项">
              <el-table-column prop="title" label="测试点" min-width="180" show-overflow-tooltip />
              <el-table-column prop="category" label="维度" width="80" />
              <el-table-column label="结果" width="90">
                <template #default="{ row: it }">
                  <el-tag :type="EXEC_META[it.exec_status]?.type || 'info'" size="small" effect="light">
                    {{ EXEC_META[it.exec_status]?.label || it.exec_status }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
```

(c) 在「状态」列（`label="状态"`）之后、「操作」列（`label="操作"`）之前，加「验收进度」列：

```html
      <el-table-column label="验收进度" width="190">
        <template #default="{ row }">
          <div v-if="row._summary" class="cl-prog">
            <el-progress
              :percentage="row._summary.total ? Math.round(row._summary.passed / row._summary.total * 100) : 0"
              :stroke-width="8" :show-text="false" color="var(--tech-signal)" style="width:70px"
            />
            <span class="cl-nums">{{ row._summary.passed }}/{{ row._summary.total }}</span>
            <el-tag v-if="row._summary.failed" type="danger" size="small" effect="light">失败{{ row._summary.failed }}</el-tag>
          </div>
          <span v-else class="cl-dim">—</span>
        </template>
      </el-table-column>
```

(d) `<style scoped>` 末尾加：

```css
.cl-prog { display: flex; align-items: center; gap: 8px; }
.cl-nums { font-family: var(--tech-mono, monospace); font-size: 12px; color: var(--tech-fg, #1a1d21); }
.cl-dim { color: var(--tech-dim, #9aa3b2); }
.cl-expand { padding: 8px 16px; }
.cl-empty { color: var(--tech-dim, #9aa3b2); font-size: 13px; padding: 4px 0; }
```

- [ ] **Step 6: 重建 dist**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh/frontend
export PATH="/Users/lixueyuan/.nvm/versions/node/v22.3.0/bin:$PATH"
npm run build
```

Expected: 构建成功，`dist/` 更新，无报错（尤其无 Tasks.vue 模板编译错误）。

- [ ] **Step 7: 手动验证（浏览器，可选但尽量做）**

起后端（`cd backend && .venv/bin/uvicorn app.main:app --port 8000`），前端 dev 或访问同源静态。以管理员登录 → 任务分配页：
- 有验收清单的任务行出现进度条 + `通过/总数`，失败非零飘红「失败N」；无清单的行显示 `—`。
- 点行展开：有清单的行懒加载出只读明细（测试点/维度/三态 tag，配色对，无操作按钮）；无清单的行显示「该任务暂无验收清单」。
- 换项目/日期后进度列与展开刷新正确。
若起服务不便，至少确认 `npm run build` 成功（会报出模板错误）。把做了哪种验证写进报告。

- [ ] **Step 8: 提交（含 dist）**

```bash
cd /Users/lixueyuan/code/test-management-platform-fresh
git add frontend/src/api/index.js frontend/src/views/Tasks.vue frontend/dist
git commit -m "feat(checklist): 管理员任务表验收进度列 + 可展开只读明细 + 重建 dist"
```

---

## Self-Review 记录

**Spec 覆盖**（对 `docs/superpowers/specs/2026-08-11-checklist-progress-admin-design.md` 逐节）：
- §3.1 聚合端点（权限 _ALL_ROLES、GROUP BY、返回 map 只含有清单项的任务、空任务集返回 {}）→ Task 1 全覆盖，含静态路由排序坑（放在 `/tasks/{tid}/checklist` 之前）。
- §3.2 明细端点复用不改 → Task 2 展开懒加载复用 `getTaskChecklist`，后端不动。
- §4.1 api 封装 → Task 2 Step1。§4.2 进度列（进度条+通过/总数+失败飘红、无清单 —）→ Task 2 Step5(c)。§4.3 展开只读明细懒加载+缓存 → Task 2 Step4/Step5(b)。§4.4 数据流 → Task 2 Step3/Step4。§4.5 dist → Task 2 Step6/Step8。
- §5 边界：summary 失败不阻断（Step3 try/catch）、无清单显示 —+占位（Step5）、明细失败占位（Step4 catch []）、权限复用、换项目/日期 load 重拉（_items 随 map 重建清空）。

**占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码块；验证步骤含可跑命令 + Expected。

**类型/签名一致性**：`getChecklistSummary(project_id, date)` 返回 map、key 为 String(task_id) → 前端 `summary[String(t.id)]` 对齐；`_summary` 结构 `{total,passed,failed,blocked,pending}` 贯穿后端返回与前端 Step5 渲染；`EXEC_META` 的 key（passed/failed/blocked/pending）与清单项 `exec_status` 值一致；`onExpandChange` 用 `_items===null` 判缓存、`_summary` 判是否请求，与 Step3 初始化（`_items:null,_summary:...`）对齐。
