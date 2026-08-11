# AI 战绩墙（接真实数据）设计

- 日期：2026-08-10
- 状态：已评审，待实现
- 相关：QA Copilot（`ai_task` / `test_case`）、`stats.py` 聚合、Dashboard 视觉语言
- 原型：`frontend/ai-wall-prototype.html`（已 dataviz 校验 + 对齐平台控制台风）

## 1. 目标与背景

QA Copilot 已能把需求生成结构化测试点（`test_case`），并记录成本/耗时/采纳（`ai_task`）。但这些数据生成后不被再加工：既不回流到任务，也不进任何统计。**战绩墙**把这批沉睡数据聚合成一张可对上汇报、可对外观摩、也让团队自己看得见 AI 贡献的图。

三个受众：
- **向上汇报（领导）**：一张带硬数据的图，"AI 为测试团队省了多少、准不准、花了多少"。
- **同行/跨团队观摩**：体现 claudecode 深度集成的差异化。
- **测试团队自己**：看见自己项目的 AI 贡献，正向激励。

本设计只做"聚合展示 + 支撑该口径所需的最小模型改动"，不做集成层、不做执行自动化（那些是后续阶段）。

## 2. 范围

### 做
- `test_case` 评审状态从二态升级为三态（pending/adopted/rejected）+ 采纳时间戳 `reviewed_at`。
- 后端只读聚合端点 `GET /api/stats/ai?from=&to=`，现算聚合，不建统计表。
- `PATCH /testcases/{cid}` 从"设 adopted 布尔"升级为"设 review_status"，写 `reviewed_at`。
- 前端新增 `AIWall.vue`：时间范围选择器 + 折算系数可调输入 + 已验证的图表；全员可见。
- QA Copilot 现有采纳 UI 升级为三态交互。

### 不做（YAGNI）
- p95 耗时（只留 avg）。理由：主受众是领导，p95 增加理解成本；SQL 算不了要拉列表到 Python；原始 `duration_ms` 都在，将来做监控页再算。
- 统计表（沿用 stats.py 铁律：对 `task`/`daily_report`/`ai_task`/`test_case` 现算聚合，避免双写不一致）。
- 跨项目下钻、单项目详情页。
- 测试点回流 Task 变验收清单（是后续阶段，非本次）。

## 3. 数据模型改动

`test_case` 现状：`adopted: bool`（采纳/未采纳二态）。

为不破坏现有 `adopted` 字段与老数据，**加列不改列**：

| 新列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `review_status` | `Enum('pending','adopted','rejected')` | `pending` | 三态评审状态 |
| `reviewed_at` | `DateTime \| None` | `NULL` | 采纳/否决动作发生时间 |

- 保留 `adopted: bool` 做兼容，PATCH 时与 `review_status` 同步维护（`review_status==adopted` ⇔ `adopted==True`）。前端逐步过渡到读 `review_status`。
- 迁移回填：老数据 `adopted==True` → `review_status='adopted'`、`reviewed_at=created_at`；其余 → `review_status='pending'`、`reviewed_at=NULL`。

### 落地方式（平台既定模式）
1. `app/core/enums.py` 加 `ReviewStatus(str, Enum)`（放 enums，不放 models，与现有枚举一致）。
2. `app/models/ai.py` 的 `TestCase` 加两列。
3. `app/db/migrate.py` 加 `ensure_testcase_columns(engine)`（仿 `ensure_task_columns` 活样板），在 startup 调用；一次加两列 + 回填。
4. `backend/sql/schema.sql` 的 `test_case` 建表同步加两列（两份 schema 手动保持同步，铁律）。
5. `app/models/__init__.py` 已汇总导入，无需改。

## 4. 后端端点

### `GET /api/stats/ai?from=<date>&to=<date>`

- 位置：`app/api/stats.py`，与 overview/daily/workload 并列。
- 权限/范围：复用 `_visible_project_ids(db, user)`——平台管理员=全部项目；成员=参与项目。无可见项目返回全 0 结构（仿 overview 空态，不报错）。
- 参数：`from`/`to` 用 `Query(..., alias="from"/"to")`（沿用 workload 写法）。默认区间由前端传"近 30 天"；后端对缺省也给合理默认（to=today，from=today-29）。
- 聚合口径（现算，不建表）：
  - **生成类**按 `test_case.created_at` / `ai_task.created_at` 落在 [from,to] 筛。
  - **采纳类**按 `test_case.reviewed_at` 落在 [from,to] 筛。
- 序列化沿用 stats.py 风格：手写 dict，不用 response_model。

**返回结构**（统一信封 `ok(...)` 包裹）：
```python
{
  "scope": "platform" | "member",
  "project_cnt": int,
  "from": "YYYY-MM-DD", "to": "YYYY-MM-DD",
  # 生成类（created_at ∈ 区间）
  "total_generated": int,          # test_case 计数
  "run_cnt": int,                  # ai_task 里 status==done 计数
  "total_cost_usd": float,         # SUM(ai_task.cost_usd)
  "avg_duration_s": float,         # AVG(duration_ms)/1000，done 且非空；无则 0.0
  "dims": [{"name": "功能", "count": int}, ...],  # 固定 5 类顺序[功能,边界,异常,兼容,性能]，缺补 0
  # 采纳类（reviewed_at ∈ 区间）
  "total_reviewed": int,           # review_status in (adopted, rejected)
  "total_adopted": int,            # review_status == adopted
  "adopt_rate": float,             # adopted / reviewed；reviewed==0 → 0.0
  "prio": [{"p": "P0", "n": int}, {"p":"P1",...}, {"p":"P2",...}, {"p":"P3",...}],  # adopted 且 priority 非空，固定 P0-P3 顺序
  # 趋势（按天，覆盖整个 from~to）
  "trend": [{"date": "MM-DD", "generated": int, "adopted": int}, ...]
}
```

**不返回 `saved_hours`**：折算系数是前端可调参数，后端只给 `total_adopted`，前端算 `saved_hours = total_adopted × factor`。

**口径变化（重要）**：KPI 是"所选区间内"的量，**不是历史累计**。汇报语义为"近 30 天 AI 为团队节省 X 人时"，比累计更有说服力（累计只增不减、显虚）。

### `PATCH /api/ai/testcases/{cid}` 改造

- 位置：`app/api/ai.py`。
- 现状：接收 `adopted: bool`。
- 改为：接收 `review_status`（'adopted' / 'rejected' / 'pending'）。
  - 设为 adopted/rejected 时写 `reviewed_at = now()`；设回 pending 时 `reviewed_at = NULL`。
  - 同步维护 `adopted` 布尔（adopted⇔True，否则 False）做兼容。
- schema：`app/schemas/ai.py` 的 `TestCaseAdoptIn` 替换为接收 `review_status: ReviewStatus`（单字段，不与旧 `adopted` 布尔并存于入参；`adopted` 仅作为 DB 兼容列由后端派生）。
- 权限沿用现有 `_WRITE_ROLES`。

## 5. 前端

### 新增 `frontend/src/views/AIWall.vue`
- 路由：`router/index.js` 加一条；**全员可见**（不挂 `meta.platformAdmin`）；侧栏加入口。
- API：`api/index.js` 加 `aiStats({from,to})` 薄封装（返回已解包 data）。
- 时间范围选择器：按 palette.md filter 规范——预设行（近 7 天 / 近 30 天 / 近 90 天 / 本月至今）+ 自定义范围。默认**近 30 天**。切换即重新拉数。
- 折算系数：输入框，默认 `0.5`，`localStorage` 记忆（键如 `tp_ai_save_factor`）。`saved_hours = total_adopted × factor`，数字旁标注"按 X 人时/条估算"。
- 图表：直接从 `ai-wall-prototype.html` 移植——
  - hero 节省工时（等宽青绿）+ KPI 行（生成数/采纳率/花费/avg 耗时）
  - 生成 vs 采纳 趋势（categorical 双线 + hover）
  - 维度覆盖（sequential 单 hue 横向 bar）
  - 采纳质量：采纳率 meter + 优先级 ordinal 分段
- 空态：所选区间无数据时，仿 Dashboard 的空态引导卡（不报错）。

### 视觉约定（已在原型中验证）
- **外壳**倒向平台控制台风：大数字用 `JetBrains Mono` 等宽 + 青绿 `--signal (#00b386)`；KPI 标签 mono 大写；panel 带 `.grid-bg` 网格底；区块标题前置青绿发光竖条；卡片 hover 青绿描边+发光+上移。
- **图表内部**严守 dataviz 校验配色：趋势双线蓝/aqua（`#2a78d6`/`#1baf7a`，暗色 `#3987e5`/`#199e70`）；维度 sequential 蓝 ramp；优先级 ordinal 蓝 ramp（暗色用 `200/300/400/600` 拉开 ΔL）；数据线颜色/坐标轴对比度不被品牌色覆盖。
- 明暗双主题：dark 值须同时声明在 `@media (prefers-color-scheme: dark)` 与 `[data-theme="dark"]` 两个 scope（原型已修此坑，手动切换在亮色系统下也生效）。

### QA Copilot 采纳交互升级
- 现有"勾选采纳"改为三态（采纳 / 否决 / 待定）。涉及展示测试点列表的组件（`AITestGen.vue` 或其用例列表区）。
- 调用改造后的 PATCH，传 `review_status`。

## 6. 数据流

```
生成：需求 → claude_runner → test_case(review_status=pending, reviewed_at=NULL)
评审：人工在 QA Copilot 点 采纳/否决 → PATCH review_status + reviewed_at=now
聚合：AIWall.vue 选区间 → GET /api/stats/ai?from&to
      → 生成类按 created_at、采纳类按 reviewed_at 现算 → 返回结构
展示：前端按折算系数算 saved_hours，渲染 hero/KPI/趋势/维度/采纳质量
```

## 7. 错误处理与边界
- 无可见项目 / 区间无数据：返回全 0 结构 + 空 trend 序列，前端走空态卡，不报错（仿 overview）。
- `adopt_rate` 分母为 0（区间内无评审）：返回 0.0，前端 meter 显 0%。
- `avg_duration_s`：done 且 `duration_ms` 非空才计；无则 0.0。
- 折算系数非法输入（负数/非数）：前端夹取到合理区间（如 0~8），默认 0.5。
- 迁移幂等：`ensure_testcase_columns` 先探列是否存在再 ADD，重复启动不报错（仿 `ensure_task_columns`）。
- 权限：端点复用现有 RBAC；PATCH 沿用 `_WRITE_ROLES`。

## 8. 测试（手动端到端，本仓库无测试框架）
1. 迁移：老库启动后 `test_case` 出现两列，旧 adopted 数据回填正确（review_status=adopted、reviewed_at=created_at）。
2. PATCH：采纳一条 → review_status/adopted/reviewed_at 三者一致；否决 → rejected + reviewed_at；置回 pending → reviewed_at 清空。
3. 端点：造跨越区间的数据，验证生成类按 created_at、采纳类按 reviewed_at 各自筛对；adopt_rate = adopted/reviewed；dims/prio 固定顺序补 0；trend 覆盖整区间。
4. 权限：成员只见参与项目聚合；管理员见全部；无项目返回全 0 不报错。
5. 前端：范围切换重拉；折算系数改动即时重算 saved_hours 且 localStorage 记忆；明暗切换双向生效；空区间走空态。

## 9. 未决/后续（非本次）
- 测试点回流 Task 变验收清单（半自动阶段）。
- L1「AI 接口冒烟」只读探活（执行自动化阶段，需 curl 白名单沙盒 + 目标登记）。
- 一键生成周报（复用 runner 读聚合出 Markdown）。
