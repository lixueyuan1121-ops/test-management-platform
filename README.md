# 测试管理平台 (Test Platform)

> 面向测试团队的**多项目、日报驱动、可扩展**测试管理平台。
> 当前进度：**P0 骨架**（工程 + 数据库 + JWT 登录 + RBAC + 项目成员管理）已就绪并通过冒烟验证。

完整设计见 [DESIGN.md](./DESIGN.md)。

---

## 功能概览

| 角色 | 能力 |
|---|---|
| 平台管理员 | 建项目、管所有人员、全局集成配置 |
| 项目管理员 (admin) | 给本项目分配每日任务、看本项目全部统计、管本项目成员 |
| 成员 (member) | 接收任务、提交日报反馈 |
| 嘉宾 (guest) | 纯只读浏览 |

P0 已实现：登录、RBAC 权限、项目 CRUD、项目成员管理（加人/改角色/移除）。
P1+ 规划：任务分配 → 日报反馈 → 日报统计 → 工作量统计 → 遗留问题跟踪 → 集成层（对接缺陷/自动化工具）。

---

## 技术栈

- **前端**：Vue3 + Vite + ElementPlus + Pinia + Vue Router
- **后端**：FastAPI + SQLAlchemy 2.0 + PyMySQL
- **数据库**：MySQL 8（本地冒烟可用 SQLite）
- **部署**：Docker Compose（内网）

---

## 目录结构

```
.
├── DESIGN.md                 # 完整设计文档
├── README.md
├── docker-compose.yml        # mysql + backend + frontend
├── backend/
│   ├── app/
│   │   ├── main.py           # 入口：建表、种子管理员、路由装配
│   │   ├── core/             # config / security(JWT+bcrypt) / deps(权限) / enums / errors
│   │   ├── db/session.py     # engine + Base
│   │   ├── models/           # 10 张表模型（含集成层扩展位）
│   │   ├── schemas/          # Pydantic 入参/出参
│   │   └── api/              # auth / projects / members / users
│   ├── sql/schema.sql        # MySQL 完整建表脚本
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── api/              # axios 封装 + 接口
    │   ├── store/auth.js     # 登录态/角色
    │   ├── router/           # 路由 + 角色守卫
    │   ├── layouts/          # 主布局（角色菜单）
    │   └── views/            # Login/Dashboard/Projects/Members/Tasks
    ├── Dockerfile
    └── package.json
```

---

## 快速开始

### 方式一：Docker Compose（推荐，生产形态）

```bash
docker compose up -d
```
- 前端：http://localhost
- 后端 API 文档：http://localhost:8000/docs
- 默认管理员：`admin` / `admin123`（首次启动自动种入，**生产请改密**）

### 方式二：本地开发模式（前后端分离，热重载）

**后端**（默认 SQLite，开箱即用）：
```bash
cd backend
cp .env.example .env                 # 可保持 SQLite 冒烟，或改 DATABASE_URL 指向 MySQL
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**前端**：
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 ，已配 /api 代理到 8000
```

> 若 `npm install` 慢：`npm config set registry https://registry.npmmirror.com`
> 若 `pip install` 慢：加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`

### 切换到 MySQL
把 `backend/.env` 的 `DATABASE_URL` 改为：
```
DATABASE_URL=mysql+pymysql://tp:tp123@localhost:3306/test_platform?charset=utf8mb4
```
建库建表用 `backend/sql/schema.sql`：
```bash
mysql -uroot -p < backend/sql/schema.sql
```
首次启动后端会自动建表并种入管理员（与 schema.sql 表结构一致）。

---

## API 速览（P0）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | /api/auth/login | 公开 | 登录，返回 JWT |
| POST | /api/auth/refresh | 公开 | 刷新 token |
| GET  | /api/auth/me | 登录 | 当前用户 + 项目成员关系 |
| GET  | /api/projects | 登录 | 项目列表（按权限过滤） |
| POST | /api/projects | 平台管理员 | 建项目 |
| PATCH| /api/projects/{id} | 平台管理员 | 改项目 |
| GET  | /api/projects/{pid}/members | 项目成员(含guest) | 成员列表 |
| POST | /api/projects/{pid}/members | 项目admin | 加成员 |
| PATCH| /api/projects/{pid}/members/{uid} | 项目admin | 改角色 |
| DELETE| /api/projects/{pid}/members/{uid} | 项目admin | 移除成员 |
| GET  | /api/users?keyword= | 登录 | 用户选择器 |
| GET  | /api/health | 公开 | 健康检查 |

完整交互文档：后端启动后访问 `/docs`（Swagger）。

---

## P0 冒烟验证结果

已通过端到端测试：
- ✅ 登录（正确/错误密码 401）
- ✅ 无 token 访问受保护接口 → 拒绝
- ✅ 平台管理员建项目
- ✅ 项目 admin 加成员/改角色/移除
- ✅ **guest 只读**：GET 成员 200，POST/建项目 403
- ✅ member 不可建项目（403）、不可访问非成员项目（403）
- ✅ 角色提升后即时获得权限（guest→admin 后可写）
- ✅ 前端 `npm run build` 通过

---

## 下一步（P1）

1. 任务分配（管理员按天指派，支持复制昨日）
2. 日报反馈（进度%/上线/工作量/遗留问题）
3. 日报统计（应交/已交/上线/遗留，Excel 导出）

数据模型（task / daily_report / remaining_issue）已在 P0 建表预留，无需改库即可进入 P1 开发。
