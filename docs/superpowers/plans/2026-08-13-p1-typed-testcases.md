# P1:类型化用例根治误派/跑偏 —— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让平台不再把"不可自动化用例"误派给执行机、让 gui 用例的 claude 不再跑去研究本地仓库——通过新增 `manual`/`e2e` 两个执行类型、平台拒发 `manual`、runner 按 kind 给不同工具集并加兜底护栏。

**Architecture:** 纯增量,落在既有分层内。枚举扩值(`ExecKind` 加 `e2e`/`manual`)→ 后端 Pydantic 自动认新值、`exec_kind` 是 `VARCHAR(8)` 无需改 DDL;migrate 幂等补 `script`/`kind_reason` 两列(为 P3 预留,P1 不填充);`enqueue` 校验拒 `manual`;`runner.mjs` 按 `item.kind` 选 `--allowedTools` 并强化 SYSTEM_PROMPT 护栏;前端下拉加 e2e/manual 且 manual 用例禁下发。

**Tech Stack:** FastAPI + SQLAlchemy 2.0(后端)、Vue3 + ElementPlus(前端)、Node 原生(runner.mjs)。本仓库无测试框架/lint(见 CLAUDE.md),验证=手动端到端 + `node --check` + 后端 import 冒烟。

## Global Constraints

- 统一响应信封 `{code,msg,data}`;后端用 `ok()/fail()`,业务里 `raise HTTPException(status, detail="中文")`。
- 不用 `response_model`;每 router 手写 `_to_out/_to_case_out` 转 dict(枚举取 `.value`、日期 `isoformat`)。
- 两份 schema 手动同步:SQLAlchemy 模型(`app/models/`)与 `backend/sql/schema.sql`;增量改列走 `app/db/migrate.py` 的 `ALTER TABLE ADD COLUMN`(幂等:先 `_columns()` 探列)。
- `exec_kind` 列类型为 `VARCHAR(8)`(非 MySQL ENUM),加枚举值**无需**改 DDL/migrate 列定义。
- runner 纯 Node、无依赖;`--allowedTools` 的值必须是**一个**空格分隔字符串(拆成多 arg 会使白名单失效)。
- 无测试框架:每个"写测试"步用**一次性脚本 / node --check / 后端 import 冒烟 / 手动端到端**代替;不要臆造 pytest/eslint。
- 提交信息用中文 conventional commit,结尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

## 文件结构

- Modify `backend/app/core/enums.py` — `ExecKind` 加 `e2e`/`manual`。
- Modify `backend/app/db/migrate.py` — `ensure_testcase_columns()` 内补 `script`/`kind_reason` 列。
- Modify `backend/app/models/ai.py` — `TestCase` 加 `script`/`kind_reason` 字段。
- Modify `backend/sql/schema.sql` — `test_case` 同步两列。
- Modify `backend/app/api/exec_queue.py` — `enqueue` 拒 `manual`。
- Modify `backend/app/api/ai.py` — `_to_case_out` 输出 `kind_reason`/`script`(为前端/后续用)。
- Modify `tools/qalab-runner/runner.mjs` — 按 kind 选 `--allowedTools` + SYSTEM_PROMPT 护栏 + gui 才 ensureNamiclaw。
- Modify `frontend/src/views/CaseLibrary.vue` — `EXEC_KINDS` 加 e2e/manual。
- Modify `frontend/src/views/Tasks.vue` — 下发时 manual 用例禁选/提示。

---

### Task 1: ExecKind 扩枚举(manual/e2e)

**Files:**
- Modify: `backend/app/core/enums.py:91-95`

**Interfaces:**
- Produces: `ExecKind.e2e = "e2e"`, `ExecKind.manual = "manual"`(供 enqueue/runner/前端引用)。

- [ ] **Step 1: 改枚举**

把 `backend/app/core/enums.py` 的 `ExecKind` 改为:

```python
class ExecKind(str, enum.Enum):
    """自动化执行类型（下发给 runner 时决定 Claude Code 怎么跑）。"""
    gui = "gui"        # GUI 用例：gui-mcp 操作被测客户端 DOM
    api = "api"        # 接口用例：curl / fetch 验证接口与响应
    cli = "cli"        # 命令行用例：起进程校验退出码 / 输出
    e2e = "e2e"        # 端到端：多步 + 等待策略（gui 工具为主，比单点 gui 长/慢）
    manual = "manual"  # 不可自动化：纯人工/探索性/主观体验；平台不派发到执行机
```

- [ ] **Step 2: 验证 import 不报错**

Run: `cd backend && .venv/bin/python -c "from app.core.enums import ExecKind; print([e.value for e in ExecKind])"`
Expected: `['gui', 'api', 'cli', 'e2e', 'manual']`

- [ ] **Step 3: 提交**

```bash
git add backend/app/core/enums.py
git commit -m "feat(enums): ExecKind 增加 e2e/manual 执行类型

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: TestCase 加 script/kind_reason 列(模型 + migrate + schema.sql)

**Files:**
- Modify: `backend/app/models/ai.py`(`TestCase` 类,`exec_kind` 字段之后)
- Modify: `backend/app/db/migrate.py:54-59`(`ensure_testcase_columns` 内,exec_kind 分支之后)
- Modify: `backend/sql/schema.sql:237`(`exec_kind` 行之后)

**Interfaces:**
- Produces: `TestCase.script: str | None`(JSON 字符串,P3 存步骤 DSL,P1 留空)、`TestCase.kind_reason: str | None`(AI 判 kind 的理由)。

- [ ] **Step 1: 模型加字段**

在 `backend/app/models/ai.py` 的 `TestCase` 里,`exec_kind` 那行之后加:

```python
    # AI 判定该 kind 的理由（供人工复核参考；P2 由生成侧填充）
    kind_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 结构化可执行步骤 JSON（步骤 DSL；P3 由生成侧填充，runner 确定性执行）。存 Text-JSON 兼容 MySQL 5.6。
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: migrate 补列(幂等)**

在 `backend/app/db/migrate.py` 的 `ensure_testcase_columns()` 内,`exec_kind` 的 `if` 块之后(回填 UPDATE 之前)加:

```python
        if "kind_reason" not in cols:
            conn.execute(text("ALTER TABLE test_case ADD COLUMN kind_reason TEXT NULL"))
        if "script" not in cols:
            conn.execute(text("ALTER TABLE test_case ADD COLUMN script TEXT NULL"))
```

- [ ] **Step 3: schema.sql 同步**

在 `backend/sql/schema.sql` 的 `` `exec_kind` VARCHAR(8) NOT NULL DEFAULT 'gui', `` 行之后加:

```sql
  `kind_reason` TEXT NULL,
  `script` TEXT NULL,
```

- [ ] **Step 4: 验证 create_all + migrate 幂等(临时 SQLite)**

Run:
```bash
cd backend && rm -f /tmp/p1.db && DB_HOST= DATABASE_URL="sqlite:////tmp/p1.db" PYTHONPATH="$(pwd)" .venv/bin/python -c "
from app.db.session import Base, engine
import app.models
from app.db.migrate import ensure_testcase_columns
Base.metadata.create_all(engine)
ensure_testcase_columns(); ensure_testcase_columns()  # 跑两次验幂等
from sqlalchemy import inspect
print(sorted(c['name'] for c in inspect(engine).get_columns('test_case')))
"
```
Expected: 列名列表包含 `kind_reason` 和 `script`,且两次调用无异常。

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/ai.py backend/app/db/migrate.py backend/sql/schema.sql
git commit -m "feat(testcase): 加 script/kind_reason 列(为类型化可执行用例预留)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 序列化输出 kind_reason/script

**Files:**
- Modify: `backend/app/api/ai.py:44-51`(`_to_case_out`)

**Interfaces:**
- Consumes: `TestCase.kind_reason`, `TestCase.script`(Task 2)。
- Produces: `_to_case_out` 返回 dict 增加 `kind_reason`、`script` 两键(前端可读)。

- [ ] **Step 1: 补序列化字段**

在 `backend/app/api/ai.py` 的 `_to_case_out` 返回 dict 里,`"exec_kind"` 那行之后加:

```python
        "kind_reason": getattr(tc, "kind_reason", None),
        "script": getattr(tc, "script", None),
```

(用 `getattr` 兜底:老库/未 migrate 时不报 AttributeError,沿用同函数 exec_kind 的写法。)

- [ ] **Step 2: 验证 import 冒烟**

Run: `cd backend && PYTHONPATH="$(pwd)" .venv/bin/python -c "import app.api.ai; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 提交**

```bash
git add backend/app/api/ai.py
git commit -m "feat(ai): 用例序列化输出 kind_reason/script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: enqueue 拒发 manual 用例

**Files:**
- Modify: `backend/app/api/exec_queue.py`(`enqueue` 循环内,`_kind_of(tc)` 使用处)

**Interfaces:**
- Consumes: `ExecKind.manual`(Task 1)、`_kind_of`(现有)。
- Produces: 入队含 manual 用例时整批 400 拒绝。

- [ ] **Step 1: 加校验**

在 `backend/app/api/exec_queue.py` 的 `enqueue` 里,`for cid in ids:` 循环内、`tc = db.get(TestCase, it.test_case_id)` 之后、构造 `ExecRun(...)` 之前,加:

```python
        if _kind_of(tc) == ExecKind.manual:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"清单项 {cid} 对应用例为『人工/不可自动化(manual)』,不能下发到执行机",
            )
```

(`ExecKind` 已在该文件 `from app.core.enums import ... ExecKind` 导入;确认无需补 import。)

- [ ] **Step 2: 验证 import 冒烟 + 逻辑自测**

Run:
```bash
cd backend && PYTHONPATH="$(pwd)" .venv/bin/python -c "
import app.api.exec_queue as q
from app.core.enums import ExecKind
class TC:  # 模拟 exec_kind=manual 的用例
    exec_kind='manual'
print('manual ->', q._kind_of(TC()) == ExecKind.manual)   # 应 True
class TC2: exec_kind='gui'
print('gui ->', q._kind_of(TC2()) == ExecKind.manual)      # 应 False
"
```
Expected: `manual -> True` 与 `gui -> False`。

- [ ] **Step 3: 提交**

```bash
git add backend/app/api/exec_queue.py
git commit -m "feat(exec-queue): 拒绝下发 manual(不可自动化)用例

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: runner 按 kind 分工具集 + gui 兜底护栏

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`(SYSTEM_PROMPT 常量;`runClaude` 的 args;`tick` 里 `runClaude(item.payload)` 调用处传 kind)

**Interfaces:**
- Consumes: `item.kind`(payload 已含,见 exec_queue `_to_out`)。
- Produces: `runClaude(payload, kind)` 按 kind 选 `--allowedTools`;SYSTEM_PROMPT 含"只测被测产品、禁止研究本地仓库、读不出步骤直接 fail"护栏。

- [ ] **Step 1: 强化 SYSTEM_PROMPT 护栏**

在 `tools/qalab-runner/runner.mjs` 的 `SYSTEM_PROMPT` 模板里,「执行规则」段末尾(`再次强调` 那行之前)加三条:

```
- **只测被测客户端/接口本身,不研究"功能如何实现"**:禁止用 Bash/git/grep/ls/Read 去翻本地代码仓库或平台源码;
  你的工作目录可能是某个代码仓库,忽略它,绝不 cd/读它。
- 读不出可自动化执行的步骤(如用例是功能描述/需求而非"点哪、断言啥")→ **立即 verdict=fail**,
  reason 写"用例不可自动化执行:<原因>";**不要反问、不要研究代码、不要空等**。
- GUI/E2E 用例只用 mcp__gui__*;api 用例用 Bash 跑 curl/fetch;cli 用例用 Bash 起进程。别越界用其它工具。
```

- [ ] **Step 2: runClaude 按 kind 选工具**

改 `runClaude` 签名与 `--allowedTools`:把 `function runClaude(payload) {` 改为 `function runClaude(payload, kind) {`,并把 args 里那行

```js
      "--allowedTools", "Bash mcp__gui__*",
```

替换为(在 `const args = [` 之前先算好白名单):

```js
    // 按 kind 给最小工具集:gui/e2e 只给 gui-mcp(不给 Bash,杜绝跑去翻代码/执行命令);api/cli 给 Bash。
    const allowed = (kind === "api" || kind === "cli") ? "Bash" : "mcp__gui__*";
```

再把 args 里对应行改为:

```js
      "--allowedTools", allowed,
```

- [ ] **Step 3: tick 传入 kind**

在 `tick()` 里,把 `result = await runClaude(item.payload);` 改为:

```js
        result = await runClaude(item.payload, item.kind);
```

- [ ] **Step 4: node 语法校验**

Run: `cd tools/qalab-runner && node --check runner.mjs`
Expected: 无输出(退出码 0)。

- [ ] **Step 5: 手动端到端验证(gui 用例仍能跑 + 不再碰 Bash)**

用本地临时后端 + seed 一条 gui 用例(payload 含 `kind:"gui"`)跑 runner,观察实时进度日志里:
- claude 就绪 `MCP=["gui"]`;
- 工具调用只有 `mcp__gui__*`,**没有** Bash/Read;
- 回写 pass/fail 正常。

(seed 方法见本仓库既往 e2e:临时 sqlite + `ExecRun(runner="mac-01", kind="gui", status="pending", payload=...)`,BASE_URL/RUNNER_TOKEN 用环境变量指向临时后端。Namiwork 需带 `--remote-debugging-port=9222`。)
Expected: gui 用例执行过程无 Bash 调用,pass/fail 正常回写。

- [ ] **Step 6: 提交**

```bash
git add tools/qalab-runner/runner.mjs
git commit -m "feat(runner): 按 kind 分工具集(gui 不给 Bash)+ 禁研究代码/读不出步骤即 fail 护栏

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 前端下拉加 e2e/manual + manual 禁下发

**Files:**
- Modify: `frontend/src/views/CaseLibrary.vue:87-88`(`EXEC_KINDS`)
- Modify: `frontend/src/views/Tasks.vue`(下发勾选/发送处)

**Interfaces:**
- Consumes: 后端接受 `exec_kind ∈ {gui,api,cli,e2e,manual}`(Task 1)、enqueue 拒 manual(Task 4)。

- [ ] **Step 1: CaseLibrary 下拉补选项**

在 `frontend/src/views/CaseLibrary.vue` 的 `EXEC_KINDS` 数组补两项:

```js
  { value: 'gui', label: 'GUI' },
  { value: 'api', label: 'API' },
  { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' },
  { value: 'manual', label: '人工' },
```

(以现有 gui/api/cli 三项为准补齐 e2e/manual;若现有只列了部分,保持已有再加缺的两项。)

- [ ] **Step 2: Tasks 下发处对 manual 提示/禁选**

在 `frontend/src/views/Tasks.vue` 的下发方法(勾选清单项→`enqueueExec` 之前)加一道前端拦截:若选中项里有 `exec_kind==='manual'` 的用例,`ElMessage.warning('含人工(manual)用例,不能下发到执行机;请取消勾选后重试')` 并 `return`。

具体:找到发送函数(调用 `enqueueExec(...)` 处),在收集 `items` 之后、调用前加:

```js
    const manualItem = items.find((it) => (it.exec_kind || 'gui') === 'manual')
    if (manualItem) { ElMessage.warning('含人工(manual)用例,不能下发到执行机;请取消勾选后重试'); return }
```

(若清单项对象上没有 `exec_kind` 字段,则跳过此前端拦截——后端 Task 4 已兜底 400;在本步的验证里确认字段是否存在,没有就仅依赖后端拦截并在 PR 说明中标注。)

- [ ] **Step 3: 构建校验**

Run: `cd frontend && npm run build`
Expected: 构建成功,无语法错误。

- [ ] **Step 4: 手动验证**

`npm run dev` 后:用例库能把某用例执行类型设为 E2E / 人工;把一条设为「人工」的用例在任务清单勾选下发 → 前端提示不能下发(或后端返回 400 被拦截器弹出)。
Expected: manual 用例无法下发,提示清晰。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/CaseLibrary.vue frontend/src/views/Tasks.vue
git commit -m "feat(frontend): 用例执行类型加 E2E/人工;人工用例禁止下发

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 自审记录

- **Spec 覆盖**:P1 对应设计稿 §8 的 P1 行(manual/e2e 枚举、migrate 补列、enqueue 拒 manual、runner 按 kind 分工具 + 护栏)。§7.1 生成层、§5 DSL 执行器属 P2/P3,本计划不含(script 列先建空,Task 2)。
- **占位扫描**:无 TBD/TODO;每步给了确切代码/命令。Task 6 Step 2 的"字段可能不存在"给了明确回退(依赖后端 400)。
- **类型一致**:`ExecKind.e2e/manual`(Task1)贯穿 Task4(enqueue)/Task6(前端);`script`/`kind_reason`(Task2)在 Task3 序列化一致;`runClaude(payload, kind)` 签名 Task5 内自洽。
- **无测试框架**:各 Task 用 import 冒烟 / `node --check` / `npm run build` / 手动 e2e 代替单测,符合 CLAUDE.md。
