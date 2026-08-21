# 设计:对话测评链路 · 子项 1 —— 平台 AI 生成对话测评 query

- 日期:2026-08-21
- 状态:已评审(待落实现计划)
- 所属大工程:**对话测评链路**(平台 AI 生成 query → 下发指定设备 → CLI 执行器跑对话+抓轨迹 → 回写 → 大模型判定 → 飞书回填 + 异常推 multica)
- 本 spec 范围:**仅子项 1(平台 AI 生成对话测评 query)**。执行下发/CLI 改造/判定/回填各为独立子项。
- 依赖:子项 0(数据模型 `eval_query`/`eval_run`,已合入 main,commit 6ff5378)
- 关联代码:`backend/app/api/ai.py`(生成链路样板,不改)、`backend/app/services/claude_runner.py`、`backend/app/services/generators/`、`backend/app/schemas/ai.py`、`backend/app/models/ai_eval.py`、`backend/app/db/migrate.py`、`backend/sql/schema.sql`、`backend/app/api/router.py`、`frontend/src/api/index.js`

## 1. 背景与问题

子项 0 建好了 `eval_query`(对话测评题)表,但**没有任何东西往里写**。本子项建"平台 AI 生成对话测评 query"这条链路,让平台能从需求文档生成一批 query 落 `eval_query`,供后续子项下发执行。

平台已有一条成熟的"AI 生成功能测试点"链路(`api/ai.py::gen_testcases`,SSE 流式 → 落 `test_case`),但它产出的是**功能测试点**(category/steps/expected/script,给 runner 做 pass/fail 断言),与"对话测评 query"(拿去和被测大模型对话、考其思考/工具·mcp 调用/产物的提问)是不同产物。本子项**仿照**这条链路的机制,新建一条**独立、隔离**的生成链路,产出对话 query。

**关键前提(brainstorm 已定的设计决策):**
- 输入:需求文档(复用现有 text/url/file 通道)+ 用户勾选的"对话测评维度"引导生成方向(方案 D)。
- 产物:AI 可产出**多轮对话序列**(同 `conversation_group` 多 `turn_index`,方案 1B)、**一并产出 `expected`**(判定层参照,方案 2A)、`dialog_options` 留空(执行策略,不由生成决定,方案 3A)。
- 被测引擎(claude/codex 等)是**执行属性**、非 query 属性:query 与引擎无关(考题不绑考生),同一 query 可派不同引擎各跑一次对比 → 该字段留到子项 2 加在 `eval_run`,本子项不涉及。

## 2. 目标与非目标

**目标**
- 新增 `POST /api/ai/eval-queries`(SSE 流式生成),从需求文档 + 维度生成对话测评 query,落 `eval_query`。
- 生成任务复用现有 `AiTask`(`kind='eval_query_gen'`),复用 claude/deepseek 双引擎、复用 SSE 事件协议(delta/result/error/heartbeat/done)。
- 支持多轮序列生成(conversation_group/turn_index)、一并产出 expected、记录每条主考维度(dimension)。
- 前端提供生成入口(引擎选择器/aiStatus 复用)。

**非目标(YAGNI)**
- 不改现有 `api/ai.py::gen_testcases` 及功能测试点链路(**隔离**:新建 `api/ai_eval.py`,见 §3 决策 2)。
- 不含执行下发、CLI 改造、判定、回填、multica(后续子项)。
- 不生成 `dialog_options`(留空)、不设"被测引擎"字段(子项 2)。
- 不做 query 的编辑/删除/回归标记等管理接口(本子项只管"生成 + 落库 + 三态评审沿用";若需管理接口另议)。评审采纳复用 `eval_query.review_status`,但采纳/编辑接口本子项不建(YAGNI,先能生成)。

## 3. 关键决策(已逐节确认)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 输入形态 | 需求文档(复用 text/url/file)+ 用户勾选对话测评维度(方案 D) |
| 2 | 端点归属 | **新建 `api/ai_eval.py` 独立 router**,不挤进 `ai.py`——隔离,避免改动波及现有测试点生成 |
| 3 | 多轮生成 | AI 产出多轮序列(同 conversation_group 多 turn_index,方案 1B) |
| 4 | expected | 生成时一并产出(方案 2A) |
| 5 | dialog_options | 留空,不由生成决定(方案 3A) |
| 6 | dimension | `eval_query` **加 `dimension` 列**记每条主考维度(用户同意) |
| 7 | 引擎 prompt 接缝 | **方案甲**:`stream_generate` 加可选 `prompt_builder` 参数(默认 testcase),eval 传 `build_eval_query_prompt`——最 DRY,claude/deepseek 各改一行 |
| 8 | 被测引擎字段 | **不在本子项**,留子项 2 加在 `eval_run.target_engine` |

## 4. 对话测评维度(初版清单)

`EvalQueryGenIn.dimensions: list[str]`,用户至少选一个;AI 按选中维度成比例生成 query。取值(字符串常量,不设枚举列——维度是生成引导词,存到 `eval_query.dimension` 记每条主考的那个):

| 维度值 | 生成的 query 意图 |
|---|---|
| `thinking` | 需多步推理/规划才能答的问题(考思考过程完整性) |
| `tool_use` | 需联网搜索/调用工具才能完成的任务(考工具·mcp 调用是否正常) |
| `artifact` | 要求产出网页/文件/代码等交付物(考产物是否符合预期) |
| `multi_turn` | 需多轮对话逐步细化的场景(考上下文连贯;这类天然产出多条同组 query) |
| `instruction` | 带明确约束/格式要求的任务(考指令遵循) |

维度清单以字符串常量维护在 `claude_runner.py`(供 prompt 构造引用),前端选择器也用同一份(前端各自列,或后端 `/api/ai/status` 暂不扩展——本子项前端硬编码这 5 个即可,YAGNI)。

## 5. AI 产出的 query 结构

`build_eval_query_prompt` 要求模型输出 JSON 数组,每条:

```json
{
  "title": "题目摘要(<=512字)",
  "prompt": "发给被测大模型的提问正文(必填)",
  "dimension": "thinking|tool_use|artifact|multi_turn|instruction(该 query 主考维度)",
  "expected": "期望被测模型产出什么/做到什么(判定层参照)",
  "attachments": [],
  "conversation_group": "g1",
  "turn_index": 0
}
```
- 多轮:同 `conversation_group` 下多条,`turn_index` 0/1/2… 按序;单轮题各自独立组(生成时给唯一组名或留空由落库补)。
- `attachments`:一般空数组;仅当需求文档本身含附件语义时才产出(与 CLI `_parseAttachments` 结构对齐:`[{name, url?/file_token?}]`)。
- `dialog_options`:**不产出**(落库留 NULL)。

## 6. 组件改动(文件级)

### 6.1 `backend/app/schemas/ai.py` —— 新增 `EvalQueryGenIn`

仿现有 `TestCaseGenIn`(schemas/ai.py:8-14):
```python
class EvalQueryGenIn(BaseModel):
    project_id: int
    task_id: int | None = None
    input_type: AiInputType = AiInputType.text     # text/url/file
    provider: str | None = None                    # claude/deepseek;空/非法后端 normalize 回落
    requirement: str = Field(min_length=1, max_length=20000)  # 需求正文(url/file 由前端取文后填)
    dimensions: list[str] = Field(min_length=1)    # 至少一个对话测评维度
```
(放 `schemas/ai.py` 现有 TestCase 系列 schema 之后;不新建 schema 文件——schema 内聚在 ai.py 符合平台惯例,且这不涉及"改动波及现有链路"的风险。)

### 6.2 `backend/app/services/claude_runner.py` —— 新增两函数

- `build_eval_query_prompt(requirement, dimensions) -> str`:构造要求模型产出 §5 结构 query 数组的 prompt。**不注入** selector key 清单 / api 契约块 / script DSL(那些是测试点特有)。prompt 里按 `dimensions` 引导覆盖哪些维度、说明多轮怎么用同组、要求每条带 expected。维度说明文案用 §4 的清单。
- `parse_eval_queries(raw) -> list[dict]`:复用现有 `_extract_cases_array`(claude_runner.py:610-630)/`_salvage_objects` 提取 JSON 数组,但字段映射为 §5 结构;**不走** `_validate_script`/`_registered_keys`/`_key_page_map`。产出 dict 字段:`title / prompt / dimension / expected / attachments(list) / conversation_group / turn_index`。校验:丢无 `prompt` 或无 `title` 的条目;`dimension` 不在 5 个合法值内 → 置空或回落输入的首个维度;`turn_index` 非整数 → 0;`conversation_group` 空 → 后续落库补唯一组名。

### 6.3 `backend/app/services/generators/` —— prompt 接缝参数化(方案甲)

**核心约束:现有 testcase 调用零改动(不碰 `gen_testcases`)。** 故参数默认 `None`、内部回落原逻辑,而非默认 `build_testcase_prompt`(后者需 project_id/pages 参数,做不成默认值)。两种 builder 签名不同(前者 requirement/project_id/pages,后者 requirement/dimensions),用"调用方传无参 lambda"消除差异。

- `claude_runner.py::stream_generate`(claude_runner.py:479-562):签名加可选参数 `prompt_builder=None`。函数体 `:490` 写死的
  ```python
  cmd = _build_cmd(build_testcase_prompt(requirement, project_id, pages))
  ```
  改为:
  ```python
  prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(requirement, project_id, pages)
  cmd = _build_cmd(prompt)
  ```
  这样:现有 `gen_testcases` 调 `stream_generate(requirement, project_id, pages)` 不传 `prompt_builder` → 走 else 分支,**行为完全不变**;eval 端调 `stream_generate(requirement, project_id=project_id, prompt_builder=lambda: build_eval_query_prompt(requirement, dimensions))` → 走 if 分支。`stream_generate` 无需知道两 builder 的签名差异。
- `deepseek_runner.py::stream_generate`(deepseek_runner.py:80-158,`:103` 写死 `build_testcase_prompt` 处)同样:加 `prompt_builder=None`,`prompt = prompt_builder() if prompt_builder is not None else build_testcase_prompt(...)`;import 处(deepseek_runner.py:29-38)加 `build_eval_query_prompt, parse_eval_queries`。
- `generators/__init__.py`:PROVIDERS/get_provider/normalize_provider/available_providers **不改**(与生成何种产物无关)。
- **`api/ai.py::gen_testcases` 不改**——它对 `stream_generate` 的调用不传 `prompt_builder`,靠默认 None 回落原逻辑。这是"隔离"决策(§3 决策 2)在共享代码层的落实:唯一改动是给 `stream_generate` 加一个带默认值的可选参数,现有调用方一字不动。

### 6.4 `backend/app/api/ai_eval.py` —— 新建独立 router(核心)

新文件。`router = APIRouter(prefix="/api/ai", tags=["ai-eval"])`(同 prefix,不同文件),在 `api/router.py` 注册。端点 `POST /api/ai/eval-queries`。

**SSE 骨架:有意复制 `gen_testcases` 的框架,不共享。** 理由(决策 2):用户明确要隔离两条链路,避免改动 eval 链路波及功能测试点生成。SSE 骨架(双 session、心跳/delta/error 转发、指标写入、StreamingResponse)在本文件独立实现一份——这是**有意的、经权衡的复制**,不是疏漏;两条链路的落库逻辑本就不同(eval 多轮落库 + 落 EvalQuery),未来可独立演化。spec 明确标注此点,code review 不应judge为 DRY 缺陷。

骨架步骤(照 gen_testcases:147-284,换三处):
1. 鉴权 `assert_project_role(db, user, body.project_id, _WRITE_ROLES)` + 项目存在性。
2. `provider_id = normalize_provider(body.provider)`;`engine = get_provider(...)`;`is_available()` 闸(503)。
3. 建 `AiTask(kind="eval_query_gen", provider=provider_id, input_type=body.input_type, input_ref=body.requirement[:20000], status=running, ...)`;commit 取 id。
4. 快照 `ai_task_id/project_id/task_id/requirement/dimensions` 到局部(StreamingResponse 生成器不能用注入 db)。
5. `sse()` 生成器:
   - `for evt in engine.stream_generate(requirement, project_id=project_id, prompt_builder=lambda: build_eval_query_prompt(requirement, dimensions))`:heartbeat→`: hb`、delta→累积 raw + 转发、result→存 meta、error→转发。
   - 落库(新 `SessionLocal`):写指标(duration/cost/tokens/output_raw);`cases = engine.parse_eval_queries(raw)`;空→AiTask failed + done{failed}。
   - **多轮落库**:遍历 cases,`conversation_group` 为空的补唯一组名(如 `f"g{ai_task_id}_{i}"`);逐条 `EvalQuery(ai_task_id=..., provider=provider_id, project_id=..., task_id=..., title=c["title"], prompt=c["prompt"], dimension=c.get("dimension") or None, expected=c.get("expected") or None, attachments=json.dumps(c["attachments"]) if c.get("attachments") else None, conversation_group=..., turn_index=c.get("turn_index") or 0, review_status=pending)`;add。
   - AiTask done + case_count=len(cases);commit;done 帧`{ai_task_id, status:"done", queries:[_to_query_out(q)...], meta:{...}}`。
   - 异常 rollback + error 帧;finally close。
6. `return StreamingResponse(sse(), media_type="text/event-stream")`。

`_to_query_out(q)`:EvalQuery ORM → dict(id/ai_task_id/project_id/task_id/title/prompt/dimension/expected/attachments/conversation_group/turn_index/review_status/created_at)。

### 6.5 `backend/app/api/router.py` —— 注册新 router

`from app.api import ai_eval` + `api_router.include_router(ai_eval.router)`(router.py:7-24 现有 include 之后加)。

### 6.6 数据模型:`eval_query` 加 `dimension` 列

- `backend/app/models/ai_eval.py`:`EvalQuery` 加 `dimension: Mapped[str | None] = mapped_column(String(16), nullable=True)`(放 title 附近或末尾;记每条主考维度)。
- `backend/sql/schema.sql`:`eval_query` 建表加 `\`dimension\` VARCHAR(16) NULL`(两份 schema 同步)。
- **`backend/app/db/migrate.py`:新增 `ensure_eval_query_dimension()`** —— 与子项 0 不同,`eval_query` 已在 main 上、老库已建表,加列必须走 migrate(仿 `ensure_testcase_columns` 模式:探列不存在则 `ALTER TABLE eval_query ADD COLUMN dimension VARCHAR(16) NULL`),在 `main.py::init_db` 里调用。新库 create_all 已含该列,migrate 探到存在即跳过(幂等)。

### 6.7 前端:`frontend/src/api/index.js` + 生成入口

- `api/index.js`:新增 `streamEvalQueries(payload, {onDelta,onDone,onError,signal})`,仿 `streamTestcases`(index.js:233-281),SSE 解析逻辑完全复用,仅 URL 换 `/api/ai/eval-queries`、done 帧字段 `queries` 而非 `cases`。
- 生成入口:新视图 `AIEvalGen.vue`(或现有 AITestGen.vue 加"生成类型"切换)。**本 spec 采新视图**(隔离,与后端新文件一致):维度多选(5 个 checkbox)、输入模式(text/url/file 复用)、引擎选择器(`aiStatus`/providers 复用)、结果表格展示 title/dimension/prompt/expected/多轮分组。路由挂载 + 导航入口。

## 7. 迁移与两份 schema 同步

1. `EvalQuery` 加 `dimension` 列:模型 + schema.sql 同步 + **migrate.py 补 `ensure_eval_query_dimension`**(老库补列,幂等)。这是本子项唯一的迁移点(区别于子项 0 纯新建表)。
2. `AiTask.kind` 加值 `eval_query_gen`:`String(32)` 自由列,**无需迁移**。
3. 无其它 schema 变更。

## 8. 影响面与风险

- **隔离设计降低回归风险**:新建 `api/ai_eval.py` + 新视图,不改 `gen_testcases`;唯一改到现有共享代码的是 `stream_generate` 加可选参数(默认值保证现有 testcase 调用行为不变)——这是最小侵入点,需重点验证"现有测试点生成不受影响"。
- **风险 1(共享代码改动)**:`stream_generate` 加 `prompt_builder` 参数。缓解:参数有默认值 `build_testcase_prompt`,现有调用不传参即行为不变;验证时跑一次现有测试点生成确认无回归。
- **风险 2(prompt 质量)**:对话 query 的 prompt 是全新的,生成质量未知(会不会产出得像功能用例、多轮拆分是否合理)。缓解:本子项交付"链路通 + 结构对",prompt 措辞可后续迭代(纯 prompt 改,不动结构);spec 不追求 prompt 完美。
- **风险 3(有意复制的 SSE 骨架)**:`ai_eval.py` 复制了 `gen_testcases` 的骨架。已在 §6.4 声明为经权衡的隔离决策;两链路落库逻辑本就不同。若未来两者需同步改 SSE 协议,需两处都改——接受此代价换隔离。

## 9. 验证方式(本仓库无测试框架,手动端到端)

1. 启动后端,`POST /api/ai/eval-queries`(带 project_id/requirement/dimensions/provider),观察 SSE 流:有 delta、最终 done 帧带 queries 数组。
2. 查库:`eval_query` 落了对应条目,字段正确(prompt/dimension/expected 非空,dialog_options 为 NULL,多轮条目 conversation_group 相同、turn_index 递增)。`ai_task` 有一条 kind='eval_query_gen'、status=done、case_count 对。
3. 老库迁移:在已有 `eval_query`(无 dimension 列)的旧 SQLite 库上启动,确认 `ensure_eval_query_dimension` 补列成功、不报错;新库 create_all 直接含该列。
4. **回归**:跑一次现有 `POST /api/ai/testcases`(测试点生成),确认 `stream_generate` 加参数后行为不变、正常落 test_case。
5. deepseek 引擎(若配置可用):同样跑一次 eval-queries,确认复用 prompt/解析、正常落库。
6. 前端:新视图选维度 + 填需求 + 选引擎 → 生成 → 结果表格展示 query(含多轮分组)。

## 10. 交付清单

- [ ] `schemas/ai.py`:`EvalQueryGenIn`
- [ ] `claude_runner.py`:`build_eval_query_prompt` + `parse_eval_queries`
- [ ] `claude_runner.py` + `deepseek_runner.py`:`stream_generate` 加 `prompt_builder` 参数(方案甲);deepseek import 两新函数
- [ ] `api/ai_eval.py`:新建,`POST /api/ai/eval-queries`(SSE) + `_to_query_out`
- [ ] `api/router.py`:注册 ai_eval.router
- [ ] `models/ai_eval.py` + `sql/schema.sql` + `migrate.py`:`eval_query` 加 `dimension` 列 + `ensure_eval_query_dimension`
- [ ] 前端:`api/index.js` `streamEvalQueries` + `AIEvalGen.vue` 视图 + 路由/导航
- [ ] 手动验证(§9),含现有测试点生成回归

## 11. 后续子项(本 spec 之外)

- 子项 2:执行下发协议 + CLI 执行器改造(把 eval_query 下发到指定设备/被测引擎,CLI 拉任务 + CDP 截 WS 帧抓轨迹回写 eval_run;**此处给 eval_run 加 `target_engine` 字段**记被测引擎 claude/codex/…)。
- 子项 3:大模型判定层(消费 eval_run.trace,产 verdict_dims/verdict/is_abnormal)。
- 子项 4:飞书回填 + multica 推送。
