# 工作台首页「深色控制中心」改造 — 设计文档

- 日期：2026-08-06
- 状态：待评审
- 涉及：`backend/app/api/stats.py`、`frontend/src/views/Dashboard.vue`、`frontend/src/api/index.js`

## 背景与目标

工作台首页（`Dashboard.vue`）当前是通用亮色 ElementPlus 风格：欢迎横幅 + 4 个彩色渐变快捷卡 + "我的项目"表格，且**只调用 `listProjects()` 一个接口，没有任何真实统计数据**。

而登录页（`Login.vue`）已确立一套经评审的「工业控制台」视觉语言：近黑面板、信号绿点缀、网格底纹、JetBrains Mono 等宽字、大写 eyebrow 标签、脉冲状态点。首页与登录页气质割裂。

**目标**：把首页改造为「深色控制中心」仪表盘，延续登录页的工业风语言，并接入真实的跨项目统计数据，让"科技感"由统一叙事 + 真实数据共同支撑。

**范围边界**：只改首页内容区（`Dashboard.vue`）。侧边栏、顶栏、其他功能页保持现状。首页深色样式全部 scoped，不污染全局。

## 决策记录（来自 brainstorming）

| 维度 | 决策 |
|---|---|
| 视觉基调 | 深色控制中心（仅首页内容区，scoped） |
| 内容范围 | 换皮 + 真实数据看板 |
| 后端接口 | 新增 `GET /api/stats/overview`，返回 KPI + 近 7 天趋势 |
| 趋势图实现 | 纯手写 SVG（轻量、仪表盘感强，不引 echarts） |

## 第 1 部分：后端 — 新增 `GET /api/stats/overview`

### 为什么需要

现有 `/stats/daily`、`/stats/workload`、`/issues` 均强制单 `project_id`（单项目口径）。首页是跨项目视角，前端逐项目循环拉取会产生 N+1 请求且难做统一权限过滤。因此新增一个专用汇总接口最干净。

### 权限口径（复用现有 RBAC）

- **平台管理员**（`user.is_platform_admin`）：统计**全部项目**。
- **普通成员**：统计**我参与的项目集合**（该用户在 `ProjectMember` 中的所有 project_id，含 admin/member/guest）。

实现上：先解出该用户可见的 `project_ids` 列表；平台管理员则取全部项目 id。后续所有聚合都 `WHERE project_id IN (project_ids)`。若 `project_ids` 为空，返回全 0 结构（不报错）。

### 数据来源与口径

沿用 CLAUDE.md 的「不建独立统计表」约定：全部对 `task` / `daily_report` / `remaining_issue` 现算聚合，与 `/stats/daily` 口径一致，避免双写不一致。

- `should_submit`：今日（`assigned_date == date`）在可见项目内的 task，按 `assigned_to` 去重的人次。
- `submitted`：今日这些 task 对应的 `daily_report`（`report_date == date`），按 `user_id` 去重人次。
- `submit_rate`：`submitted / should_submit * 100`（should_submit 为 0 时取 0）。
- `online_cnt`：今日 `daily_report.is_online == True` 的条数。
- `avg_progress`：今日 `daily_report.progress_pct` 平均（无数据取 0）。
- `workload_hours`：今日 `daily_report.workload_hours` 求和。
- `open_issues`：可见项目内 `remaining_issue.status == open` 的总数（**当前未解决存量**，不限今日）。
- `trend`：近 7 天（含今日）逐日的 `hours`（`SUM(workload_hours)`）与 `submitted`（按 user 去重人次）序列，用于迷你趋势图。

### 返回结构

```json
{
  "date": "2026-08-06",
  "scope": "platform",              // "platform" | "member"
  "project_cnt": 5,
  "today": {
    "should_submit": 12,
    "submitted": 9,
    "not_submitted": 3,
    "submit_rate": 75.0,
    "online_cnt": 4,
    "avg_progress": 68.5,
    "workload_hours": 46.5
  },
  "open_issues": 7,
  "trend": [
    { "date": "2026-07-31", "hours": 32.0, "submitted": 8 }
    // ... 共 7 项，末项为今日
  ]
}
```

### 放置与风格

- 在 `app/api/stats.py` 新增 `@router.get("/overview")`，复用文件顶部已导入的 `func` 与模型（`Task`/`DailyReport`/`RemainingIssue`/`ProjectMember`/`Project`）。
- `date` 参数可选，默认取服务器当天（`date.today()`）；便于首页不传参直接用。
- 沿用本仓库序列化风格：手写 dict 返回，`ok(...)` 信封。
- 前端 `api/index.js` 增加：`export const overviewStats = (date) => http.get('/stats/overview', { params: { date } })`。

## 第 2 部分：前端 — `Dashboard.vue` 深色控制中心

### 视觉 token（复用登录页体系，scoped 在 `.dashboard`）

```
--ink: #0d0f12;     --panel: #14171c;   --line: #2a2f37;
--dim: #4a525c;     --fg: #e6e8ea;      --muted: #7d858f;
--signal: #00e5a0;  （信号绿）
```

辅助强调色（KPI 区分用，低饱和以融入深色）：警示橙 `#e6a23c`（遗留问题）、信息蓝 `#5b9bd5`（进度）。主色仍以信号绿为骨架。

### 布局（从上到下四段）

**① 控制台头条**（替换现欢迎横幅）
深色面板 + 网格底纹（复用登录页 `grid-bg` 手法）。左：`// OPERATIONS CENTER` eyebrow + 问候语（复用现有 `greeting`）+ 身份副标题。右：实时时钟（等宽字，秒级更新，`onMounted` 起 `setInterval`，`onUnmounted` 清除）、`SYSTEM // READY` + 脉冲状态点、纳入项目数 `project_cnt`。

**② KPI 指标墙**（核心新增）
一排指标块（桌面 5 列，窄屏自适应换行），每块：小写 eyebrow 标签 + 大号等宽数字（信号色/强调色）+ 单位/副文案。指标：
- 今日提交率（`submit_rate` %，附 `submitted/should_submit`）
- 今日上线数（`online_cnt`）
- 平均进度（`avg_progress` %）
- 今日总人时（`workload_hours` h）
- 未解决遗留问题（`open_issues`，警示橙）

每块深色卡 + 锐角 + 细描边，hover 时描边转信号绿 + 轻微发光（复用登录页按钮 hover 手法）。数字入场用现有 `fadeInUp` 错落动画。

**③ 近 7 天趋势条**
宽面板，手写 SVG 迷你图：以 `trend` 的 `hours` 为主序列画信号绿折线 + 面积渐变，`submitted` 可作次要柱或点。x 轴 7 个日期短标签，网格背景呼应工业风。纯 SVG + `computed` 生成 path，无第三方库。空数据时显示占位提示。

**④ 快捷入口 + 我的项目**
- 快捷入口：保留现有 `quickEntries` 逻辑（按角色切换首项），卡片从彩色渐变改为深色卡 + 信号色图标 + 锐角描边 + hover 发光。
- 我的项目：保留 `el-table` 数据逻辑，用 `:deep()` 套深色皮（表头/行/边框/hover 深色化），角色 tag 保留但配色适配深色。

### 数据与状态

- `onMounted` 并行 `listProjects()` + `overviewStats()`；各自 loading。
- overview 拉取失败时 KPI 区显示"—"占位，不阻断项目表渲染（降级）。
- 时钟 `setInterval` 每秒更新，`onUnmounted` 清理，避免离开页面后泄漏。

### 技术注意

- 全部样式 scoped，深色皮通过 `:deep()` 精确作用于 `.dashboard` 内的 Element 组件，**不改全局 `anim.css` 与 `main.js` 的输入框修复**。
- `prefers-reduced-motion` 下关闭动画与时钟脉冲（复用登录页做法）。
- 响应式：KPI 墙与快捷入口在窄屏换行；趋势 SVG 用 `viewBox` 自适应宽度。

## 测试与验证

1. **后端 E2E**：本地 SQLite 启动，用平台管理员 token 调 `/api/stats/overview`，核对 today 各字段与 trend 长度=7；构造一条今日 task + report 验证聚合数字正确；用普通成员 token 验证 scope=member 且只统计其参与项目。
2. **前端构建**：`npm run build` 通过无报错。
3. **浏览器冒烟**：登录后进入首页，确认深色控制中心渲染、KPI 显示真实数字、趋势图绘制、快捷入口跳转、项目表深色皮正常；窄屏检查换行。

## 非目标（YAGNI）

- 不改侧边栏/顶栏/其他页面。
- 不引入 echarts 到首页。
- 不新建统计表、不加缓存。
- 不做 KPI 的下钻/筛选交互（点击 KPI 跳详情页留待后续）。
