# 对话测评链路 · 子项 0 数据模型 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为"对话测评链路"大工程建数据模型地基——新增 `eval_query`(对话测评题) 与 `eval_run`(一次执行+判定结果) 两张表及配套枚举,生成任务复用现有 `AiTask`。

**Architecture:** 纯加表,零改动现有链路。对齐平台既有建模风格(`Text` 存 JSON 兼容 MySQL 5.6、枚举带 `length`、软关联 project/task)。新表走 `Base.metadata.create_all` 自动建(已被 `models/__init__.py` 导入即进 `sorted_tables`);MySQL 初始化用的 `sql/schema.sql` 手工同步。

**Tech Stack:** FastAPI + SQLAlchemy 2.0(`Mapped`/`mapped_column` 声明式) + Python enum;本地 SQLite、生产 MySQL 5.6。

**Spec:** `docs/superpowers/specs/2026-08-21-eval-pipeline-data-model-design.md`

## Global Constraints

- **不用原生 JSON 列**:一切结构化数据用 `Text` 存 JSON 字符串(兼容生产 MySQL 5.6)。
- **枚举带 length**:`Enum(XxxEnum, length=N)`,值为小写字符串;枚举定义放 `app/core/enums.py`(不放 models,避免 api/deps 循环导入)。
- **本仓库无测试框架**:没有 pytest/eslint/ruff——不要臆造。验证 = 手动跑一次性 Python 脚本 + 启动后端观察建表(见各任务验证步)。
- **两份 schema 手动同步**:SQLAlchemy 模型(`app/models/`,`create_all` 用) 与 `backend/sql/schema.sql`(MySQL/docker 初始化用) 必须一致。
- **新模型必须在 `app/models/__init__.py` 汇总导入**,`create_all` 才建得到。
- **本 spec 边界**:只有数据模型。不含任何 API 路由 / service / 前端 / CLI 改造 / 判定逻辑 / 回填 / multica。
- 后端命令在 `backend/` 目录下跑;本地默认 SQLite,开箱即用。

## 文件结构（本子项触及 4 个文件）

- **Modify** `backend/app/core/enums.py` —— 追加 `EvalRunStatus` / `EvalDeviceKind` / `EvalVerdict` 三个枚举(文件尾部,`ALL_PROJECT_ROLES` 常量之前)。
- **Create** `backend/app/models/ai_eval.py` —— 新增 `EvalQuery` / `EvalRun` 两个模型。独立新文件(不塞进 `models/ai.py`,后者是功能测试点专属;对话测评是不同领域)。
- **Modify** `backend/app/models/__init__.py` —— 汇总导入 `EvalQuery`/`EvalRun` + 加进 `__all__`。
- **Modify** `backend/sql/schema.sql` —— 追加 `eval_query`、`eval_run` 两段 `CREATE TABLE`(放 `test_case`/`exec_run` 相关段落之后)。

`AiTask.kind` 加值 `eval_query_gen` **不改任何代码**——它是 `String(32)` 自由字符串列,生成侧(子项 1)传值即可,本子项无需触碰 `models/ai.py`。

---

### Task 1: 枚举 + 两个模型 + 汇总导入

一个完整可验证的交付:枚举定义好、两张表能被 `create_all` 建出、`Text`-JSON 往返无损、枚举取值正确、且不破坏现有 `AiTask`。枚举是模型的前置(scaffolding),故与模型同任务。

**Files:**
- Modify: `backend/app/core/enums.py`(尾部追加,`ALL_PROJECT_ROLES` 定义之前)
- Create: `backend/app/models/ai_eval.py`
- Modify: `backend/app/models/__init__.py`
- Verify(临时脚本,验证后删): `backend/_verify_eval_model.py`

**Interfaces:**
- Consumes(现有,勿改):
  - `app.db.session.Base`、`app.db.session.SessionLocal`、`app.db.session.engine`
  - `app.core.enums.ReviewStatus`(复用作 `eval_query.review_status`,值 pending/adopted/rejected)
- Produces(子项 1~4 依赖这些确切名字与类型):
  - 枚举 `EvalRunStatus`(pending/running/done/judging/judged/failed)、`EvalDeviceKind`(web/desktop/cli)、`EvalVerdict`(passed="pass"/failed="fail"/error="error")
  - 模型 `EvalQuery`(表 `eval_query`)、`EvalRun`(表 `eval_run`),字段签名见下 Step 2/3
  - `AiTask.kind` 约定新值字符串 `"eval_query_gen"`(不改模型,仅约定)

- [ ] **Step 1: 追加三个枚举到 `enums.py`**

在 `backend/app/core/enums.py` 中,`ALL_PROJECT_ROLES = {...}` 那一行**之前**追加:

```python
class EvalRunStatus(str, enum.Enum):
    """一次对话测评执行 + 判定的生命周期。"""
    pending = "pending"    # 已下发，等执行机拉取
    running = "running"    # 执行机已认领、对话进行中
    done = "done"          # 对话+轨迹抓取完成（尚未判定）
    judging = "judging"    # 轨迹已回传，大模型判定中
    judged = "judged"      # 判定完成（终态）
    failed = "failed"      # 执行失败（对话没跑起来/抓取失败；区别于“判定不通过”）


class EvalDeviceKind(str, enum.Enum):
    """执行载体（对齐 ai-eval-cli 的三种运行形态）。"""
    web = "web"            # Web 多账号（ContextPool，注入 storageState 登录态）
    desktop = "desktop"    # 桌面客户端（CDP 连 Electron 单客户端多对话）
    cli = "cli"            # 命令行执行（具体形态见子项 2；先占位）


class EvalVerdict(str, enum.Enum):
    """大模型对一次会话的总判定。"""
    passed = "pass"        # 三维皆过（passed 规避 Python 保留字 pass，值仍为 "pass"）
    failed = "fail"        # 有维度不过
    error = "error"        # 判定本身出错（轨迹缺失/判定引擎异常）
```

- [ ] **Step 2: 创建 `models/ai_eval.py` 的 `EvalQuery`**

新建 `backend/app/models/ai_eval.py`,写入文件头 + `EvalQuery`:

```python
"""对话测评链路的数据模型：eval_query（测评题）+ eval_run（一次执行+判定）。

与 models/ai.py 的 test_case（功能测试点）是不同领域，故独立文件。
生成任务复用现有 AiTask（kind='eval_query_gen'），此处不重复建生成任务表。
一切结构化数据用 Text 存 JSON 字符串（兼容 MySQL 5.6，不用原生 JSON 列）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EvalDeviceKind, EvalRunStatus, ReviewStatus
from app.db.session import Base


class EvalQuery(Base):
    """一道对话测评题：发给被测大模型的 query 及其执行参数。类比 test_case。"""

    __tablename__ = "eval_query"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 哪次 AI 生成的（复用 AiTask，kind='eval_query_gen'）；人工录入为 NULL。
    ai_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 生成引擎 claude/deepseek，冗余自 ai_task.provider（免 join），default claude。
    provider: Mapped[str] = mapped_column(String(16), default="claude", server_default="claude", index=True)
    title: Mapped[str] = mapped_column(String(512))
    prompt: Mapped[str] = mapped_column(Text)  # query 正文（发给被测模型的提问）
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: [{name,file_token?/url?}]
    # 多轮分组键（对齐 CLI conversationId）；NULL/空 = 单轮独立会话。
    conversation_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 同组内第几轮（0 起）
    dialog_options: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {model?,chatMode?,thinkingDepth?}
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)  # 期望产物/行为（判定参照；可空）
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, length=16), default=ReviewStatus.pending, server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- [ ] **Step 3: 在同文件追加 `EvalRun`**

接着在 `backend/app/models/ai_eval.py` 末尾追加:

```python
class EvalRun(Base):
    """一次对话测评执行 + 判定结果。类比 exec_run，但承载会话全过程轨迹 + 三维判定。

    一道题可派到不同设备/多次执行，各留一条。判定与执行合并在此表（一对一）。
    """

    __tablename__ = "eval_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # —— 下发 ——（题删了执行记录仍留痕，故 SET NULL，学 exec_run.test_case_id）
    eval_query_id: Mapped[int | None] = mapped_column(
        ForeignKey("eval_query.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 一次下发一批
    runner: Mapped[str] = mapped_column(String(64), default="mac-01", server_default="mac-01", index=True)
    device_kind: Mapped[EvalDeviceKind] = mapped_column(
        Enum(EvalDeviceKind, length=8), default=EvalDeviceKind.web, server_default="web"
    )
    status: Mapped[EvalRunStatus] = mapped_column(
        Enum(EvalRunStatus, length=16), default=EvalRunStatus.pending, server_default="pending", index=True
    )
    # —— CLI 抓回的会话数据 ——
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # work.n.cn 会话 UUID
    share_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_share_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最终回答正文
    trace: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 会话全过程轨迹（见 spec §5.4）
    reported_duration: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 平台上报耗时（秒）
    bean_cost: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 算力豆变动
    tokens: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 本次 tokens（仅记录）
    # —— 大模型判定 ——
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)  # EvalVerdict 值；NULL=未判定
    verdict_dims: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 三维结论（见 spec §5.5）
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 判定理由汇总
    judged_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 判定用的引擎
    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    pushed_multica: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    multica_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)  # multica 侧任务 id/链接
    # —— 通用 ——
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 执行失败/未完成原因
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 墙钟耗时
    enqueued_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: 汇总导入 `models/__init__.py`**

在 `backend/app/models/__init__.py` 中,`from app.models.perf_report_set import PerfReportSet` 这类导入之后追加一行:

```python
from app.models.ai_eval import EvalQuery, EvalRun
```

并在 `__all__` 列表末尾(`"PerfReportSet",` 之后)追加:

```python
    "EvalQuery",
    "EvalRun",
```

- [ ] **Step 5: 写验证脚本**

新建 `backend/_verify_eval_model.py`(临时,验证完删除):

```python
"""子项0 数据模型验证：建表 + Text-JSON 往返 + 枚举取值 + AiTask 新 kind 兼容。
在 backend/ 目录下运行：python _verify_eval_model.py
"""
import json

from app.db.session import Base, SessionLocal, engine
from app.models import AiTask, EvalQuery, EvalRun
from app.core.enums import EvalDeviceKind, EvalRunStatus, EvalVerdict, ReviewStatus

# 1) 建表（新表随 create_all 建出）
Base.metadata.create_all(bind=engine)
insp = engine.dialect.get_table_names(engine.connect())
assert "eval_query" in insp, "eval_query 未建出"
assert "eval_run" in insp, "eval_run 未建出"

db = SessionLocal()
try:
    # 2) 需要一个 project 作外键；取任意已有的，没有则跳过外键强校验（SQLite 默认不强制 FK）
    from app.models import Project
    proj = db.query(Project).first()
    pid = proj.id if proj else 1

    # 3) AiTask 新 kind 兼容（String 列，加值零迁移）
    at = AiTask(project_id=pid, user_id=1, kind="eval_query_gen", provider="claude")
    db.add(at)
    db.flush()
    assert at.kind == "eval_query_gen"

    # 4) EvalQuery：Text-JSON 往返 + 枚举
    q = EvalQuery(
        project_id=pid, ai_task_id=at.id, provider="claude",
        title="测评题1", prompt="用python写贪吃蛇",
        attachments=json.dumps([{"name": "a.png", "url": "http://x/a.png"}], ensure_ascii=False),
        conversation_group="mt-a", turn_index=0,
        dialog_options=json.dumps({"model": "豆包", "thinkingDepth": "深度"}, ensure_ascii=False),
        expected="产出可运行的贪吃蛇网页",
        review_status=ReviewStatus.pending,
    )
    db.add(q)
    db.flush()

    # 5) EvalRun：trace/verdict_dims 往返 + 枚举 + 布尔
    trace = {
        "session_id": "sess-uuid", "run_id": "run-1",
        "thinking": "先分析需求...",
        "tool_calls": [{
            "tool_call_id": "tc1", "name": "网页搜索",
            "original_tool_name": "mcp__serper__web_search",
            "is_mcp": True, "mcp_server": "serper",
            "args": {"q": "贪吃蛇"}, "result_text": "...", "reached_result": True,
        }],
        "artifacts": [{"name": "snake.html", "kind": "file", "share_link": "http://x/s"}],
        "answer": "完成",
    }
    dims = {
        "thinking_complete": {"pass": True, "note": "完整"},
        "tools_ok": {"pass": True, "note": "正常"},
        "artifact_expected": {"pass": True, "note": "符合"},
    }
    r = EvalRun(
        eval_query_id=q.id, project_id=pid, batch_id="20260821-000000-abcd",
        runner="mac-01", device_kind=EvalDeviceKind.web, status=EvalRunStatus.judged,
        session_id="sess-uuid", share_link="http://x/share", answer="完成",
        trace=json.dumps(trace, ensure_ascii=False),
        verdict=EvalVerdict.passed.value, verdict_dims=json.dumps(dims, ensure_ascii=False),
        judged_by="claude", is_abnormal=False, pushed_multica=False, duration_ms=1234,
    )
    db.add(r)
    db.commit()

    # 6) 读回校验往返无损
    r2 = db.get(EvalRun, r.id)
    back = json.loads(r2.trace)
    assert back["tool_calls"][0]["original_tool_name"] == "mcp__serper__web_search"
    assert back["tool_calls"][0]["is_mcp"] is True
    assert r2.status == EvalRunStatus.judged
    assert r2.device_kind == EvalDeviceKind.web
    assert r2.verdict == "pass"
    assert r2.is_abnormal is False
    q2 = db.get(EvalQuery, q.id)
    assert json.loads(q2.attachments)[0]["name"] == "a.png"
    assert q2.review_status == ReviewStatus.pending

    print("OK: eval_query/eval_run 建表 + JSON 往返 + 枚举 + AiTask 新 kind 全部通过")
finally:
    db.rollback()
    db.close()
```

- [ ] **Step 6: 运行验证脚本,确认通过**

Run(在 `backend/` 目录下):
```bash
python _verify_eval_model.py
```
Expected: 末行打印 `OK: eval_query/eval_run 建表 + JSON 往返 + 枚举 + AiTask 新 kind 全部通过`,进程退出码 0,无 traceback。

若报 `no such table` / 字段错 → 检查 Step 2~4 的字段名与导入;若 JSON 断言失败 → 检查 `Text` 列是否误用了其他类型。

- [ ] **Step 7: 删除验证脚本**

Run:
```bash
rm backend/_verify_eval_model.py
```
(验证是一次性的,不留临时脚本进仓库。)

- [ ] **Step 8: 提交**

```bash
git add backend/app/core/enums.py backend/app/models/ai_eval.py backend/app/models/__init__.py
git commit -m "feat(eval): 对话测评数据模型 eval_query/eval_run + 枚举

子项0 数据模型地基。新增 EvalQuery(测评题)/EvalRun(执行+判定合并) 两模型
及 EvalRunStatus/EvalDeviceKind/EvalVerdict 枚举;生成任务复用 AiTask。
新表随 create_all 自动建(已汇总导入)。sql/schema.sql 同步见后续提交。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 同步 `sql/schema.sql`（MySQL 初始化镜像）

`schema.sql` 是 MySQL/docker 初始化用的完整 DDL,须与模型一致(Global Constraints:两份 schema 手动同步)。本任务把两张新表的 DDL 补进去,可被 fresh reviewer 独立评审(字段/类型/索引与模型逐一对得上)。

**Files:**
- Modify: `backend/sql/schema.sql`(追加两段,放 `exec_run` 那段 `CREATE TABLE` 之后)

**Interfaces:**
- Consumes: Task 1 定的 `EvalQuery`/`EvalRun` 字段与类型(此处翻译成 MySQL DDL)
- Produces: 无(纯 MySQL 侧镜像,不被代码引用)

- [ ] **Step 1: 追加 `eval_query` 与 `eval_run` 的 CREATE TABLE**

在 `backend/sql/schema.sql` 中,`CREATE TABLE \`exec_run\` (...)` 那一段**之后**追加(类型映射:`Text`→TEXT、`String(n)`→VARCHAR(n)、`Boolean`→TINYINT(1)、枚举列→VARCHAR、`Integer`→INT、PK→BIGINT AUTO_INCREMENT,对齐文件里现有表的写法):

```sql
-- 对话测评题（发给被测大模型的 query 及执行参数）
CREATE TABLE `eval_query` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `project_id` BIGINT NOT NULL,
  `task_id` BIGINT NULL,
  `ai_task_id` BIGINT NULL,
  `provider` VARCHAR(16) NOT NULL DEFAULT 'claude',
  `title` VARCHAR(512) NOT NULL,
  `prompt` TEXT NOT NULL,
  `attachments` TEXT NULL,
  `conversation_group` VARCHAR(64) NULL,
  `turn_index` INT NOT NULL DEFAULT 0,
  `dialog_options` TEXT NULL,
  `expected` TEXT NULL,
  `review_status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `reviewed_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_evalquery_project` (`project_id`),
  KEY `idx_evalquery_task` (`task_id`),
  KEY `idx_evalquery_aitask` (`ai_task_id`),
  KEY `idx_evalquery_provider` (`provider`),
  CONSTRAINT `fk_evalquery_project` FOREIGN KEY (`project_id`) REFERENCES `project` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_evalquery_task` FOREIGN KEY (`task_id`) REFERENCES `task` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_evalquery_aitask` FOREIGN KEY (`ai_task_id`) REFERENCES `ai_task` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 一次对话测评执行 + 判定结果（会话全过程轨迹 + 三维判定，合并一行）
CREATE TABLE `eval_run` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `eval_query_id` BIGINT NULL,
  `project_id` BIGINT NOT NULL,
  `batch_id` VARCHAR(32) NULL,
  `runner` VARCHAR(64) NOT NULL DEFAULT 'mac-01',
  `device_kind` VARCHAR(8) NOT NULL DEFAULT 'web',
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
  `session_id` VARCHAR(64) NULL,
  `share_link` VARCHAR(512) NULL,
  `artifact_share_link` VARCHAR(512) NULL,
  `answer` TEXT NULL,
  `trace` TEXT NULL,
  `reported_duration` VARCHAR(32) NULL,
  `bean_cost` VARCHAR(32) NULL,
  `tokens` VARCHAR(32) NULL,
  `verdict` VARCHAR(16) NULL,
  `verdict_dims` TEXT NULL,
  `verdict_reason` TEXT NULL,
  `judged_by` VARCHAR(16) NULL,
  `is_abnormal` TINYINT(1) NOT NULL DEFAULT 0,
  `pushed_multica` TINYINT(1) NOT NULL DEFAULT 0,
  `multica_ref` VARCHAR(512) NULL,
  `reason` TEXT NULL,
  `duration_ms` INT NULL,
  `enqueued_by` BIGINT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_evalrun_query` (`eval_query_id`),
  KEY `idx_evalrun_project` (`project_id`),
  KEY `idx_evalrun_batch` (`batch_id`),
  KEY `idx_evalrun_runner` (`runner`),
  KEY `idx_evalrun_status` (`status`),
  KEY `idx_evalrun_abnormal` (`is_abnormal`),
  CONSTRAINT `fk_evalrun_query` FOREIGN KEY (`eval_query_id`) REFERENCES `eval_query` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_evalrun_project` FOREIGN KEY (`project_id`) REFERENCES `project` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_evalrun_user` FOREIGN KEY (`enqueued_by`) REFERENCES `user` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 字段一致性自查（逐列比对模型 vs DDL）**

对照 `backend/app/models/ai_eval.py`,逐列确认 DDL 覆盖了模型的**每一个** `mapped_column`、类型/可空/默认一致。重点核对:
- `eval_query`:14 个业务列 + `id`(模型 Step 2 有几列 DDL 就有几列)。
- `eval_run`:注意 `device_kind`/`status` 是 VARCHAR(非 MySQL 原生 ENUM,对齐现有 `test_case.exec_kind` 的做法,避免日后加枚举值要 `MODIFY COLUMN`)。
- `is_abnormal`/`pushed_multica` 是 TINYINT(1) DEFAULT 0。

Expected: 无遗漏列、无类型错位。(此步无命令,是人工比对;漏了后续 MySQL 建库会与模型不一致。)

- [ ] **Step 3: (可选,若本机有 MySQL/docker)验证 DDL 语法**

若能起 MySQL:把这两段 DDL 在一个空库执行,确认无语法错。
Run(示例,按实际 MySQL 连接调整):
```bash
mysql -h127.0.0.1 -uroot -p <dbname> < backend/sql/schema.sql
```
Expected: 无 `ERROR ... near ...` 语法报错。
(若本机无 MySQL,跳过本步——Step 2 的人工比对是底线保障;SQLite 侧已在 Task 1 Step 6 验证过模型可建。)

- [ ] **Step 4: 提交**

```bash
git add backend/sql/schema.sql
git commit -m "feat(eval): sql/schema.sql 同步 eval_query/eval_run 建表 DDL

对齐 Task1 的模型：两份 schema 手动同步（MySQL/docker 初始化侧）。
device_kind/status/verdict 用 VARCHAR 而非原生 ENUM，避免日后加值要 MODIFY COLUMN。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage（逐节对照 spec）:**
- §4 三枚举 → Task 1 Step 1 ✓
- §5.1 `EvalQuery` → Task 1 Step 2 ✓(14 业务列全覆盖)
- §5.2 `EvalRun` → Task 1 Step 3 ✓(下发/会话数据/判定/通用四组列全覆盖)
- §5.3 复用 AiTask(kind=eval_query_gen) → Task 1 Step 5 验证兼容 + Global Constraints 说明"不改代码" ✓
- §5.4 trace 结构 / §5.5 verdict_dims 结构 → 作为 `Text` 列 + 验证脚本里的往返样例覆盖 ✓(结构本身是"约定",由子项 2/3 写入方遵守,本子项只保证列能无损存 JSON)
- §6 迁移(create_all + schema.sql 同步 + AiTask 零迁移) → Task 1 Step 4(汇总导入) + Task 2 + Global Constraints ✓
- §8 验证方式 → Task 1 Step 5~6 的手动脚本 + Task 2 Step 3 ✓
- §9 交付清单 4 项 → enums(T1S1)/models(T1S2-3)/__init__(T1S4)/schema.sql(T2) 全覆盖 ✓

**2. Placeholder 扫描:** 无 TBD/TODO;每个代码步都给了完整代码;验证脚本是完整可跑的,非"写测试如上"。✓

**3. 类型一致性:** 枚举名(`EvalRunStatus`/`EvalDeviceKind`/`EvalVerdict`)、`EvalVerdict.passed` 值为 `"pass"`(Step 1 定义 → Step 5 用 `EvalVerdict.passed.value` 存、断言 `== "pass"`,一致);模型字段名(Step 2/3)与验证脚本(Step 5)、DDL(Task 2)三处逐一对齐(如 `original_tool_name` 只是 trace JSON 内的 key、非列名,无冲突)。✓

---

## Execution Handoff

计划已存 `docs/superpowers/plans/2026-08-21-eval-pipeline-data-model.md`。
