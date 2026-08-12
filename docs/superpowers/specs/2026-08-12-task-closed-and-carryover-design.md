# 任务:新增「已关闭」状态 + 未完成任务顺延可见 · 设计

日期:2026-08-12

## 需求

任务分配模块两项调整:

1. **新增任务状态「已关闭」**(`closed`):用于不再跟进/取消/合并的任务。
2. **未完成任务顺延可见**:当天任务若未达到「已上线」或「已关闭」,应在之后的日期仍然可见(当前任务只在其派单当天可见)。

经与用户对齐的口径:

- 顺延**只改可见范围,不改 `assigned_date`**(不搬任务、不改数据)。
- 顺延起点 = **所有历史未完成**:查某日时带出 `assigned_date < 该日 且 status ∉ {online, closed}` 的全部任务。
- **任意选中日都叠加**顺延(逻辑统一,不特判「今天」)。
- 顺延任务在列表**标「顺延」标签**,附原派单日期。

## 一、新增 `closed` 状态

- `backend/app/core/enums.py::TaskStatus` 增加成员 `closed = "closed"`。
- **`backend/app/db/migrate.py`**:
  - `migrate_task_status()` 中**删除**将 `closed` 归并为 `online` 的那句
    `UPDATE task SET status='online' WHERE status='closed'`。该句会在每次 startup
    把用户设置的「已关闭」任务改回「已上线」,与本需求直接冲突,必须移除。
  - MySQL 分支「收紧」ENUM 的最终定义补上 `closed`:
    `ENUM('pending','testing','blocked','online','closed')`。放宽集本就含 `closed`,只改收紧句。
- `backend/sql/schema.sql` 的 `task.status` ENUM 定义补 `closed`:
  `ENUM('pending','testing','blocked','online','closed') NOT NULL DEFAULT 'pending'`。
- 前端 `frontend/src/views/Tasks.vue::STATUS_META` 增加
  `closed: { label: '已关闭', type: 'info' }`。状态下拉、编辑弹窗均遍历 STATUS_META,自动带出。

## 二、未完成任务顺延可见

### 后端 `list_tasks`(`backend/app/api/tasks.py`)

传 `date` 时,过滤条件由「等于当天」改为「当天派单 OR 历史未完成」:

```
Task.assigned_date == date
OR (Task.assigned_date < date AND Task.status NOT IN (TaskStatus.online, TaskStatus.closed))
```

- `mine`、`project_id` 过滤不变;排序仍 `assigned_date desc, id desc`(顺延旧任务自然下沉)。
- 不传 `date`(全量视图)时行为不变。
- 用 SQLAlchemy `or_(...)` 组合;`NOT IN` 用 `Task.status.notin_([...])`。

### 顺延标记 `is_carried`

`_to_out(db, t)` 增加布尔字段 `is_carried`,判定 = `on_date is not None and t.assigned_date < on_date`
(顺延 = 派单日早于查询日;用 `<` 而非 `!=`,不依赖过滤已排除未来日期这一隐含前提)。

由于 `_to_out` 当前不知道「查询日」,给它增加一个可选参数 `on_date: date | None = None`:
- `list_tasks` 传入本次查询的 `date`;
- 其余调用方(create/update/copy 的返回)不传 `on_date`,`is_carried` 恒为 `False`,不受影响。

### 前端 `Tasks.vue`

- 「任务名称」列(现展示 description)旁,当 `row.is_carried` 为真时显示
  `<el-tag size="small" type="warning">顺延自 {{ MM-DD }}</el-tag>`,日期取 `row.assigned_date` 截月日。
- 其余逻辑不变(查询仍每次传 `date`)。

## 三、明确不动的地方(YAGNI / 防止口径漂移)

- **统计与看板口径全部不变**:`stats.py`(overview/daily/workload)、`checklist.py` 中
  `assigned_date == 当天` 的聚合保持原样。顺延仅是任务列表的展示便利;若统计也叠加历史未完成,
  同一任务会在多天被重复计数。
- **`closed` 不计入完成率**:overview 完成率仍只数 `online`,不改。
- **「复制昨日」`copy_yesterday` 不变**:它按 `assigned_date == yesterday` 复制,与顺延无关。
- 不新增数据库列(`is_carried` 为现算,不落库)。

## 四、影响面与风险

- 改动文件:`enums.py`、`migrate.py`、`sql/schema.sql`、`api/tasks.py`、`views/Tasks.vue`。
- 有 DB 迁移(MySQL ENUM 定义变更),但 `migrate_task_status()` 每次 startup 幂等执行,
  服务器 `update.sh` 重启即自动生效,无需手动 SQL;SQLite(本地)status 存为 TEXT,天然兼容。
- `list_tasks` 是被 `Tasks.vue` 与 `MyReports` 等调用的读接口;顺延逻辑只在「传了 date」时触发,
  且只**增加**可见行、不删减,向后兼容。实现前会 grep 确认调用方对「多返回历史未完成行」无副作用。

## 五、验证

- 后端:用独立 SQLite 库造数据(跨多日、含各状态),断言:
  - 查某日带出「当天 + 历史未完成」,且不含历史已 online/closed;
  - `is_carried` 标记正确;`closed` 任务不被顺延带出;
  - `migrate_task_status` 重复执行不再篡改 `closed` 任务。
- 前端:构建通过;顺延标签渲染正确。
- 上线:构建 dist → push → 服务器 `bash scripts/update.sh`(git pull + 重启,自动迁移)。
