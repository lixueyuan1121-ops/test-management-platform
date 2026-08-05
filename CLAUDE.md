# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

面向测试团队的多项目测试管理平台。前后端分离：后端 FastAPI + SQLAlchemy 2.0，前端 Vue3 + Vite + ElementPlus + Pinia。

### 文档 vs 代码（重要，别被文档误导）
- `DESIGN.md`（v1.0，已评审）是**权威设计与路线图**（P0 脚手架 → P1 任务/日报/统计 → P2 工作量/遗留问题/嘉宾 → P3 集成层 → P4 扩展）。读它了解"打算做什么"，但**不代表已实现**。
- `README.md` 只描述 P0，**已过时**——代码实际已到 P1/P2 + 工具广场。
- **文档写了但代码里还没有的**（不要以为存在）：
  - **集成层（P3）全部未实现**：没有 `app/integrations/` 包、没有适配器/注册表/事件总线、没有 webhook 入口、没有 `/api/results`、没有 API Token 相关接口。`integration` / `api_token` / `integration_event` 三张表**仅是建表占位**（`app/models/integration.py`），无任何业务代码。
  - **Excel 导出未实现**：DESIGN/README 都提"日报/工作量 Excel 导出"，但没有 `openpyxl`（连 requirements 里都没有）、没有导出端点、前端也无导出逻辑。
- **文档没提但代码里有的**：测试工具广场（`tools` 后端 + `ToolPlaza.vue`/`ToolAdmin.vue` 前端），平台管理员登记自研测试工具、分类、上下线，全体登录用户浏览下载。

### 已实现模块
`auth`、`projects`、`members`、`users`、`tasks`、`reports`（日报 upsert）、`stats`（`/daily` 日报统计、`/workload` 工作量统计）、`issues`（遗留问题）、`tools`（工具广场）。

## 常用命令

后端（默认 SQLite，开箱即用；在 `backend/` 下）：
```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000   # Swagger: http://localhost:8000/docs
```

前端（在 `frontend/` 下）：
```bash
npm install
npm run dev        # http://localhost:5173，/api 代理到 :8000
npm run build      # 产物 dist/
```

整栈（生产形态，MySQL + backend + frontend）：
```bash
docker compose up -d   # 前端 :80，后端 :8000，MySQL :3306
```

切换 MySQL：改 `backend/.env` 的 `DATABASE_URL`（见 `.env.example` 注释）。首次启动后端会自动建表并种入管理员（用户名/密码来自 `SEED_ADMIN_*` 环境变量）。

本仓库**没有配置测试框架，也没有 lint/format 工具**——不要臆造 `pytest`/`eslint`/`ruff` 命令。README 中的"冒烟验证"是手动端到端测试。

## 架构要点（跨文件的关键约定）

### 统一响应信封 `{code, msg, data}`（后端↔前端强耦合）
- 后端所有接口用 `app/schemas/common.py` 的 `ok(data)` / `fail(code, msg)` 返回；`code==0` 表示成功。
- `app/core/errors.py` 把 `HTTPException`、`RequestValidationError`（422）、未捕获 `Exception`（500）**统一转成同一信封**，所以业务代码里 `raise HTTPException(status, detail="中文提示")` 即可，前端能直接拿到 `msg`。
- 前端 `api/http.js` 的响应拦截器会**解包**：`code===0` 时直接返回 `data`（不是整个 body），否则 `ElMessage.error(msg)` 并 reject。因此 `api/index.js` 里所有函数的返回值已是 `data` 本身。修改任一端的信封约定必须同步另一端。
- 401 由前端拦截器统一处理：清登录态 + 跳 `/login`。

### 两级 RBAC + 两种鉴权写法（`app/core/deps.py`）
- **平台管理员** `User.is_platform_admin`：绕过一切校验，在任何项目里都被当作 `admin`，会返回一个**虚拟、未入库**的 `ProjectMember`。
- **项目级角色** `ProjectRole`（admin/member/guest）来自 `ProjectMember` 表。角色枚举集中在 `app/core/enums.py`（刻意不放 models，避免 api/deps 循环导入）。
- 两种强制方式，按 `project_id` 的来源选用：
  - 路径参数 `{pid}`：用依赖注入 `Depends(get_project_member)` 或 `require_project_role(*roles)`。**坑**：`get_project_member` 的参数名必须叫 `pid`，与路由变量一致，否则会被当成 query 参数。
  - `project_id` 来自请求体/query：用非注入版 `assert_project_role(db, user, project_id, roles)`（见 `api/tasks.py`）。

### 数据库与迁移
- 启动时 `Base.metadata.create_all`（`app/main.py::init_db`），**未启用 alembic**（虽已在 requirements 里）。
- 增量改列走 `app/db/migrate.py` 的手写 `ALTER TABLE ADD COLUMN`（如 `ensure_task_columns`），在 startup 里调用——加字段时参照此模式补一段，否则老库不会更新。
- **两份 schema 需手动保持同步**：SQLAlchemy 模型（`app/models/`，`create_all` 用）与 `backend/sql/schema.sql`（MySQL/docker 初始化用）。改表结构要同时改两处（必要时再加 migrate 步骤）。
- 模型必须在 `app/models/__init__.py` 汇总导入，`create_all` 才能建全表。

### 序列化约定
后端**不用** `response_model`，而是每个 router 内手写 `_to_out(db, obj) -> dict` 把 ORM 对象转 dict（枚举取 `.value`、日期转 `str`/`isoformat`），并用 `_user_name(db, uid)` 之类的辅助函数补关联名。新增接口沿用此风格。

### 领域模型与业务规则
- 核心链路：`Task`（管理员按天指派给成员）→ 成员对每条当日任务提交 `DailyReport`（进度%/是否上线/工作量人时/小结）→ 日报里可挂多条 `RemainingIssue`（遗留问题，结构化：标题+严重度+负责人+外部缺陷引用 `external_ref`）。
- `daily_report` 对 `(task_id, report_date)` 唯一，提交走 **upsert**（见 `api/reports.py`）；成员只能为指派给自己的任务提交。
- **不建独立统计表**：`/stats/daily` 和 `/stats/workload` 都是对 `task`/`daily_report`/`remaining_issue` 现算聚合（`SUM(workload_hours)` 等），避免双写不一致。改动这几张表的语义会直接影响统计口径。
- `Task` 的 `requirement_url` / `developer` 两列是 P1 后加的，靠 `migrate.py::ensure_task_columns` 给老库补列——这是新增字段该走的模式的活样板。

### JWT / 鉴权细节（`app/core/security.py`）
- 密码 bcrypt；token 用 python-jose (HS256)，payload 带 `type`（`access`/`refresh`）。`get_current_user` 会校验 `type=="access"`，`/auth/refresh` 校验 `type=="refresh"`——两者不可混用。
- access 默认 120 分钟、refresh 7 天（`config.py` 可配）。**登出仅前端丢弃 token**，服务端无黑名单（DESIGN 说 P3 才用 Redis 拉黑）。

### tools 模块与其余模块风格不一致（留意）
`api/tools.py` 把 Pydantic schema **内联定义在路由文件里**（不像其他模块放 `app/schemas/`），且用了 `class Config: pass`、单行 `if ...: ...` 等紧凑写法。改这个文件时沿用它自己的风格即可；新增**其他**模块仍应把 schema 放 `app/schemas/`。

### 前端结构
- `api/index.js` 集中所有接口函数（薄封装，返回已解包的 data）。
- `store/auth.js`（Pinia）：token 存 `localStorage` 的 `tp_token`；`isPlatformAdmin`、`roleIn(projectId)` getter 供组件判权。
- `router/index.js`：`beforeEach` 守卫处理公开路由、登录校验、首次进入懒加载 `fetchMe()`、`meta.platformAdmin` 页面拦截。加需要平台管理员的页面时给路由挂 `meta: { platformAdmin: true }`。
- `api/http.js` 的 baseURL：dev 走 vite 代理 `/api`；生产用 `http://<当前页面 hostname>:8000/api`（为局域网访问，访客浏览器直连服务所在机器的 8000）。
- 别名 `@` → `frontend/src`。
