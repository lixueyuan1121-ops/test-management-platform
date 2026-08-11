# 测试点回流 Task 验收清单 设计

- 日期：2026-08-11
- 状态：已评审，待实现
- 分支：将新建 feat/checklist-reflow（从 main 切）
- 相关：QA Copilot（`test_case`、采纳流程）、`Task`、`RemainingIssue`、`/stats/daily` 与 `/stats/overview`

## 1. 目标与背景

战绩墙已让 AI 生成的测试点可采纳、可统计，但采纳后的 `TestCase` 不进任务流程——成员执行仍靠脑子/Excel。本功能把采纳的测试点**回流成 Task 的验收清单**：成员逐条勾"通过/失败/阻塞"，失败一键转 `RemainingIssue`，从而串起"生成→采纳→执行→遗留"闭环。这是自动化演进阶梯的第 2 级（半自动），也是日常减负最实的一刀。

## 2. 范围

### 做
- 新增 `checklist_item` 表（关联 task↔test_case，自带执行状态）。
- 采纳测试点时：若其有 `task_id`，自动 upsert 成清单项（采纳的副作用）。
- 手动补挂：把已采纳、同项目的测试点手动加入某任务清单。
- 逐条勾执行结果（pending/passed/failed/blocked）。
- 失败一键转 `RemainingIssue`（放宽 issue 模型，允许任务直挂）。
- 统计口径兼容：`open_issues` 计"报告下的 + 任务直挂的"并集。
- 前端验收清单 UI（先挂 MyReports 成员视角）+ 手动补挂弹窗 + 失败转遗留弹窗。

### 不做（YAGNI）
- 清单项排序/拖拽、批量勾选、执行历史时间线。
- 管理员在 Tasks.vue 看验收进度（后续可加）。
- 严格"只有 assigned_to 能勾"（本版放给项目 member/admin 协作）。

## 3. 数据模型

### 新增 `checklist_item`（`app/models/`，create_all 自动建）
| 列 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `task_id` | FK→task(CASCADE) | 清单归属任务，index |
| `test_case_id` | FK→test_case(CASCADE) | 来源测试点，index |
| `project_id` | FK→project(CASCADE) | 冗余，便于权限/查询，index |
| `exec_status` | Enum(`pending`/`passed`/`failed`/`blocked`) | 默认 pending，server_default |
| `executed_by` | FK→user(SET NULL) 可空 | 谁勾的 |
| `executed_at` | DateTime 可空 | 执行时间（UTC） |
| `created_at` | DateTime | server_default now |
| 唯一约束 | `UNIQUE(task_id, test_case_id)` | 防重复挂 |

新增枚举 `ChecklistStatus(str, Enum)` 于 `app/core/enums.py`（不放 models）。

### 改 `remaining_issue`（放宽，加列不改语义）
- `report_id` 由 NOT NULL 改**可空**。
- 新增 `task_id`（FK→task(SET NULL) 可空）、`checklist_item_id`（FK→checklist_item(SET NULL) 可空）。
- 一条 issue 要么挂 report（旧路径），要么挂 task（新路径）。

### 迁移
- `checklist_item` 是新表，`create_all` 建。
- `remaining_issue` 改动走 `migrate.py` 手写：`ensure_issue_columns`（仿 `ensure_task_columns`）——加 `task_id`/`checklist_item_id` 列；`report_id` 放宽为可空对 SQLite 无需 DDL（SQLite 列约束宽松），对 MySQL 需 `MODIFY COLUMN ... NULL`——迁移里按方言处理（沿用 `migrate_task_status` 已有的 MySQL 分支模式）。
- `schema.sql` 同步：新增 `checklist_item` 建表 + `remaining_issue` 三处改动。
- 新模型在 `app/models/__init__.py` 汇总导入。

## 4. 后端接口

新增 router `app/api/checklist.py`（独立模块）。统一信封 `ok()`，手写 `_to_out(db,obj)->dict`，权限 `assert_project_role`。

### 4.1 采纳自动挂载（改现有 PATCH，不新增端点）
`PATCH /api/ai/testcases/{cid}`（现三态评审）增强：
- 当 `review_status` 设为 `adopted` 且 `test_case.task_id` 非空 → upsert 一条 `checklist_item`（`(task_id,test_case_id)` 幂等，已存在则跳过）。
- 当从 adopted 改为 rejected/pending → 删除对应 `checklist_item`（仅当该项仍是 pending 未执行；已执行过的保留，避免丢执行记录——实现时按此规则）。

### 4.2 `GET /api/tasks/{tid}/checklist`
取任务验收清单：清单项 + 关联 test_case 的 title/category/steps/expected/priority + exec_status/executed_by 名/executed_at。权限：任务所在项目 member/admin/guest 可看。

### 4.3 `POST /api/tasks/{tid}/checklist`
手动补挂。请求体 `{ test_case_ids: [int] }`，每个必须是已采纳（review_status==adopted）且同项目的 test_case，批量 upsert。权限：member/admin。返回新增/已存在的清单项列表。

### 4.4 `PATCH /api/checklist/{item_id}`
勾执行结果。请求体 `{ exec_status: 'passed'|'failed'|'blocked'|'pending' }`。写 `executed_by`=当前用户、`executed_at`=`datetime.utcnow()`（与项目其他时间戳对齐）；设回 pending 时清空 executed_at/by。权限：任务所在项目 member/admin（不限 assigned_to）。

### 4.5 `POST /api/checklist/{item_id}/to-issue`
失败转遗留。前置：该项 `exec_status=='failed'`。请求体 `{ title?, severity, owner?, external_ref? }`（title 缺省用 test_case.title）。创建 `RemainingIssue`：`report_id=None`、`task_id`=清单项的 task、`checklist_item_id`=item_id、`project_id` 带上、`status=open`。权限：member/admin。返回新建 issue。

### 4.6 统计口径兼容（改现有，不新增端点）
`/stats/daily` 与 `/stats/overview` 的 `open_issues` 聚合：从"仅 report_id in (...)"改为兼容两种来源——
- daily：该日报下的 issue（report 路径）+ 该项目该日相关任务直挂的 open issue。
- overview：可见项目的 open issue 并集（report 路径 + task 路径），去重按 issue id。
实现时明确去重，避免同一 issue 双算。

## 5. 前端

### 5.1 `api/index.js` 薄封装
- `getTaskChecklist(tid)`、`attachChecklist(tid, testCaseIds)`、`updateChecklistItem(itemId, exec_status)`、`checklistItemToIssue(itemId, payload)`。

### 5.2 验收清单 UI（挂 `MyReports.vue`，成员视角）
- 成员点开自己的任务 → 验收清单表格：测试点标题/维度/预期 + 三态按钮组（通过/失败/阻塞，配色沿用 Dashboard `.dtag`：通过青绿 `--tech-signal`、失败 danger `--tech-danger`、阻塞灰/warn）+ 失败项旁"转遗留"按钮。
- 勾选调 `updateChecklistItem`，用返回 data 回写本地行。
- 空清单显示占位提示。

### 5.3 手动补挂弹窗
- "添加测试点"按钮 → 弹窗列出该项目**已采纳但未进本任务清单**的 test_case，多选 → `attachChecklist`。

### 5.4 失败转遗留弹窗
- 点"转遗留"→ 小表单：标题（预填 test_case.title）、严重度（下拉，沿用 issues 的 severity 枚举）、负责人、external_ref（可选）→ `checklistItemToIssue`。

### 5.5 重建 dist
- 改前端源码后 `cd frontend && npm run build`，重建 dist 与源码一起提交（服务器无 Node 的既定约束）。

## 6. 数据流

```
生成：需求 → test_case(可带 task_id, review_status=pending)
采纳：PATCH review_status=adopted → 若有 task_id 自动 upsert checklist_item(pending)
     （或成员在任务页手动 attachChecklist 补挂已采纳的测试点）
执行：成员在 MyReports 任务清单逐条 PATCH exec_status(passed/failed/blocked)
转遗留：failed 项 → POST to-issue → RemainingIssue(report_id=null, task_id, checklist_item_id)
统计：/stats/daily、/stats/overview 的 open_issues 兼容 report 路径 + task 路径，去重
```

## 7. 错误处理与边界
- 补挂非采纳/跨项目 test_case → 400，跳过非法项或整体拒绝（实现时选整体校验并报错）。
- to-issue 时该项非 failed → 400。
- 采纳取消删除清单项：仅删仍 pending 的，已执行的保留（避免丢数据）。
- 唯一约束冲突：upsert 幂等，重复挂不报错。
- 权限：全程复用 assert_project_role；member/admin 可写，guest 只读。
- 时间戳统一 UTC（executed_at 用 datetime.utcnow，与 reviewed_at 对齐）。
- 统计去重：issue 可能同时逻辑关联 report 与 task（一般不会），按 id 去重防双算。

## 8. 测试（手动端到端，本仓库无测试框架）
1. 迁移：老库启动后 `checklist_item` 建出；`remaining_issue` 出现 task_id/checklist_item_id 列且 report_id 可空；幂等。
2. 采纳自动挂：生成带 task_id 的测试点 → 采纳 → GET checklist 出现该项(pending)；取消采纳 → pending 项消失；已执行项取消采纳后保留。
3. 手动补挂：把另一采纳测试点 attach → 出现在清单；补挂未采纳/跨项目 → 拒绝。
4. 勾选：passed/failed/blocked/pending 各态写入正确，executed_by/at 正确，回 pending 清空。
5. 转遗留：failed 项 to-issue → RemainingIssue 生成(report_id null、task_id/checklist_item_id 填对)；非 failed 拒绝。
6. 统计兼容：任务直挂 open issue 后，/stats/overview open_issues 计入且不与 report 路径重复；/stats/daily 口径正确。
7. 前端：MyReports 清单渲染、三态勾选即时回写、补挂弹窗只列可选项、转遗留弹窗预填 title、空态、dist 重建生效。

## 9. 未决/后续（非本次）
- 管理员在 Tasks.vue 看每个任务的验收进度汇总。
- 验收完成率进战绩墙/overview 作为新指标。
- L1「AI 接口冒烟」只读探活（自动化下一大阶段）。
