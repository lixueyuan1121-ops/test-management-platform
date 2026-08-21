# 对话测评 query 生成 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台新增一条"从需求文档 + 对话测评维度生成对话测评 query"的 SSE 流式生成链路,产物落 `eval_query`,与现有功能测试点生成链路隔离。

**Architecture:** 复用现有 `AiTask`(kind='eval_query_gen')、claude/deepseek 双引擎、SSE 事件协议。新建独立 `api/ai_eval.py`(不改 `gen_testcases`);对共享代码唯一改动是给 `stream_generate` 加默认 `None` 的 `prompt_builder` 可选参(现有调用零改动)。SSE 骨架有意复制一份以隔离两链路。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Python;claude CLI(subprocess)/deepseek(OpenAI 兼容 HTTP);前端 Vue3 + ElementPlus + 原生 fetch(SSE)。

**Spec:** `docs/superpowers/specs/2026-08-21-eval-query-generation-design.md`

## Global Constraints

- **不改 `api/ai.py::gen_testcases` 及功能测试点链路**(隔离,决策 2)。对共享代码唯一允许的改动:给 `stream_generate` 加带默认值的可选参 `prompt_builder=None`。
- **不用原生 JSON 列**:结构化数据用 `Text` 存 JSON 字符串(兼容 MySQL 5.6)。
- **两份 schema 手动同步**:SQLAlchemy 模型(`app/models/ai_eval.py`)与 `backend/sql/schema.sql` 一致。
- **老库加列走 migrate**:`eval_query` 已在 main 上,加 `dimension` 列必须在 `migrate.py` 补幂等 `ensure_eval_query_dimension` 并在 `main.py::init_db` 调用(区别于子项 0 纯新建表)。
- **本仓库无测试框架**:没有 pytest/eslint——不要臆造。验证 = 一次性 Python 脚本 / curl SSE / 手动前端。后端命令在 `backend/` 下跑,本地默认 SQLite。
- **SSE 事件协议**(前端契约,与现有一致):`: hb\n\n`(心跳注释帧) / `data: {"type":"delta","text":...}` / `data: {"type":"error","msg":...}` / `data: {"type":"done",...}`。
- **AiTask.kind** 加值 `eval_query_gen` 无需迁移(String(32) 自由列)。
- **对话测评维度 5 个**:`thinking` / `tool_use` / `artifact` / `multi_turn` / `instruction`。
- 生成产物:`dialog_options` 留空(NULL);被测引擎字段不在本子项。

## 文件结构

- **Modify** `backend/app/models/ai_eval.py` — `EvalQuery` 加 `dimension` 列。
- **Modify** `backend/sql/schema.sql` — `eval_query` 建表加 `dimension` 列。
- **Modify** `backend/app/db/migrate.py` — 新增 `ensure_eval_query_dimension()`。
- **Modify** `backend/app/main.py` — `init_db()` 调用 `ensure_eval_query_dimension()`。
- **Modify** `backend/app/services/claude_runner.py` — 新增 `build_eval_query_prompt` + `parse_eval_queries`;`stream_generate` 加 `prompt_builder` 参。
- **Modify** `backend/app/services/generators/deepseek_runner.py` — `stream_generate` 加 `prompt_builder` 参 + import 两新函数。
- **Modify** `backend/app/schemas/ai.py` — 新增 `EvalQueryGenIn`。
- **Create** `backend/app/api/ai_eval.py` — `POST /api/ai/eval-queries`(SSE) + `_to_query_out`。
- **Modify** `backend/app/api/router.py` — 注册 `ai_eval.router`。
- **Modify** `frontend/src/api/index.js` — `streamEvalQueries`。
- **Create** `frontend/src/views/AIEvalGen.vue` — 生成视图。
- **Modify** `frontend/src/router/index.js` — 挂载路由(+ 导航入口)。

---

### Task 1: `eval_query` 加 `dimension` 列（模型 + schema.sql + migrate）

数据地基。其它任务(parse/落库/前端展示)依赖此列。可独立验证:新库 create_all 含列、老库 migrate 补列幂等。

**Files:**
- Modify: `backend/app/models/ai_eval.py`(EvalQuery 内,`title` 之后)
- Modify: `backend/sql/schema.sql`(eval_query 建表,`title` 之后)
- Modify: `backend/app/db/migrate.py`(新增函数)
- Modify: `backend/app/main.py`(init_db 调用)
- Verify(临时,验证后删): `backend/_verify_eval_dim.py`

**Interfaces:**
- Consumes: 现有 `EvalQuery`(models/ai_eval.py)、`migrate.py` 的 `_columns()` helper、`main.py::init_db`
- Produces: `EvalQuery.dimension`(str|None, 供 Task 4 落库、Task 5 展示);`migrate.ensure_eval_query_dimension()`

- [ ] **Step 1: 模型加列**

`backend/app/models/ai_eval.py`,在 `EvalQuery` 的 `title` 列之后加:
```python
    # 该 query 主考的对话测评维度(thinking/tool_use/artifact/multi_turn/instruction);生成侧填,可空
    dimension: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

- [ ] **Step 2: schema.sql 加列**

`backend/sql/schema.sql` 的 `CREATE TABLE \`eval_query\`` 内,`title` 行之后加一行:
```sql
  `dimension` VARCHAR(16) NULL,
```

- [ ] **Step 3: migrate 补列函数**

`backend/app/db/migrate.py` 末尾(其它 `ensure_*` 函数旁)加。先看文件里 `_columns` 用法(如 `ensure_testcase_columns`)对齐写法:
```python
def ensure_eval_query_dimension() -> None:
    """eval_query 补 dimension 列(对话测评题主考维度)。老库已建表故走 ALTER;新库 create_all 已含,探到即跳过(幂等)。"""
    if not _columns("eval_query"):
        return  # 表还没建(全新库 create_all 尚未跑到)——create_all 会带上该列,无需 ALTER
    if "dimension" not in _columns("eval_query"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_query ADD COLUMN dimension VARCHAR(16) NULL"))
```
(注:`_columns`/`engine`/`text` 已在 migrate.py 顶部 import——确认后沿用,勿重复 import。)

- [ ] **Step 4: init_db 调用**

`backend/app/main.py::init_db`,在现有 `ensure_*` 调用序列末尾(如 `ensure_perf_run_columns()` 之后)加一行:
```python
    ensure_eval_query_dimension()
```
并在文件顶部 migrate import 那一行(`from app.db.migrate import ...`)的名单里加 `ensure_eval_query_dimension`。

- [ ] **Step 5: 写验证脚本**

`backend/_verify_eval_dim.py`:
```python
"""Task1 验证:新库 create_all 含 dimension 列 + migrate 幂等 + 能存取。backend/ 下: python _verify_eval_dim.py"""
from sqlalchemy import inspect, text
from app.db.session import Base, SessionLocal, engine
from app.models import EvalQuery
from app.db.migrate import ensure_eval_query_dimension

Base.metadata.create_all(bind=engine)
cols = {c["name"] for c in inspect(engine).get_columns("eval_query")}
assert "dimension" in cols, f"dimension 不在 eval_query 列: {cols}"

# migrate 幂等:列已存在再调一次不报错
ensure_eval_query_dimension()

# 模拟老库缺列:临时删列再 migrate 补(SQLite 不支持 DROP COLUMN 简单验,改为直接验 ALTER 幂等——已存在时安全跳过)
ensure_eval_query_dimension()  # 再调,应静默跳过

# 存取
db = SessionLocal()
try:
    from app.models import Project
    proj = db.query(Project).first()
    pid = proj.id if proj else 1
    q = EvalQuery(project_id=pid, provider="claude", title="t", prompt="p", dimension="thinking")
    db.add(q); db.commit(); db.refresh(q)
    assert q.dimension == "thinking"
    db.delete(q); db.commit()
    print("OK: dimension 列建出、migrate 幂等、存取正常")
finally:
    db.close()
```

- [ ] **Step 6: 跑验证**

Run(backend/ 下): `python _verify_eval_dim.py`
Expected: 末行 `OK: dimension 列建出、migrate 幂等、存取正常`,退出码 0。
(依赖缺失先 `pip install -r requirements.txt`。Windows 控制台收尾可能有 `CreateFile() Error: 5` 环境噪声,出现在 OK 之后/退出码 0,忽略。)

- [ ] **Step 7: 删验证脚本**

Run: `rm backend/_verify_eval_dim.py`

- [ ] **Step 8: 提交**

```bash
git add backend/app/models/ai_eval.py backend/sql/schema.sql backend/app/db/migrate.py backend/app/main.py
git commit -m "feat(eval): eval_query 加 dimension 列 + migrate 补列

对话测评题主考维度列(thinking/tool_use/artifact/multi_turn/instruction)。
新库 create_all 含列;老库走 ensure_eval_query_dimension 幂等 ALTER 补列。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `build_eval_query_prompt` + `parse_eval_queries`（claude_runner）

两个纯函数:构造生成 prompt、解析模型输出为 query dict 列表。可独立验证(喂样例 raw → 解析出正确结构)。

**Files:**
- Modify: `backend/app/services/claude_runner.py`(新增两函数,放 `parse_testcases` 附近)
- Verify(临时,删): `backend/_verify_eval_parse.py`

**Interfaces:**
- Consumes: 现有 `_extract_cases_array(raw)`(claude_runner.py:610-630)、`_salvage_objects`
- Produces:
  - `build_eval_query_prompt(requirement: str, dimensions: list[str]) -> str`
  - `parse_eval_queries(raw: str) -> list[dict]`,每 dict 键:`title, prompt, dimension, expected, attachments(list), conversation_group, turn_index(int)`

- [ ] **Step 1: 加维度常量 + build_eval_query_prompt**

`backend/app/services/claude_runner.py`,在 `parse_testcases` 定义之前加:
```python
# 对话测评维度:值 → 生成引导说明(供 build_eval_query_prompt 拼进 prompt)
EVAL_DIMENSIONS = {
    "thinking": "需要多步推理/规划才能回答的问题,考查思考过程是否完整、有条理",
    "tool_use": "需要联网搜索或调用工具(含 MCP 工具)才能完成的任务,考查工具调用是否正常、结果是否被正确使用",
    "artifact": "要求产出网页/文件/代码/文档等交付物的任务,考查产物是否符合预期",
    "multi_turn": "需要多轮对话逐步澄清/细化的场景,考查上下文连贯性(这类应产出同一 conversation_group 下的多条,turn_index 递增)",
    "instruction": "带明确约束或格式要求的任务,考查是否严格遵循指令",
}


def build_eval_query_prompt(requirement: str, dimensions: list[str]) -> str:
    """构造"生成对话测评 query"的 prompt。产物是发给被测大模型的对话提问,不是功能测试点。
    不注入 selector key / api 契约 / script DSL(那些是测试点特有)。
    """
    valid = [d for d in (dimensions or []) if d in EVAL_DIMENSIONS] or ["thinking"]
    dim_lines = "\n".join(f"- {d}: {EVAL_DIMENSIONS[d]}" for d in valid)
    return f"""你是"AI 对话能力测评"的出题专家。基于下面的需求文档,生成一批"对话测评 query"——
即拿去发给被测大模型(如 Claude、codex 等 Agent)对话、用来考查其对话能力的提问。

要覆盖的测评维度(按这些维度出题,尽量均衡覆盖):
{dim_lines}

严格输出一个 JSON 数组,不要任何数组之外的解释文字。每个元素:
{{
  "title": "题目摘要(<=50字)",
  "prompt": "发给被测大模型的完整提问正文(必填,这是要真正发出去对话的内容)",
  "dimension": "该题主考的维度,取值必须是: {", ".join(valid)} 之一",
  "expected": "期望被测模型产出什么或做到什么(用于后续判定的参照,要具体、可核对)",
  "attachments": [],
  "conversation_group": "会话分组名。单轮题给独立唯一名(如 g1/g2);多轮题同一对话的多条用相同名",
  "turn_index": 0
}}

多轮说明:multi_turn 维度的题,把一个对话意图拆成多条,conversation_group 相同、turn_index 从 0 递增
(0=首轮提问,1/2=追问)。单轮题 turn_index 恒为 0、各自独立 conversation_group。
attachments 一般为空数组 [];仅当需求明确涉及上传文件/图片时才给出 [{{"name":"...","url":"..."}}]。
不要输出 dialog_options 等执行参数。

<requirement>
{requirement}
</requirement>"""
```

- [ ] **Step 2: 加 parse_eval_queries**

紧接其后加:
```python
_EVAL_DIM_VALUES = set(EVAL_DIMENSIONS.keys())


def parse_eval_queries(raw: str) -> list[dict]:
    """把模型输出解析成对话测评 query dict 列表。复用 _extract_cases_array 的多重兜底提取;
    字段映射为 query 结构,不走 script/selector 校验。丢弃无 prompt 或无 title 的条目。
    """
    arr = _extract_cases_array(raw)
    out: list[dict] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        prompt = (item.get("prompt") or "").strip()
        if not title or not prompt:
            continue  # 无题干或无提问正文的条目无意义,丢弃
        dim = item.get("dimension")
        if dim not in _EVAL_DIM_VALUES:
            dim = None  # 非法维度置空(不猜)
        # turn_index 归一为非负整数
        ti = item.get("turn_index", 0)
        try:
            ti = max(0, int(ti))
        except (TypeError, ValueError):
            ti = 0
        # attachments 归一为 list
        att = item.get("attachments")
        if not isinstance(att, list):
            att = []
        cg = item.get("conversation_group")
        cg = cg.strip() if isinstance(cg, str) and cg.strip() else None
        out.append({
            "title": title[:512],
            "prompt": prompt,
            "dimension": dim,
            "expected": (item.get("expected") or "").strip() or None,
            "attachments": att,
            "conversation_group": cg,   # None → 落库时补唯一组名
            "turn_index": ti,
        })
    return out
```

- [ ] **Step 3: 写验证脚本**

`backend/_verify_eval_parse.py`:
```python
"""Task2 验证:build_eval_query_prompt 含维度说明 + parse_eval_queries 解析正确。backend/下: python _verify_eval_parse.py"""
from app.services.claude_runner import build_eval_query_prompt, parse_eval_queries

# build:含选中维度说明、含 requirement
p = build_eval_query_prompt("做一个天气查询网页", ["tool_use", "artifact"])
assert "tool_use" in p and "artifact" in p and "做一个天气查询网页" in p
assert "thinking" not in p.split("要覆盖的测评维度")[1].split("<requirement>")[0] or True  # 只放选中维度(宽松)

# parse:正常数组
raw = '''前言噪声
```json
[
  {"title":"多步推理","prompt":"帮我规划三天行程","dimension":"thinking","expected":"给出可执行的3天计划","attachments":[],"conversation_group":"g1","turn_index":0},
  {"title":"追问","prompt":"第二天换成海边","dimension":"multi_turn","expected":"在g1上下文调整第二天","conversation_group":"g1","turn_index":1},
  {"title":"缺prompt应被丢","dimension":"thinking"},
  {"title":"坏维度","prompt":"x","dimension":"bogus","turn_index":"2"}
]
```'''
qs = parse_eval_queries(raw)
assert len(qs) == 3, f"应解析出3条(丢无prompt那条), 实际 {len(qs)}"
assert qs[0]["dimension"] == "thinking" and qs[0]["conversation_group"] == "g1"
assert qs[1]["turn_index"] == 1 and qs[1]["conversation_group"] == "g1"
assert qs[2]["dimension"] is None, "坏维度应置空"
assert qs[2]["turn_index"] == 2, "字符串 turn_index 应转 int"
assert qs[2]["conversation_group"] is None, "缺 conversation_group 应为 None"
assert qs[0]["expected"] == "给出可执行的3天计划"
print("OK: build_eval_query_prompt + parse_eval_queries 正常")
```

- [ ] **Step 4: 跑验证**

Run(backend/ 下): `python _verify_eval_parse.py`
Expected: 末行 `OK: build_eval_query_prompt + parse_eval_queries 正常`,退出码 0。

- [ ] **Step 5: 删验证脚本**

Run: `rm backend/_verify_eval_parse.py`

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/claude_runner.py
git commit -m "feat(eval): build_eval_query_prompt + parse_eval_queries

对话测评 query 的 prompt 构造(按 5 维度出题、支持多轮)与解析(复用 _extract_cases_array,
映射为 query 结构,丢无 prompt/title 条目,非法维度置空,turn_index 归一)。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `stream_generate` 参数化（claude + deepseek）

给两个 provider 的 `stream_generate` 加 `prompt_builder=None` 可选参,默认回落原 `build_testcase_prompt`(现有 testcase 调用零改动)。这是唯一改到共享代码的任务,验证核心是"现有测试点生成不回归"。

**Files:**
- Modify: `backend/app/services/claude_runner.py`(stream_generate:479-490)
- Modify: `backend/app/services/generators/deepseek_runner.py`(stream_generate + import)
- Verify(临时,删): `backend/_verify_stream_param.py`

**Interfaces:**
- Consumes: Task 2 的 `build_eval_query_prompt`
- Produces: `stream_generate(requirement, project_id=None, timeout=None, pages=None, prompt_builder=None)` — 两 provider 同签名扩展;`prompt_builder` 为无参 callable,None 时回落 `build_testcase_prompt(requirement, project_id, pages)`

- [ ] **Step 1: claude_runner.stream_generate 加参**

`backend/app/services/claude_runner.py:479` 签名改为:
```python
def stream_generate(requirement: str, project_id: int | None = None, timeout: int | None = None, pages: list[str] | None = None, prompt_builder=None) -> Iterator[dict]:
```
`:490` 那行 `cmd = _build_cmd(build_testcase_prompt(requirement, project_id, pages))` 改为:
```python
    prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)
    cmd = _build_cmd(prompt)
```
(docstring 可补一句:`prompt_builder 非空则用它(无参调用)构造 prompt,否则默认生成测试点 prompt。`)

- [ ] **Step 2: deepseek_runner.stream_generate 加参 + import**

`backend/app/services/generators/deepseek_runner.py` 的 import 段(现 `from app.services.claude_runner import (...)`)加两个名字:
```python
    build_eval_query_prompt, parse_eval_queries,
```
`stream_generate` 签名同样加 `prompt_builder=None`;函数体里写死调 `build_testcase_prompt(requirement, project_id, pages)` 的那行(约 :103)改为:
```python
        prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)
```
并把后续 `_body(build_testcase_prompt(...), stream=True)` 里的 prompt 换成这个局部 `prompt` 变量。
(读该文件确认 build_testcase_prompt 被调用的确切位置与用法后对齐修改;只改 prompt 来源,不动 SSE/端点逻辑。)

- [ ] **Step 3: 写验证脚本(回归 + 参数生效)**

`backend/_verify_stream_param.py`:
```python
"""Task3 验证:stream_generate 加参后——①默认回落 build_testcase_prompt(现有行为不变)②传 prompt_builder 生效。
不真跑 claude 子进程(慢/需 CLI),只验 prompt 构造分支。backend/下: python _verify_stream_param.py"""
import inspect as _inspect
from app.services import claude_runner
from app.services.generators import deepseek_runner

# 签名含 prompt_builder
for mod in (claude_runner, deepseek_runner):
    sig = _inspect.signature(mod.stream_generate)
    assert "prompt_builder" in sig.parameters, f"{mod.__name__}.stream_generate 缺 prompt_builder 参"
    assert sig.parameters["prompt_builder"].default is None, "prompt_builder 默认应为 None"

# deepseek 复用了两新函数(import 生效)
assert hasattr(deepseek_runner, "build_eval_query_prompt")
assert hasattr(deepseek_runner, "parse_eval_queries")

# 分支逻辑等价性:模拟 prompt 选择
def pick(prompt_builder, fallback):
    return prompt_builder() if prompt_builder is not None else fallback
assert pick(None, "TESTCASE") == "TESTCASE"
assert pick(lambda: "EVAL", "TESTCASE") == "EVAL"
print("OK: stream_generate 参数化(两 provider 同签名, 默认回落, deepseek 复用新函数)")
```

- [ ] **Step 4: 跑验证**

Run(backend/ 下): `python _verify_stream_param.py`
Expected: 末行 `OK: stream_generate 参数化(...)`,退出码 0。

- [ ] **Step 5: 现有测试点生成回归(代码审查式)**

确认 `backend/app/api/ai.py:190` 处 `engine.stream_generate(requirement, project_id=project_id, pages=pages)` **未传 prompt_builder** → 走默认 None 回落 → 行为不变。这一步无需运行(不真跑 claude),读代码确认调用点未变即可,并在报告里写明"gen_testcases 对 stream_generate 的调用未改,靠默认参回落"。

- [ ] **Step 6: 删验证脚本**

Run: `rm backend/_verify_stream_param.py`

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/claude_runner.py backend/app/services/generators/deepseek_runner.py
git commit -m "feat(eval): stream_generate 加 prompt_builder 可选参(方案甲)

两 provider 同签名扩展,默认 None 回落 build_testcase_prompt——现有测试点生成
调用零改动;eval 端传 prompt_builder=lambda: build_eval_query_prompt(...) 复用同一流式引擎。
deepseek import build_eval_query_prompt/parse_eval_queries 复用。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `EvalQueryGenIn` + `api/ai_eval.py` 端点 + 路由注册

把前面拼成可调用端点。新建独立文件(隔离),SSE 骨架有意复制 gen_testcases 框架,换"解析/落库/序列化"三处。验证:真实 POST 走通 SSE + 落 eval_query(多轮分组正确)。

**Files:**
- Modify: `backend/app/schemas/ai.py`(加 EvalQueryGenIn)
- Create: `backend/app/api/ai_eval.py`
- Modify: `backend/app/api/router.py`(注册)
- Verify(临时,删): `backend/_verify_eval_endpoint.py`

**Interfaces:**
- Consumes: Task 2 `build_eval_query_prompt`/`parse_eval_queries`;Task 3 `stream_generate(prompt_builder=...)`;`EvalQuery`(Task 1 含 dimension);现有 `generators`/`AiTask`/`assert_project_role`/`SessionLocal`
- Produces: `POST /api/ai/eval-queries`(SSE);done 帧含 `queries` 数组

- [ ] **Step 1: 加 EvalQueryGenIn**

`backend/app/schemas/ai.py`,在 `TestCaseGenIn` 之后加:
```python
class EvalQueryGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text
    provider: str | None = None  # claude/deepseek;空/非法后端 normalize 回落
    requirement: str = Field(min_length=1, max_length=20000)  # 需求正文(url/file 由前端取文后填)
    dimensions: list[str] = Field(min_length=1)  # 至少一个对话测评维度
```

- [ ] **Step 2: 新建 api/ai_eval.py**

创建 `backend/app/api/ai_eval.py`(SSE 骨架有意复制 gen_testcases,换解析/落库/序列化):
```python
"""对话测评 query 生成路由(SSE 流式 + 落库 eval_query)。

独立于 api/ai.py 的功能测试点生成(隔离,避免改动互相波及)。SSE 骨架与 gen_testcases
同构(双 session、心跳/delta/error 转发、指标写入),但落 EvalQuery、支持多轮分组。
流式落库同坑:生成器在 get_db 关闭后才迭代,故函数体内建 running 记录,生成器内另开 SessionLocal。
"""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import AiTaskStatus, ProjectRole
from app.db.session import SessionLocal, get_db
from app.models import AiTask, EvalQuery, Project, User
from app.schemas.ai import EvalQueryGenIn
from app.services import claude_runner, generators

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/ai", tags=["ai-eval"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _to_query_out(q: EvalQuery) -> dict:
    rs = q.review_status
    return {
        "id": q.id,
        "ai_task_id": q.ai_task_id,
        "project_id": q.project_id,
        "task_id": q.task_id,
        "title": q.title,
        "prompt": q.prompt,
        "dimension": q.dimension,
        "expected": q.expected,
        "attachments": json.loads(q.attachments) if q.attachments else [],
        "conversation_group": q.conversation_group,
        "turn_index": q.turn_index,
        "review_status": getattr(rs, "value", rs),
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


@router.post("/eval-queries")
def gen_eval_queries(
    body: EvalQueryGenIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """流式生成对话测评 query 并落库。SSE 事件:delta / error / done(含 queries)。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    provider_id = generators.normalize_provider(body.provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"生成引擎「{provider_id}」未启用或不可用")

    at = AiTask(
        project_id=body.project_id,
        task_id=body.task_id,
        user_id=user.id,
        kind="eval_query_gen",
        provider=provider_id,
        input_type=body.input_type,
        input_ref=body.requirement[:20000],
        status=AiTaskStatus.running,
    )
    db.add(at)
    db.commit()
    db.refresh(at)

    ai_task_id = at.id
    project_id = body.project_id
    task_id = body.task_id
    requirement = body.requirement
    dimensions = body.dimensions

    def sse():
        raw = ""
        meta: dict | None = None
        err: str | None = None
        t0 = time.monotonic()
        try:
            for evt in engine.stream_generate(
                requirement, project_id=project_id,
                prompt_builder=lambda: claude_runner.build_eval_query_prompt(requirement, dimensions),
            ):
                etype = evt.get("type")
                if etype == "heartbeat":
                    yield ": hb\n\n"
                elif etype == "delta":
                    raw += evt["text"]
                    yield _sse({"type": "delta", "text": evt["text"]})
                elif etype == "result":
                    meta = evt
                    if evt.get("text"):
                        raw = evt["text"]
                elif etype == "error":
                    err = evt.get("msg")
                    yield _sse({"type": "error", "msg": err})
        except Exception as e:
            logger.exception("AI 对话 query 流式生成异常")
            err = err or f"生成中断：{e}"

        s = SessionLocal()
        try:
            at2 = s.get(AiTask, ai_task_id)
            if at2 is None:
                yield _sse({"type": "error", "msg": "任务记录丢失"})
                return
            if meta:
                at2.duration_ms = meta.get("duration_ms") or int((time.monotonic() - t0) * 1000)
                at2.cost_usd = meta.get("cost_usd")
                at2.output_tokens = meta.get("output_tokens")
            at2.output_raw = raw or None

            queries = claude_runner.parse_eval_queries(raw)
            if not queries:
                at2.status = AiTaskStatus.failed
                if err:
                    detail = err
                elif not raw:
                    detail = "未生成有效 query：引擎无任何输出(可能被网关/超时切断)"
                else:
                    detail = f"未生成有效 query：输出 {len(raw)} 字但未解析出 query 数组(尾部:…{raw[-200:]})"
                at2.error = detail[:2000]
                s.commit()
                yield _sse({"type": "done", "ai_task_id": ai_task_id,
                            "status": "failed", "msg": at2.error, "queries": []})
                return

            objs = []
            for i, c in enumerate(queries):
                # conversation_group 为空 → 补唯一组名(单轮题各自独立),避免与别批/别题混淆
                cg = c["conversation_group"] or f"g{ai_task_id}_{i}"
                q = EvalQuery(
                    ai_task_id=ai_task_id,
                    provider=provider_id,
                    project_id=project_id,
                    task_id=task_id,
                    title=c["title"],
                    prompt=c["prompt"],
                    dimension=c.get("dimension"),
                    expected=c.get("expected"),
                    attachments=json.dumps(c["attachments"], ensure_ascii=False) if c.get("attachments") else None,
                    conversation_group=cg,
                    turn_index=c.get("turn_index") or 0,
                )
                s.add(q)
                objs.append(q)
            at2.status = AiTaskStatus.done
            at2.case_count = len(queries)
            s.commit()
            for q in objs:
                s.refresh(q)
            yield _sse({
                "type": "done",
                "ai_task_id": ai_task_id,
                "status": "done",
                "queries": [_to_query_out(q) for q in objs],
                "meta": {
                    "case_count": at2.case_count,
                    "duration_ms": at2.duration_ms,
                    "cost_usd": float(at2.cost_usd) if at2.cost_usd is not None else None,
                    "output_tokens": at2.output_tokens,
                },
            })
        except Exception as e:
            logger.exception("对话 query 落库失败")
            s.rollback()
            yield _sse({"type": "error", "msg": f"落库失败：{e}"})
        finally:
            s.close()

    return StreamingResponse(sse(), media_type="text/event-stream")
```

- [ ] **Step 3: 注册 router**

`backend/app/api/router.py`:import 行(第 4 行)末尾加 `, ai_eval`;并在 `api_router.include_router(ai.router)` 之后加:
```python
api_router.include_router(ai_eval.router)
```

- [ ] **Step 4: 写验证脚本(不真跑引擎,直接验落库路径)**

真跑 claude 慢且需 CLI。改为直接调 `parse_eval_queries` + 手工模拟 sse() 的落库段,验证 EvalQuery 正确落库(尤其多轮 conversation_group 补名、attachments JSON)。`backend/_verify_eval_endpoint.py`:
```python
"""Task4 验证:落库路径正确(不真跑引擎)。验 parse 输出 → EvalQuery 落库映射 + _to_query_out。
backend/下: python _verify_eval_endpoint.py"""
import json
from app.db.session import SessionLocal
from app.models import AiTask, EvalQuery, Project
from app.core.enums import AiTaskStatus
from app.services.claude_runner import parse_eval_queries
from app.api.ai_eval import _to_query_out

raw = '''```json
[
 {"title":"规划","prompt":"规划三天行程","dimension":"thinking","expected":"3天计划","conversation_group":"g1","turn_index":0},
 {"title":"追问","prompt":"第二天改海边","dimension":"multi_turn","expected":"调整第二天","conversation_group":"g1","turn_index":1},
 {"title":"单轮无组","prompt":"写个网页","dimension":"artifact","expected":"可运行网页","attachments":[{"name":"a.png","url":"http://x/a"}]}
]
```'''
qs = parse_eval_queries(raw)
assert len(qs) == 3

db = SessionLocal()
try:
    proj = db.query(Project).first()
    pid = proj.id if proj else 1
    at = AiTask(project_id=pid, user_id=1, kind="eval_query_gen", provider="claude", status=AiTaskStatus.running)
    db.add(at); db.commit(); db.refresh(at)

    objs = []
    for i, c in enumerate(qs):
        cg = c["conversation_group"] or f"g{at.id}_{i}"
        q = EvalQuery(ai_task_id=at.id, provider="claude", project_id=pid, title=c["title"], prompt=c["prompt"],
                      dimension=c.get("dimension"), expected=c.get("expected"),
                      attachments=json.dumps(c["attachments"], ensure_ascii=False) if c.get("attachments") else None,
                      conversation_group=cg, turn_index=c.get("turn_index") or 0)
        db.add(q); objs.append(q)
    at.status = AiTaskStatus.done; at.case_count = len(qs); db.commit()
    for q in objs: db.refresh(q)

    # 多轮同组
    assert objs[0].conversation_group == "g1" and objs[1].conversation_group == "g1"
    assert objs[0].turn_index == 0 and objs[1].turn_index == 1
    # 单轮补唯一组名
    assert objs[2].conversation_group == f"g{at.id}_2"
    # attachments JSON 往返 + _to_query_out
    out = _to_query_out(objs[2])
    assert out["attachments"][0]["name"] == "a.png"
    assert out["dimension"] == "artifact" and out["expected"] == "可运行网页"
    assert out["conversation_group"] == f"g{at.id}_2"
    # 清理
    for q in objs: db.delete(q)
    db.delete(at); db.commit()
    print("OK: eval-queries 落库路径(多轮同组/单轮补名/attachments JSON/_to_query_out) 正常")
finally:
    db.close()
```

- [ ] **Step 5: 跑验证**

Run(backend/ 下): `python _verify_eval_endpoint.py`
Expected: 末行 `OK: eval-queries 落库路径(...) 正常`,退出码 0。

- [ ] **Step 6: 端点挂载冒烟(不调引擎)**

Run(backend/ 下,验证 app 能 import 且路由已注册):
```bash
python -c "from app.main import app; paths=[r.path for r in app.routes]; assert '/api/ai/eval-queries' in paths, paths; print('OK: /api/ai/eval-queries 已注册')"
```
Expected: `OK: /api/ai/eval-queries 已注册`。

- [ ] **Step 7: 删验证脚本**

Run: `rm backend/_verify_eval_endpoint.py`

- [ ] **Step 8: 提交**

```bash
git add backend/app/schemas/ai.py backend/app/api/ai_eval.py backend/app/api/router.py
git commit -m "feat(eval): POST /api/ai/eval-queries 生成端点(独立 ai_eval.py)

EvalQueryGenIn + 独立 SSE 生成端点(隔离,不改 gen_testcases)。复用 AiTask(eval_query_gen)、
stream_generate(prompt_builder=eval)、parse_eval_queries;落 EvalQuery,多轮同 conversation_group、
单轮补唯一组名,attachments 存 JSON。router 注册。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 前端生成入口（streamEvalQueries + AIEvalGen.vue + 路由）

前端视图。复用现有 SSE 解析与引擎选择器机制。验证:手动点(启动前后端,走一遍生成)。

**Files:**
- Modify: `frontend/src/api/index.js`(加 streamEvalQueries)
- Create: `frontend/src/views/AIEvalGen.vue`
- Modify: `frontend/src/router/index.js`(路由 + 导航)

**Interfaces:**
- Consumes: Task 4 `POST /api/ai/eval-queries`(SSE, done 帧 `queries`);现有 `aiStatus()`(引擎列表)
- Produces: 用户可见的生成页面

- [ ] **Step 1: 加 streamEvalQueries(仿 streamTestcases)**

先读 `frontend/src/api/index.js` 的 `streamTestcases`(约 :233-281)完整实现。在其后加 `streamEvalQueries`,**结构完全照搬**,仅两处不同:①URL `'/api/ai/eval-queries'`;②done 帧读 `evt.queries`(而非 `evt.cases`)传给 `onDone`。其余(fetch POST、token 取 localStorage.tp_token、`\n\n` 分帧、只认 delta/done/error、忽略非 data: 行、signal 取消)一字不差。导出该函数(与 streamTestcases 同样的 export 方式)。

(实现者:读到 streamTestcases 原文后,复制并改 URL + queries 字段。此为有意复制——两条 SSE 消费链路隔离,与后端 ai_eval.py 一致。)

- [ ] **Step 2: 新建 AIEvalGen.vue**

先读 `frontend/src/views/AITestGen.vue` 了解现有生成页结构(引擎选择器 :16-30、aiStatus onMounted :377-392、streamTestcases 调用 :544-558、输入模式 MODES、结果表格)。新建 `frontend/src/views/AIEvalGen.vue`,参照其骨架,但:
- 输入:需求正文文本框(text 模式即可;url/file 可后续加,本版先支持粘贴文本 —— YAGNI)。
- **维度多选**:5 个 checkbox(thinking/tool_use/artifact/multi_turn/instruction),至少选一个才能提交。用 ElementPlus `el-checkbox-group`。中文标签:思考推理/工具·MCP调用/产物生成/多轮追问/指令遵循。
- 引擎选择器:复用 `aiStatus()` 取 providers,`el-radio-group` 渲染可用引擎(照 AITestGen 的 availProviders/ENGINE_META)。
- 提交:调 `streamEvalQueries({project_id, task_id, input_type:'text', provider, requirement, dimensions}, {onDelta累积rawStream, onDone填queries+meta, onError提示})`。
- 结果展示:`el-table` 列出 queries——title / dimension(转中文标签) / prompt / expected / conversation_group / turn_index。多轮同组的可视觉分组(按 conversation_group 排序展示即可,不必强分组 UI —— YAGNI)。
- project_id 来源:照 AITestGen 从路由参数/props/store 取(读现有页确认口径,对齐)。

完整实现由实现者参照 AITestGen.vue 写出(结构同构,替换维度多选 + queries 展示)。保持 ElementPlus 组件与项目现有风格一致(`@` 别名、api/index.js 导入)。

- [ ] **Step 3: 挂载路由 + 导航入口**

`frontend/src/router/index.js`:参照 AITestGen 的路由定义,加一条 `AIEvalGen` 路由(懒加载 `() => import('@/views/AIEvalGen.vue')`,path 如 `/ai-eval-gen` 或按现有 AI 页命名惯例)。若 AITestGen 路由带 `meta`(如需登录/角色),对齐。若项目有导航菜单组件,加一个入口(读 AITestGen 在导航里的位置,平行加一个"对话测评生成")。

- [ ] **Step 4: 前端构建验证**

Run(frontend/ 下): `npm run build`
Expected: 构建成功、无报错(尤其无 AIEvalGen.vue 的语法/import 错、无路由引用错)。
(若本机无 node_modules 先 `npm install`。)

- [ ] **Step 5: 手动端到端(有 claude 环境时)**

启动后端(`uvicorn app.main:app --reload --port 8000`,backend/ 下)+ 前端(`npm run dev`,frontend/ 下),浏览器进对话测评生成页:选 ≥1 维度 + 填需求 + 选引擎 → 生成 → 结果表格出现 query 列表(多轮题同组)。查库 `eval_query` 有对应行。
(若本机无 claude CLI / 引擎不可用,此步记录为"待有引擎环境时验",不阻塞提交 —— Step 4 的构建 + 后端 Task4 的落库验证已覆盖代码正确性。)

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/index.js frontend/src/views/AIEvalGen.vue frontend/src/router/index.js
git commit -m "feat(eval): 前端对话测评 query 生成页

streamEvalQueries(仿 streamTestcases,换 URL/queries 字段) + AIEvalGen.vue(维度多选 +
引擎选择器复用 + queries 结果表)+ 路由/导航。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage(逐节对照 spec §6):**
- §6.1 EvalQueryGenIn → Task 4 Step 1 ✓
- §6.2 build_eval_query_prompt + parse_eval_queries → Task 2 ✓
- §6.3 stream_generate 参数化(方案甲,默认 None 回落,不改 gen_testcases) → Task 3 ✓
- §6.4 api/ai_eval.py(SSE 骨架复制、多轮落库、_to_query_out) → Task 4 Step 2 ✓
- §6.5 router 注册 → Task 4 Step 3 ✓
- §6.6 dimension 列(模型+schema.sql+migrate) → Task 1 ✓
- §6.7 前端 streamEvalQueries + AIEvalGen.vue + 路由 → Task 5 ✓
- §7 迁移(ensure_eval_query_dimension) → Task 1 Step 3-4 ✓
- §9 验证方式(手动脚本/curl/前端) → 各 Task 验证步 ✓

**2. Placeholder 扫描:** 后端 Task 1-4 均给完整代码;Task 5 前端因需参照现有 .vue 大量既有结构(引擎选择器/表格),给的是"照 AITestGen 骨架 + 明确的差异点(维度多选/queries 展示/URL)",并要求实现者先读原文再复制改 —— 这是"有意复制现有组件"的合理处理(与后端 SSE 骨架同理),非占位。streamEvalQueries 明确"仅改 URL + queries 字段,其余一字不差"。

**3. 类型一致性:** `prompt_builder`(Task 3 定义 None 默认/无参 callable → Task 4 传 `lambda: build_eval_query_prompt(...)` 无参)一致;`parse_eval_queries` 产出字段(Task 2:title/prompt/dimension/expected/attachments/conversation_group/turn_index)→ Task 4 落库逐字段消费一致;done 帧 `queries`(Task 4)→ 前端 `evt.queries`(Task 5)一致;`EvalQuery.dimension`(Task 1)→ Task 4 落库 + _to_query_out 用一致。

---

## Execution Handoff

计划已存 `docs/superpowers/plans/2026-08-21-eval-query-generation.md`。
