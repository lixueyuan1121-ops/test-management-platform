# AI 用例:关联任务下拉改说明 + 用例库回溯 + 日报已采纳用例 · 设计

日期:2026-08-12

## 需求

1. **AI 测试助手「关联任务」下拉**显示字段由任务标题(title)改为说明(description)。
2. **已生成用例(含已采纳)缺少回溯入口**:现只能在 AI 测试助手里逐个生成批次点选回看(受 20 批次上限),无跨批次汇总/筛选。需要一个能回溯展示的地方。
3. **「我的日报」对应任务下需展示该任务已采纳的用例**。

经与用户对齐:
- 需求 2 → **新建「用例库」页面**(侧边栏),按项目列出全部已生成用例,支持筛选:采纳状态 / 关联任务 / 维度 / 关键词;**只读**(不在此改采纳态)。
- 需求 3 → MyReports 任务行**新增只读「已采纳用例」入口**(抽屉),按 `task_id` 查已采纳 TestCase,与现有「验收清单」分开(验收清单是执行勾选视角,已采纳用例是纯回溯视角)。
- 需求 2 与 3 **共用一个新查询端点**。

## 需求 1(最小改动)

`frontend/src/views/AITestGen.vue:24`:
```
:label="t.title"  →  :label="t.description || t.title"
```
(说明为空时回退标题,避免下拉项空白无法辨识。)

## 后端:新增查询端点 `GET /api/ai/cases`(需求 2/3 共用)

在 `backend/app/api/ai.py` 新增。参数:
- `project_id: int`(必填,Query)
- `task_id: int | None`(可选)
- `review_status: ReviewStatus | None`(可选:pending/adopted/rejected)
- `category: str | None`(可选)
- `keyword: str | None`(可选,对 title 做 `ilike %kw%`)

权限:`assert_project_role(db, user, project_id, _ALL_ROLES)`(平台管理员放行全部,与现有 AI 端点一致)。
查询:`TestCase.project_id == project_id` + 各可选过滤;`order_by(TestCase.id.desc())`。
返回:`ok([_to_case_out(tc, task_title=...) for tc in rows])`。

### `_to_case_out` 增加可选参数 `task_title`
```
def _to_case_out(tc: TestCase, task_title: str | None = None) -> dict:
    return { ...现有字段..., "task_title": task_title }
```
- 新端点显式传入 `task_title`(批量预取 task 名,避免 N+1:一次性 `db.query(Task.id, Task.title).filter(Task.id.in_(task_ids))` 建 map)。
- 现有 3 处调用方(SSE 生成 `ai.py:213`、批次查 `ai.py:267`、评审 PATCH `ai.py:310`)不传 `task_title`,值为 `None`,不受影响(前端多收一个可空字段无害)。

### 前端 API 封装(`frontend/src/api/index.js`)
```
export const listCases = (params) => http.get('/ai/cases', { params })
```

## 需求 2:「用例库」页面

- **新组件** `frontend/src/views/CaseLibrary.vue`。
- **新路由**(`router/index.js`,MainLayout 子路由):
  `{ path: 'case-library', name: 'case-library', component: () => import('@/views/CaseLibrary.vue') }`
- **侧边栏菜单**(`MainLayout.vue`):紧挨「AI 测试助手」新增一个全员可见顶级项:
  `<el-menu-item index="/case-library"><el-icon><Collection /></el-icon><span>用例库</span></el-menu-item>`
  (`Collection` 加入图标导入列表。)
- **页面结构**:
  - 顶部筛选条:项目下拉(默认用 `pickDefaultProjectId`,复用全局记忆工具;切换写 `setLastProjectId`)+ 采纳状态下拉(全部/已采纳/已否决/待定)+ 关联任务下拉(该项目任务,`listTasks`,label 用 description)+ 维度下拉(功能/边界/异常/兼容/性能)+ 关键词输入(回车触发)。
  - 表格列:维度、优先级、测试点(title)、步骤、预期、采纳状态(el-tag 三态)、关联任务(task_title,无则 -)、生成时间。**只读**。
  - 数据:`listCases({ project_id, review_status, task_id, category, keyword })`,任一筛选变化即重查。
- 复用 AITestGen 的三态配色常量口径(采纳=success/否决=danger/待定=info)。

## 需求 3:MyReports「已采纳用例」入口

- `frontend/src/views/MyReports.vue` 任务行操作区(现有「填报」「验收清单」旁)新增:
  `<el-button link type="success" @click="openAdopted(row)">已采纳用例</el-button>`
- **只读抽屉**(新 reactive 状态 `adopted`),`openAdopted(row)` 调
  `listCases({ project_id: pid, task_id: row.id, review_status: 'adopted' })`,
  列出:维度、优先级、测试点(title)、步骤、预期。空态提示「该任务暂无已采纳用例」。
- 与现有「验收清单」抽屉并存,互不影响。

## 明确不做(YAGNI / 防口径漂移)

- 用例库**不做**采纳/否决操作(评审仍在 AI 测试助手页),保持只读回溯,职责单一。
- **不改**采纳回流 ChecklistItem 的现有逻辑(`ai.py` review_testcase 副作用不动)。
- **不动** `schema.sql` / 模型 / 迁移(纯新增查询端点 + 前端,无表结构变化)。
- 用例行不展示来源批次的成本/耗时/生成人(太重);如需可后续加。

## 影响面与风险

- 后端:`ai.py` 加一个只读端点 + `_to_case_out` 加一个可选字段;无迁移、无契约破坏。
- 前端:1 新页面 + 1 路由 + 1 菜单项 + 1 API 封装 + MyReports 一个入口和抽屉 + AITestGen 一行。
- 无数据库变化。所有新增均为读路径,低风险。

## 验证

- 后端:独立 SQLite 库造多用例(跨任务、含 pending/adopted/rejected),断言 `GET /ai/cases` 各过滤组合正确、`task_title` 正确 join、权限校验生效。
- 前端:构建通过;用例库筛选联动、MyReports 已采纳抽屉渲染。
- 上线:构建 dist → push → 服务器 `bash scripts/update.sh`(纯前后端代码,无迁移)。
