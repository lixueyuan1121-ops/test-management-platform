# 管理员任务验收进度汇总 设计

- 日期：2026-08-11
- 状态：已评审，待实现
- 分支：将新建 feat/checklist-progress-admin（从 main 切）
- 相关：验收清单（`checklist_item`、`GET /api/tasks/{tid}/checklist`）、`Tasks.vue`（管理员任务分配页）

## 1. 目标与背景

「测试点回流 Task 验收清单」已让成员在 MyReports 逐条勾执行结果，但管理员在 `Tasks.vue`（任务分配页）看不到每个任务的验收执行情况——测了几条、通过/失败多少、有没有踩雷。本功能给管理员任务表加一列**只读的验收进度汇总**（迷你进度条 + `通过/总数` + 失败飘红），并支持**展开某行看该任务的验收明细**（只读，不勾选）。补齐验收闭环的管理员视角。同时命中三轴：向上汇报有数据、日常盯进度有用、纯读不碰新机制。

## 2. 范围

### 做
- 新增只读批量聚合端点 `GET /api/tasks/checklist-summary?project_id&date`：一次返回该批任务每个的验收汇总（total/passed/failed/blocked/pending）。
- `Tasks.vue` 加「验收进度」列：迷你进度条（通过占比）+ 文字 `通过/总数` + 失败数非零飘红；无清单项显示淡灰 `—`。
- 可展开行（`type="expand"`）：展开时懒加载并只读渲染该任务的验收明细（测试点标题/维度/三态结果 tag），管理员不勾选、无操作。
- `api/index.js` 加薄封装 `getChecklistSummary(project_id, date)`。

### 不做（YAGNI）
- 页面顶部「验收完成率」全局汇总（属另一方向，单独立项）。
- 管理员在此页补挂/勾选/转遗留（那些是成员在 MyReports 做的）。
- 展开明细里的排序/筛选/分页。

## 3. 后端

### 3.1 新增端点（加进现有 `app/api/checklist.py`）
`GET /api/tasks/checklist-summary`

- 查询参数：`project_id: int`（必填）、`date: date`（必填，`YYYY-MM-DD`）。
- 权限：`assert_project_role(db, user, project_id, _ALL_ROLES)`（admin/member/guest 可看，与现有 checklist 查询端点口径一致，guest 只读无害）。
- 逻辑：
  1. 查该项目该日任务 id：`Task.project_id == project_id AND Task.assigned_date == date` → `task_ids`。
  2. 若 `task_ids` 为空 → 返回 `ok({})`。
  3. 对 `checklist_item` 做一次分组聚合（SQL 现算，不建统计表，与 stats.py 风格一致）：
     `db.query(ChecklistItem.task_id, ChecklistItem.exec_status, func.count(ChecklistItem.id)).filter(ChecklistItem.task_id.in_(task_ids)).group_by(ChecklistItem.task_id, ChecklistItem.exec_status).all()`
  4. 在 Python 侧汇总成 map：每个出现的 task_id → `{total, passed, failed, blocked, pending}`（四态计数 + total=四者和）。**只含有清单项的任务**（没挂清单的 task_id 不出现在 map 里）。
- 返回信封：`ok({ "<task_id>": {"total":8,"passed":5,"failed":2,"blocked":0,"pending":1}, ... })`（key 为 task_id 的字符串形式，JSON 对象 key 天然是字符串）。

### 3.2 明细端点复用（不改）
展开行调用**已有的** `GET /api/tasks/{tid}/checklist`（返回清单项 + 关联 test_case 的 title/category/steps/expected/priority + exec_status），后端无需改动。

## 4. 前端

### 4.1 `api/index.js` 薄封装
- `getChecklistSummary(project_id, date)` → `GET /tasks/checklist-summary?project_id&date`（返回已解包的汇总 map）。
- `getTaskChecklist(tid)` 已存在（验收清单功能已加），复用。

### 4.2 `Tasks.vue` 验收进度列
- 在「状态」列之后、「操作」列之前加 `el-table-column label="验收进度"`（宽约 180）。
- 每行读 `row._summary`（load 时 merge 进来）：
  - 有汇总（`_summary.total > 0`）：迷你进度条（`el-progress` 或自绘细条，通过占比 = `passed/total`，色用 `--tech-signal` 青绿）+ 文字 `{passed}/{total}`；`failed > 0` 时旁边飘红标签 `失败{failed}`（`--tech-danger`）。
  - 无汇总：淡灰 `—`（`--tech-dim`）。
- 可展开：给 `el-table` 加 `row-key` + 一列 `type="expand"`。无汇总的行展开区显示占位「该任务暂无验收清单」（或通过 `_summary` 判定不显示展开箭头——ElementPlus 用 `expand` 列时统一有箭头，故采用展开区占位提示的方式，简单一致）。

### 4.3 展开明细（懒加载 + 只读）
- 展开行内嵌只读小表：每条验收项显示 测试点标题 / 维度(category) / 结果 tag。
- 结果 tag 配色沿用三态口径：通过=`success`(青绿)、失败=`danger`、阻塞=`warning`、待测=`info`(灰)。
- **只读**：无勾选按钮、无转遗留、无任何写操作（与 MyReports 成员视角区分）。
- 懒加载：展开时若 `row._items` 未加载 → 调 `getTaskChecklist(row.id)` → 存入 `row._items`，展开区 `v-loading`；缓存后重复展开不重复请求。

### 4.4 数据流
```
load() 取 tasks 后 → 并行 getChecklistSummary(pid, date)
     → 汇总 map 按 task_id merge 进各行 row._summary（无则不设，显示 —）
展开某行 → 首次 getTaskChecklist(row.id) → row._items 缓存 → 只读渲染明细
```

### 4.5 重建 dist
- 改前端源码后 `cd frontend && npm run build`，dist 与源码一起提交（服务器无 Node 的既定约束）。

## 5. 错误处理与边界
- summary 请求失败：不阻断任务表（进度列降级为 `—`，任务分配功能照常可用）。
- 无清单项的任务：`_summary` 缺省 → 显示 `—`；展开区显示「该任务暂无验收清单」占位。
- 明细请求失败：展开区显示错误占位（沿用全局拦截器的 `ElMessage.error`），不影响表格。
- 权限：全程复用 `assert_project_role`；聚合端点 `_ALL_ROLES` 只读。
- 换项目/换日期（现有 `load()`）：重新拉 summary，清掉上一批的 `_items` 缓存（随 tasks 重建自然清空）。

## 6. 测试（手动端到端，本仓库无测试框架）
1. 聚合端点：造某项目某日多个任务，部分挂验收清单（含 passed/failed/blocked/pending 各态）、部分不挂 → 调 `/api/tasks/checklist-summary` 返回 map 只含有清单项的任务，四态计数与 total 正确；空任务集返回 `{}`；跨项目/无权限被拒。
2. 进度列：有清单项的行显示进度条 + `通过/总数`，失败非零飘红；无清单项显示 `—`。
3. 展开明细：展开有清单的行懒加载出只读明细（三态 tag 配色对），只读无操作按钮；重复展开不重复请求；展开无清单的行显示占位。
4. 边界：summary 请求失败时任务表照常、进度列降级 `—`；换项目/日期后 summary 与明细缓存刷新正确。
5. 前端：dist 重建生效。

## 7. 未决/后续（非本次）
- 验收完成率进战绩墙/overview 作为新指标。
- 验收进度的时间趋势（按周看某项目验收覆盖率变化）。
