# 测试管理平台 — 技术架构与实现全景

> 本文基于对**当前代码库的实读**整理，反映代码的**实际实现状态**（截至 2026-08-15），而非规划文档。
>
> 注意与既有文档的关系：
> - `DESIGN.md`（v1.0）是权威**设计与路线图**，描述"打算做什么"，不代表已实现。
> - `README.md` 只描述 P0，**已过时**。
> - 本文档描述"代码里实际有什么"——项目实际进度已远超上述两份文档，演进成了一个**以 AI 生成测试用例 + 本地执行机自动化回写为核心闭环**的平台。

---

## 一、整体形态

前后端分离 + 一个独立的**执行机（runner）工具链**，三者构成闭环：

```
┌─────────────┐   HTTP/JSON      ┌──────────────────┐   subprocess    ┌──────────────┐
│  前端 SPA   │ ───────────────► │  后端 FastAPI     │ ──────────────► │  claude CLI  │  (AI 生成测试点)
│ Vue3+Vite   │  {code,msg,data} │  SQLAlchemy 2.0   │                 └──────────────┘
│ ElementPlus │ ◄─────────────── │  SQLite / MySQL5.6│
└─────────────┘   SSE 流式        └──────────────────┘
                                        ▲  Pull 轮询(runner 长期 token)
                                        │  GET/claim/PATCH  /api/exec-queue
                                  ┌─────┴────────────────────────┐
                                  │  qalab-runner (Node,你自己的机器)│
                                  │  拉取用例 → 本地 Claude Code   │
                                  │  headless 执行 → pass/fail 回写 │
                                  └───────────────────────────────┘
```

### 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI 0.115 + SQLAlchemy 2.0 + Pydantic v2 + python-jose(JWT) + bcrypt + PyMySQL |
| 前端 | Vue 3.5 + Vite 6 + Element Plus + Pinia + vue-router + echarts + markdown-it + dompurify |
| 数据库 | 默认 SQLite（开箱即用）；生产 MySQL 5.6（`work_qa` 库） |
| AI | 本机 `claude` CLI，后端以 subprocess 非交互调用 |
| 执行机 | Node（纯内置模块，零第三方依赖）+ Playwright(CDP) + 本地 Claude Code |

### 部署形态

- 前端 `npm run build` 产出 `dist/`，由**后端 uvicorn 单进程同源托管**（`main.py::_mount_frontend`，含 SPA 回退与防目录穿越）。服务器无需 Node。
- 生产用 docker-compose（MySQL + backend + frontend）。
- 数据库因 MySQL 5.6 不支持原生 JSON，所有"结构化"字段（`script`、`payload`）一律用 `Text` 存 JSON 字符串。

---

## 二、后端核心约定（跨文件强耦合）

### 1. 统一响应信封 `{code, msg, data}`

- 所有接口用 `schemas/common.py` 的 `ok(data)` / `fail(code, msg)` 返回，`code==0` 表示成功。
- `core/errors.py` 把 `HTTPException`、`RequestValidationError`（422）、未捕获 `Exception`（500）**统一转成同一信封**，业务代码里直接 `raise HTTPException(status, detail="中文提示")` 即可，前端能直接拿到 `msg`。
- 前端 `api/http.js` 响应拦截器**自动解包**：`code===0` 时直接返回 `data`（不是整个 body），否则 `ElMessage.error(msg)` 并 reject。因此 `api/index.js` 里所有函数返回值已是 `data` 本身。
- 401 由前端拦截器统一处理：先尝试用 refresh_token 静默换新 token 重放一次，失败才清登录态跳 `/login`。
- **改任一端信封约定必须同步另一端。**

### 2. 两级 RBAC + 三套鉴权（`core/deps.py`）

- **平台管理员** `User.is_platform_admin`：绕过一切校验，在任何项目里都被当作 `admin`，返回一个**虚拟、未入库**的 `ProjectMember`。
- **项目级角色** `ProjectRole`（admin/member/guest），来自 `ProjectMember` 表。角色枚举集中在 `core/enums.py`（刻意不放 models，避免 api/deps 循环导入）。
- 两种项目级鉴权写法，按 `project_id` 来源选用：
  - 路径参数 `{pid}`：依赖注入 `Depends(get_project_member)` 或 `require_project_role(*roles)`。**坑**：`get_project_member` 参数名必须叫 `pid`，与路由变量一致，否则被当成 query 参数。
  - `project_id` 来自请求体/query：非注入版 `assert_project_role(db, user, project_id, roles)`。
- **第三套鉴权：runner**（`require_runner_ctx`）。与用户 JWT 完全分离的长期 token，两种来源：
  - **设备专属 token**：每台注册设备一个（`secrets.token_hex(32)`），归属到人，接口据此把 `runner` 锁定为 `device.runner_id`，防冒充。
  - **共享 token**（`RUNNER_TOKEN`，兜底）：过渡期老 runner 用，靠 query 的 runner 字符串区分。未配置时一律拒绝。

### 3. 序列化与迁移风格

- **不用 `response_model`**：每个 router 手写 `_to_out(obj) -> dict`（枚举取 `.value`、日期 `isoformat`），辅助函数补关联名，批量预取消除 N+1。
- **未启用 alembic**：启动时 `Base.metadata.create_all` 建表；增量加列走 `db/migrate.py` 的手写 `ensure_*` 函数，在 startup 调用。
- **两份 schema 手动同步**：SQLAlchemy 模型（`app/models/`）与 `backend/sql/schema.sql`（MySQL/docker 初始化用）。
- P3 集成层三张占位表（`integration`/`api_token`/`integration_event`）含 JSON 列且无业务代码，建表时被**显式排除**（`_SKIP_TABLES`）。

### 4. JWT（`core/security.py`）

- bcrypt 密码；HS256 token，payload 带 `type`（access/refresh）。`get_current_user` 校验 `type=="access"`，`/auth/refresh` 校验 `type=="refresh"`，两者不可混用。
- access 默认 120 分钟、refresh 30 天。登出仅前端丢弃 token，服务端无黑名单。

---

## 三、领域模型与数据字典

已注册模型（`models/__init__.py`）：User / Project / Team / ProjectMember / Task / DailyReport / RemainingIssue / ToolCategory / TestTool / **AiTask / TestCase / ChecklistItem / ExecRun / RunnerDevice / ReleaseRecord**（后 6 个是文档未记载的新模块）。

### 字段级数据字典（重点：唯一约束与状态字段）

| 表 | 唯一约束 | 关键字段/状态 |
|---|---|---|
| `user` | `username` unique | `is_platform_admin`、`status`(active/disabled) |
| `project` | `code` unique | `status`(active/archived) |
| `project_member` | **`uk_user_project(user_id, project_id)`** | `role`(admin/member/guest) |
| `task` | — | `assigned_by`/`assigned_to`、`assigned_date`(索引)、`priority`(p0-p3,默认 p2)、`status`(pending/testing/blocked/online/**closed**)、**`status_locked`**、`online_at`/`closed_at`/`close_note`、P1 加的 `requirement_url`/`developer` |
| `daily_report` | **`uk_task_date(task_id, report_date)`** → upsert | `progress_pct`(0-100)、`is_online`、`workload_hours` Numeric(5,1)、`summary` |
| `remaining_issue` | — | **双挂载路径**：`report_id`(报表路径,可空) 或 `task_id`+`checklist_item_id`(清单直挂,可空)；`severity`(blocker/major/minor)、`status`(open/resolved)、`external_ref` |
| `checklist_item` | **`uq_checklist_task_case(task_id, test_case_id)`** | `exec_status`(pending/passed/failed/blocked)、`executed_by`/`executed_at` |
| `ai_task` | — | `input_type`(text/url/file)、`status`(running/done/failed)、`output_raw`、`case_count`、`cost_usd`、`output_tokens`、`duration_ms` |
| `test_case` | — | `category`、`title`、`steps`、`expected`、`priority`、**`exec_kind`**(gui/api/cli/e2e/manual)、`kind_reason`、**`script`**(结构化步骤 JSON,仅 gui/e2e)、`review_status`(pending/adopted/rejected)、`reviewed_at`、`adopted`(兼容列) |
| `exec_run` | — | `checklist_item_id`(回写落点,SET NULL)、`test_case_id`/`task_id`/`project_id`、`runner`、`kind`、`status`(pending/running/passed/failed)、`payload`(用例快照 JSON)、`verdict`/`reason`/`evidence_url`/`duration_ms`、`enqueued_by` |
| `runner_device` | **`uk_owner_runner(owner_id, runner_id)`** | `token` unique(`secrets.token_hex(32)`)、`last_seen_at` |
| `release_record` | — | `version`、**`sub_product`**(白名单枚举)、`release_date`(索引)、`req_count`、`content`/`memo`、`created_by` |

### 枚举总览（`core/enums.py`）

- `ProjectRole`：admin / member / guest（`WRITE_ROLES` = admin + member，guest 不可写）
- `TaskStatus`：pending / testing / blocked / online / closed
- `TaskPriority`：p0 / p1 / p2 / p3
- `IssueSeverity`：blocker / major / minor；`IssueStatus`：open / resolved
- `AiTaskStatus`：running / done / failed；`AiInputType`：text / url / file
- `ReviewStatus`：pending / adopted / rejected
- `ChecklistStatus`：pending / passed / failed / blocked
- `ExecKind`：gui / api / cli / e2e / manual
- `ExecStatus`：pending / running / passed / failed
- `ToolStatus`：online / offline

---

## 四、两条核心业务链路

### 链路一：传统测试管理（P1/P2）

```
Task(管理员按天指派,四态流转)
  → DailyReport(成员对每条当日任务 upsert:进度%/是否上线/工作量人时/小结)
  → RemainingIssue(日报下挂结构化遗留问题:标题+严重度+负责人+外部缺陷引用)
```

- `daily_report` 对 `(task_id, report_date)` 唯一，提交走 **upsert**；成员只能为指派给自己的任务提交。
- **不建独立统计表**，全部现算聚合（`stats.py`）：
  - `GET /stats/overview`：工作台跨项目今日 KPI（基于 `Task.status` GROUP BY，与日报解耦，`done_rate=online/total`）+ 近 7 天趋势。
  - `GET /stats/daily`：某项目某天应交/已交/未交名单/平均进度/上线数/遗留问题数。
  - `GET /stats/workload`：按成员/按天聚合任务数与上线数（SQL 聚合下推，`COUNT(id)` + `SUM(CASE status==online→1)`）。
  - `GET /stats/ai`：AI 战绩墙——生成/采纳/成本/耗时/维度分布/优先级分布/趋势（生成类按 `created_at`、采纳类按 `reviewed_at`，用 `func.date()` 兼容 SQLite/MySQL）。

### 链路二：AI 生成测试用例 → 本地执行闭环（平台重头戏）

分四段：

```
① QA Copilot 生成测试点 (subprocess 调 claude, SSE 流式落库)
      ↓ 三态评审
② 采纳一条带 task_id 的用例 → 副作用 upsert 一个 checklist_item
      ↓ 前端勾选下发
③ POST /api/exec-queue/enqueue → ExecRun(pending), 用例快照(含 script)入队
      ↓ Pull 轮询
④ runner 拉取 → claim → 本地执行 → PATCH 回写 verdict → 同步 checklist_item.exec_status
```

**① QA Copilot 生成（`services/claude_runner.py` + `api/ai.py`）**

- 后端用 **subprocess 调本机 `claude` CLI**（非交互 `-p`，`--output-format stream-json --verbose`），逐行解析事件流。
- **安全纵深防御**：`--disallowedTools` 禁掉 Bash/Read/Write/WebFetch/Task 等一切可改文件/执行/联网工具；`--strict-mcp-config` + 空 MCP 隔离本机 MCP；`cwd` 指向临时目录（避免读到项目 CLAUDE.md、触发 hook）。纯文本生成本不需要工具，禁用是纵深防御。
- **资源控制**：全局并发信号量（`AI_MAX_CONCURRENCY=2`，满了直接拒绝）；单次硬超时（默认 600s）；后台读线程 + 队列，空转时发 **SSE 心跳**（`: hb`）防网关按空闲切断长连接。
- **三种输入源**（殊途同归填入 `requirement`）：手动文本 / 需求 URL（`extract-url`）/ 上传文档（`extract-file`，txt/md/docx/pdf，≤5MB）。
- **输出契约**：强约束模型输出 JSON 数组，每条含 category / title / steps / expected / priority + **`kind`**(gui/api/cli/e2e/manual) + `kind_reason` + **`script`**（仅 gui/e2e 的结构化可执行步骤）。解析层容错：剥 markdown fence、非法/缺失 kind 兜底 manual、script 校验不过降级 manual、"名不副实的 e2e"（步数 <5 或实质交互 <2）自动纠偏为 gui。
- **流式落库的坑**：`StreamingResponse` 生成器在 `get_db` 关闭后才迭代，故路由体内先建 running 记录拿 id，SSE 生成器内部**另开 `SessionLocal`** 完成落库。

**② 评审与回流（`api/ai.py`）**

- 三态评审 `ReviewStatus`（pending/adopted/rejected）。采纳一条带 `task_id` 的用例会**副作用地 upsert 一个 `ChecklistItem`**；取消采纳则删除仍 pending 的清单项（已执行的保留）。
- 可按当前 steps/expected **重新生成 script**（`POST /testcases/{cid}/gen-script`，同步调 claude，注入选择器 key 清单）。

**③ 验收清单 → 下发（`api/checklist.py` + `api/exec_queue.py`）**

- 前端勾选清单项 → `POST /api/exec-queue/enqueue`，把用例快照（含解析回数组的 script）写入 `ExecRun`（status=pending）。**manual 类用例禁止下发**。

**④ runner 拉取执行并回写**（详见第五节）。

---

## 五、执行机工具链 `tools/qalab-runner/`

"AI 生成 → 本地自动化执行 → 回写"闭环的执行端，部署在测试人员**自己的机器**上（Node，零第三方依赖）。

### 数据流与四接口契约

- **触发 = Pull 轮询**（runner 主动拉），平台零侵入，绕开一切入站网络问题。

```
GET  /api/exec-queue          runner 拉取 pending (runner token)
POST /api/exec-queue/{id}/claim  认领防重跑 + 归属校验 (runner token)
       ↓ 本地按 kind 执行
PATCH /api/exec-queue/{id}     回写 {verdict,reason,evidence_url,duration}
                               平台同步 checklist_item.exec_status，形成闭环
```

### 按 kind 分派执行（`runner.mjs`）

- `gui` / `e2e`：通过 gui-mcp server 用 Playwright `connectOverCDP(:9222)` 操作被测 Electron 客户端（namiclaw）的 DOM，按 CSS/语义 key 断言，无坐标依赖、不怕锁屏。
- `api` / `cli`：给 claude `Bash` 工具跑 curl / 起进程校验退出码与输出。
- **硬禁内置工具**：`--disallowedTools` 排除 Read/Grep/Edit… 否则 claude 会跑去翻本地仓库源码而不调 gui（实测踩过）。payload 走 **stdin** 而非 argv，防命令注入。

### 确定性优先（`step-executor.mjs`）

- 若用例带结构化 script，**不经 LLM** 直接按步骤执行：connect / click / fill / wait_for / wait_response / get_text / assert_text / assert_visible / screenshot / goto。`assert_*` 直接算 pass/fail——快、稳、省、可复现。
- 只有遇到**未知 action** 或 **`judge`**（主观判定）步才退回 claude 兜底（judge 未注入 judgeFn 时整条降级）。
- 判定：任一 `assert_*` / judge 失败 → 整条 fail；全部通过 → pass。

### 语义选择器注册表（`gui-mcp/selectors.json`）

- 当前约 57 个 key（如 loginUserName / loginSubmit）。AI 生成 script 时后端注入这份清单，让模型只用库内已知元素；找不到合适 key 就改判 manual、不瞎编 selector。

---

## 六、前端结构

### API 层

- `api/index.js` 集中所有接口（薄封装，返回已解包的 `data`）。分组：auth / projects / members / users / tasks / reports / stats / issues / tools / **ai** / **checklist** / **exec** / **devices** / **release**。
- `api/http.js`：baseURL 恒为相对 `/api`（dev 走 vite 代理，生产同源托管无 CORS）；请求拦截器注入 `Bearer token`；响应拦截器解包信封；401 用 refresh_token 静默重放一次。
- **SSE 实现**：`streamTestcases()` 不用 EventSource，用原生 `fetch` POST + `resp.body.getReader()` + `TextDecoder` 流式读，按 `\n\n` 切帧、取 `data:` 行 JSON.parse，按 `evt.type` 分发；支持 `AbortController` 取消。

### 状态管理

- `store/auth.js`（Pinia）：token 存 localStorage（`tp_token`/`tp_refresh`）；getter `isPlatformAdmin`、`isLoggedIn`、`roleIn(projectId)`；action `login`/`setToken`/`fetchMe`/`logout`。
- `store/app.js`：路由级 loading + **项目列表进程内缓存**（`fetchProjects(force)` 并发去重，写操作后 `invalidateProjects()`）。

### 布局导航（`MainLayout.vue`）

侧栏深色，折叠态持久化。菜单分组：工作台 → 发版记录 → 测试设计（AI 测试助手/用例库/已采纳用例）→ 测试执行（任务分配/执行结果/遗留问题）→ 我的工作台（我的日报/我的设备）→ 数据统计（日报统计/工作量统计/AI 战绩墙）→ 测试工具广场 → 组织管理（项目/用户，置底）。组织管理整组与工具配置 `v-if="auth.isPlatformAdmin"`。

### 页面（19 个 view）

工作台 Dashboard、AI 测试助手 AITestGen（SSE 流式生成 + 三态评审）、用例库 CaseLibrary（分页/多维筛选/生成 script/下发执行）、已采纳用例 AdoptedCases、AI 战绩墙 AIWall、任务 Tasks（四态流转 + 行内验收清单）、我的日报 MyReports（upsert + 清单勾选）、我的设备 MyDevices、执行结果 ExecResults、日报统计、工作量统计、遗留问题、发版记录 ReleaseNotes（看板 + 明细 + 子产品 Tab + 配色）、工具广场/工具管理、项目/用户/成员管理。

### utils

- `lastProject.js`：`tp_last_project` 记住跨页选中项目。
- `markdown.js`：markdown-it（`html:false`）+ DOMPurify 双重防 XSS，导出 `renderMarkdown()`。
- `residue.js`：路由切换后清理 Element Plus 卡死的 v-loading/dialog 残留遮罩。

---

## 七、关键机制补遗

**① 验收清单"顺延"逻辑（`checklist.py`）**
`GET /tasks/checklist-summary` 汇总某项目某天清单时，纳入条件是 `assigned_date == date` **或** `assigned_date < date 且 status ∉ {online, closed}`——即**未完成的历史任务自动顺延**到今天，不因跨天消失。

**② 遗留问题的回流闭环**
AI 用例被**采纳** → 自动生成 `checklist_item` → 执行判 **failed** → `POST /checklist/{id}/to-issue`（仅 failed 可转）建一条 `report_id=None` 的**清单直挂 issue**。因此 `stats` 里 `open_issues` 对"报表路径 ∪ 清单路径" **按 id 去重**，避免双算。

**③ 需求抽取的 SSRF 防护（`services/extractors.py`）**
`extract_from_url` 非裸抓：仅允许 http/https、**禁内网/环回/保留网段**、**重定向逐跳校验**、2MB 上限、正则剥标签。飞书链接转 `feishu.py` 走 OpenAPI（tenant_access_token 内存缓存 + 过期刷新，识别 docx/wiki/sheets/base，sheets 限 5 表 A1:Z200、base 限 5 表 100 条）。

**④ 设备 token 安全约定（`devices.py`）**
全私人视图（平台管理员也不特殊化）；注册时**明文 token 仅返回一次**，之后 GET 一律脱敏；可 reset-token。

**⑤ 有历史包袱的迁移（`migrate.py`）**
- `migrate_task_status`：`doing→testing`、`done→online` 历史迁移（MySQL 下先放宽 ENUM 再收紧）。
- `ensure_issue_columns`：为把 `report_id` 改可空，MySQL 下先 drop FK 再重建。
- `ensure_exec_run_kind`：给 kind/status ENUM 补 e2e/manual，并把坏行修成 manual。
- 其余：`ensure_task_columns`、`ensure_testcase_columns`（含回填）、`ensure_release_columns`（sub_product）、`ensure_perf_indexes`。

---

## 八、配置项速查（`core/config.py`）

| 分类 | 配置项 |
|---|---|
| 数据库 | `DATABASE_URL`（默 SQLite）vs 分字段 `DB_HOST/PORT/USER/PASSWORD/NAME/CHARSET`（设 DB_HOST 即拼 MySQL，密码 URL 编码） |
| JWT | `JWT_SECRET`、`JWT_ALG=HS256`、`ACCESS_TOKEN_EXPIRE_MINUTES=120`、`REFRESH_TOKEN_EXPIRE_DAYS=30` |
| 种子/CORS | `SEED_ADMIN_USERNAME/PASSWORD/NAME`、`CORS_ORIGINS` |
| Runner | `RUNNER_TOKEN`（空则拒一切 runner 请求） |
| AI | `AI_ENABLED`、`CLAUDE_BIN`、`AI_MODEL`、`AI_TIMEOUT_SECONDS=600`、`AI_MAX_CONCURRENCY=2`、`SELECTORS_PATH` |
| 飞书 | `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE` |

---

## 九、常用命令

后端（默认 SQLite，`backend/` 下）：
```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000   # Swagger: http://localhost:8000/docs
```

前端（`frontend/` 下）：
```bash
npm install
npm run dev        # http://localhost:5173，/api 代理到 :8000
npm run build      # 产物 dist/（提交入库，供后端同源托管）
```

整栈（生产形态，MySQL + backend + frontend）：
```bash
docker compose up -d
```

> 本仓库**没有配置测试框架，也没有 lint/format 工具**——"冒烟验证"是手动端到端测试。
