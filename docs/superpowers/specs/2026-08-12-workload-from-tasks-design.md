# 工作量统计改用任务(task)数据 · 设计

日期:2026-08-12

## 背景与问题

工作量统计模块(`/api/stats/workload` + `WorkloadStats.vue`)线上全部显示为空。

根因:该端点 **100% 依赖 `daily_report` 表**——按成员/按天聚合 `workload_hours`(人时)与 `is_online`。线上几乎没有人提交日报,故聚合结果为空(这在旧口径下是"正确的空",并非计算 bug)。任务派单表(`task`)有数据,但工作量统计根本不读它。

## 目标

把工作量统计的取数源从 `daily_report` 换成 `task` 表,让"已有的任务数据"直接驱动展示。经与用户确认,口径定为:

- **取数源**:`task` 表(不再依赖日报)。
- **工作量口径**:**任务数量(条)**。任务表无"人时"字段,不做任何工时折算。
- **成员维度**:按 `Task.assigned_to`(测试人员/指派给)分组。

## 后端改动

仅改 `backend/app/api/stats.py` 的 `workload_stats` 端点内部实现。**签名、权限校验、返回信封、`from`/`to` 参数别名全部不变**。

查询改为:从 `Task` 按 `project_id` 且 `assigned_date ∈ [from_date, to_date]` 拉取,遍历聚合。

返回字段(口径全部基于 task):

| 字段 | 含义(新口径) | 说明 |
|---|---|---|
| `project_id` / `from` / `to` | 同旧 | 原样保留 |
| `total_tasks` | 区间内任务总条数 | **替代旧 `total_hours`** |
| `total_online` | `status == online` 的任务条数 | 语义由"上线日报数"变为"上线任务数" |
| `members[].user_id` | 成员 id | 按 `assigned_to` 分组 |
| `members[].name` | 成员姓名 | `db.get(User, uid)`,沿用旧辅助函数风格 |
| `members[].task_cnt` | 该成员任务条数 | **主指标**,按 `task_cnt` 降序排序 |
| `members[].online_cnt` | 该成员 `online` 任务条数 | |
| `daily[].date` | 日期(`YYYY-MM-DD`) | 按 `assigned_date` 分组,按日期升序 |
| `daily[].task_cnt` | 当天任务条数 | **替代旧 `daily[].hours`** |
| `daily[].online_cnt` | 当天 `online` 任务条数 | |

- "参与人数"不作为独立返回字段,前端沿用旧做法用 `members.length` 计算。
- 只保留出现在结果集中的成员/日期(空缺日期不补零),与旧实现的稀疏序列行为一致。

## 前端改动

仅改 `frontend/src/views/WorkloadStats.vue`(`api/index.js` 的 `workloadStats` 封装不变)。

- `data` 初始结构:`total_hours` → `total_tasks`。
- KPI 卡片一:文案"总工作量(人时)" → **"总任务数(条)"**,绑定 `data.total_tasks`。KPI 卡片二"累计上线数"、卡片三"参与人数"(`data.members?.length`)不变。
- 成员对比柱状图:`members.map(m => m.hours)` → `m.task_cnt`;Y 轴名"人时" → "任务数";首个 series 名"工作量(人时)" → "任务数";"上线数" series 不变。
- 每日趋势折线:`daily.map(d => d.hours)` → `d.task_cnt`;左 Y 轴名与 legend/series 文案"工作量(人时)" → "任务数";"当日上线" series 不变。

## 影响面与风险

- 调用方唯一:全仓 grep 确认 `/stats/workload`、`workloadStats`、`total_hours` 仅被 `WorkloadStats.vue` 使用,故改返回字段名安全,前后端同步改即可。
- 纯读接口 + 展示层改动:无写操作、无数据库迁移、不改任何模型或 `schema.sql`。
- 不触碰 `/stats/daily`、`/stats/overview`、`/stats/ai`——它们口径独立,与本次无关。

## 不做的事(YAGNI)

- 不新增数据库字段,不动 `task` / `daily_report` 模型。
- 不引入"固定工时 × 任务数"之类的人时折算。
- 不新增开发(`developer`)维度的图表。

## 验证

改完需构建前端产物并上线(与本项目既有流程一致):本地 `npm run build` 重建 `frontend/dist` → 提交推送 → 服务器执行 `bash scripts/update.sh`(`git pull` + 重启)。线上进入「工作量统计」页,选有任务的项目与日期区间,应看到总任务数、成员对比、每日趋势有内容。
