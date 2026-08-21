# 设计:对话测评链路 · 子项 0 —— 数据模型地基（eval_query / eval_run）

- 日期:2026-08-21
- 状态:已评审(待落实现计划)
- 所属大工程:**对话测评链路**(把 `D:\code\ai-eval-cli-yt` 的 AI 对话测评能力接入本平台,端到端:平台 AI 生成 query → 下发指定设备 → CLI 执行器跑对话+抓全过程轨迹 → 回写平台 → 大模型判定 → 飞书回填 + 异常推 multica)
- 本 spec 范围:**仅子项 0(数据模型)**。生成/下发/执行/判定/回填各为独立子项,另行 spec。
- 关联代码:`backend/app/models/ai.py`(建模风格样板)、`backend/app/models/exec_queue.py`、`backend/app/core/enums.py`、`backend/app/models/__init__.py`、`backend/sql/schema.sql`、`backend/app/db/migrate.py`

## 1. 背景与问题

平台现有两条**互不相干**的能力,都不覆盖"对话测评":

- **平台 AI 生成**(`api/ai.py` + `models/ai.py`):从需求文档生成**结构化功能测试点**(category/title/steps/expected/script),落 `test_case` 表,配套"生成→三态评审采纳"。产物是"功能用例",不是"拿去和大模型对话的 query"。
- **平台 exec_queue 闭环**(`api/exec_queue.py` + `models/exec_queue.py`):勾选用例下发 → runner(Playwright CDP 连纳米Work 客户端)执行 gui/api/e2e/cli → 回写 **pass/fail** → 同步验收清单。判定是"功能断言通过与否",语义是二元对错。

而 `ai-eval-cli-yt` 是**独立的** Node 工具:飞书表格读 query → 驱动 work.n.cn/纳米Work 客户端逐条对话 → 抓【对话分享链接/产物分享链接/耗时/算力豆/正文】→ 实时回填飞书;仅有**规则式**串台诊断,**无大模型判定、无 multica、不与平台交互**。

目标形态(大工程已定的四个方向):平台编排、CLI 退化为纯执行器、CLI 抓**会话全过程轨迹**回传、query 由平台 AI 生成、异常会话推 multica。这要求平台先有一处**存"对话测评题 + 每次执行与判定结果"的数据落点**——语义上区别于 `test_case`(功能用例)/`exec_run`(功能 pass/fail),不能硬塞进去。本 spec 建这个地基。

## 2. 目标与非目标

**目标**
- 平台新增数据模型,承载:①一道对话测评题(query + 附件 + 多轮分组 + 对话选项 + 期望);②一次执行的会话全过程轨迹(思考/工具·mcp 调用/产物/正文 + 分享链接 + 耗时/算力豆);③对该次执行的大模型判定结论(三维 + 总判定 + 是否异常 + 是否已推 multica)。
- 完全对齐平台既有建模风格:`Text` 存 JSON(兼容生产 MySQL 5.6,不用原生 JSON 列)、枚举带 `length`、软关联 project/task、手写序列化。
- 两份 schema(SQLAlchemy 模型 + `sql/schema.sql`)同步;老库零迁移即可用(全为新表 + `String` 枚举加值)。

**非目标(YAGNI)**
- 不含任何 API 路由 / service / 前端(子项 1~4)。
- 不含 CLI 执行器改造、不含 WS 帧抓取实现、不含大模型判定逻辑、不含飞书回填、不含 multica 对接。
- 不复用 `test_case`/`exec_run`——语义不同(功能用例 vs 对话测评题;功能 pass/fail vs 会话质量三维判定),强行复用会污染现有统计口径(`/stats/ai`、checklist-summary)。
- 不建独立"判定表"——判定与执行一对一,合并进 `eval_run`(见 §5.2 决策)。
- 不建独立"生成任务表"——复用现有 `AiTask`(见 §5.3 决策)。

## 3. 关键决策(已逐节确认)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 落点位置 | **平台编排**,CLI 当纯执行器;数据落平台新表 |
| 2 | 判定输入 | **CLI 抓会话全过程轨迹回传**,大模型对完整轨迹判定(非只喂分享链接) |
| 3 | 轨迹来源 | **CDP 截 work.n.cn 的 WebSocket 事件帧**(非抓 DOM);注入读 `openclaw-app` 内存 state 作断连补帧兜底 |
| 4 | query 来源 | **平台 AI 生成**(新产物类型,复用 claude/deepseek 抽象) |
| 5 | 表拆分 | **3 张**:`eval_query`(题)/ `eval_run`(执行+判定,合并)/ 生成任务复用 `AiTask` |
| 6 | 判定与执行 | **合并进 `eval_run`**(一对一,判定即对该次执行的结论) |
| 7 | 生成任务记录 | **复用 `AiTask`**,加 `kind='eval_query_gen'`(不建 `eval_gen_task`) |
| 8 | 执行态粒度 | `EvalRunStatus` **拆 judging/judged**(对话完成与判定完成是两阶段,可异步) |
| 9 | cli 执行载体 | 建 `EvalDeviceKind.cli` **占位**,具体用法留待子项 2 |

### 3.1 轨迹来源的依据（B 深读结论,决定 §5.4 的 trace 形态）

`openclaw360-web` 已确认为 work.n.cn 前端源码。AI 对话数据走一条 **WebSocket**(`/api/claw/v2/ws?vm_id=...`,自定义 JSON 帧协议 protocol=3),**不是 SSE/DOM**:
- 外部 CDP 可截获每一帧(`page.on('websocket')`→`framereceived` / `Network.webSocketFrameReceived`)。
- 事件帧 `{type:"event", event:"agent", payload:{stream, data, runId, sessionId, sessionKey, seq, toolCallId,...}}`。思考正文=`stream:"thinking"` 的 `data.text`;工具调用=`stream:"tool"` 的 `data.{name, originalToolName, phase, toolCallId, args, result}`。
- **MCP 与内置工具靠 `data.originalToolName` 的 `mcp__<server>__<tool>` 前缀区分**(server 内嵌名字,无独立类型字段)。⚠️ DOM 上 `name` 已被 L4 改写成中文,判 MCP 只能用帧里 `originalToolName` 全名——这是"抓 DOM 做不到、抓 WS 才能做到"的关键差异。
- 权威协议文档:前端 `src/ui/AGENTS.md`。解析逻辑(解 `nami_panel` 信封、按 `toolCallId` 聚合多 phase 帧)可移植前端 `app-gateway.ts`/`app-tool-stream.ts`。

结论:执行器在 WS 帧层拿到结构化语义数据,**规整成干净 JSON 后**回写 `eval_run.trace`,判定层直接消费,不接触原始帧/DOM。

## 4. 新增枚举（`app/core/enums.py`）

对齐现有 `ExecStatus`/`AiTaskStatus`/`ReviewStatus` 风格(`str, enum.Enum`,值为小写)。

```python
class EvalRunStatus(str, enum.Enum):
    """一次对话测评执行 + 判定的生命周期。"""
    pending = "pending"    # 已下发,等执行机拉取
    running = "running"    # 执行机已认领、对话进行中
    done    = "done"       # 对话+轨迹抓取完成(尚未判定)
    judging = "judging"    # 轨迹已回传,大模型判定中
    judged  = "judged"     # 判定完成(终态)
    failed  = "failed"     # 执行失败(对话没跑起来/抓取失败;区别于"判定不通过")

class EvalDeviceKind(str, enum.Enum):
    """执行载体(对齐 ai-eval-cli 的三种运行形态)。"""
    web     = "web"        # Web 多账号(ContextPool,注入 storageState 登录态)
    desktop = "desktop"    # 桌面客户端(CDP 连 Electron 单客户端多对话)
    cli     = "cli"        # 命令行执行(具体形态见子项 2;先占位)

class EvalVerdict(str, enum.Enum):
    """大模型对一次会话的总判定。"""
    passed = "pass"        # 三维皆过(用 passed 规避 Python 保留字 pass)
    failed = "fail"        # 有维度不过
    error  = "error"       # 判定本身出错(轨迹缺失/判定引擎异常)
```

- `eval_query.review_status` **复用 `ReviewStatus`**(pending/adopted/rejected),不新建。
- `EvalRunStatus.failed`(执行失败)与 `EvalVerdict.failed`(判定不通过)是**两件事**:前者是"没跑成",后者是"跑成了但会话质量不合格"。异常推 multica 的触发是后者(`is_abnormal`),不是前者。

## 5. 数据模型（`app/models/ai_eval.py`，新文件）

放新文件 `models/ai_eval.py`(不塞进 `models/ai.py`,后者是功能测试点专属;新文件边界清晰)。须在 `models/__init__.py` 汇总导入。

### 5.1 `eval_query` —— 一道对话测评题

类比 `TestCase`。存要发给被测大模型的 query 及其执行参数。

```python
class EvalQuery(Base):
    __tablename__ = "eval_query"

    id: Mapped[int]                        # PK
    project_id: Mapped[int]                # FK project, CASCADE, index
    task_id: Mapped[int | None]            # FK task, SET NULL, index(可挂到派单任务)
    ai_task_id: Mapped[int | None]         # FK ai_task, SET NULL, index(哪次 AI 生成的;人工录入为 NULL)
    provider: Mapped[str]                  # String(16),生成引擎 claude/deepseek,default claude,index(冗余自 ai_task,免 join)
    title: Mapped[str]                     # String(512),题目摘要
    prompt: Mapped[str]                    # Text,query 正文(发给被测模型的提问)
    attachments: Mapped[str | None]        # Text-JSON,附件[{name,file_token?/url?}],对齐 CLI _parseAttachments
    conversation_group: Mapped[str | None] # String(64),多轮分组键(对齐 CLI conversationId;NULL=单轮独立)
    turn_index: Mapped[int]                # 同组内第几轮(0 起),default 0
    dialog_options: Mapped[str | None]     # Text-JSON,{model?,chatMode?,thinkingDepth?}(对齐 CLI dialogOptions)
    expected: Mapped[str | None]           # Text,期望产物/行为描述(判定时喂给大模型作参照;可空)
    review_status: Mapped[ReviewStatus]    # Enum(len16),pending/adopted/rejected,default pending
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime]           # server_default now()
```

### 5.2 `eval_run` —— 一次执行 + 判定结果（核心大表）

类比 `exec_run`,但承载**会话轨迹 + 三维判定**。一道题可派到不同设备/多次执行,各留一条。**判定与执行合并在此表**(决策 6):判定是对"这一次执行"的结论,一对一,无需拆表。

```python
class EvalRun(Base):
    __tablename__ = "eval_run"

    id: Mapped[int]                          # PK
    # —— 下发 ——
    eval_query_id: Mapped[int | None]        # FK eval_query, SET NULL, index(题删了执行记录仍留痕,学 exec_run.test_case_id)
    project_id: Mapped[int]                   # FK project, CASCADE, index
    batch_id: Mapped[str | None]              # String(32), index(一次下发一批,结果页按批汇总)
    runner: Mapped[str]                       # String(64), index(哪台设备的 runner_id)
    device_kind: Mapped[EvalDeviceKind]       # Enum(len8), default web
    status: Mapped[EvalRunStatus]             # Enum(len16), default pending, index
    # —— CLI 抓回的会话数据 ——
    session_id: Mapped[str | None]            # String(64)(work.n.cn 会话 UUID,来自 WS 帧;可回链)
    share_link: Mapped[str | None]            # String(512)(对话分享链接)
    artifact_share_link: Mapped[str | None]   # String(512)(产物分享链接)
    answer: Mapped[str | None]                # Text(最终回答正文)
    trace: Mapped[str | None]                 # Text-JSON(会话全过程轨迹,结构见 §5.4)
    reported_duration: Mapped[str | None]     # String(32)(平台上报耗时,秒;对齐 CLI reportedDuration)
    bean_cost: Mapped[str | None]             # String(32)(算力豆变动)
    tokens: Mapped[str | None]                # String(32)(本次 tokens,仅记录)
    # —— 大模型判定 ——
    verdict: Mapped[str | None]               # String(16)(EvalVerdict 值;NULL=未判定)
    verdict_dims: Mapped[str | None]          # Text-JSON(三维结论,结构见 §5.5)
    verdict_reason: Mapped[str | None]        # Text(判定理由汇总)
    judged_by: Mapped[str | None]             # String(16)(判定用的引擎 claude/deepseek)
    is_abnormal: Mapped[bool]                 # Boolean, default 0, index(是否异常→决定推不推 multica)
    pushed_multica: Mapped[bool]              # Boolean, default 0(是否已推 multica,防重推)
    multica_ref: Mapped[str | None]           # String(512)(multica 侧任务 id/链接;推送后回填)
    # —— 通用 ——
    reason: Mapped[str | None]                # Text(执行失败/未完成原因,对齐 CLI completeReason)
    duration_ms: Mapped[int | None]           # 墙钟耗时
    enqueued_by: Mapped[int | None]           # FK user, SET NULL
    created_at / updated_at                    # server_default now() / onupdate now()
```

索引:`project_id`、`status`、`batch_id`、`is_abnormal`(异常会话查询/推送用)、`runner`。

### 5.3 生成任务:复用 `AiTask`（不新建）

生成 query 复用现有 `AiTask`(决策 7),仅新增 `kind` 取值 **`eval_query_gen`**(现有为 `testcase_gen`)。`AiTask.kind` 是 `String(32)`,加字符串值老库直接兼容,**无需迁移**。`AiTask` 的 provider/input_type/input_ref/status/output_raw/cost_usd/output_tokens/duration_ms 全部可原样复用(它本就是"一次 AI 生成任务"的通用记录)。`EvalQuery.ai_task_id` 指回它。

### 5.4 `eval_run.trace` 的 JSON 结构（执行器规整后回写）

执行器把 WS 帧按 `toolCallId` 聚合、解 `nami_panel` 信封后,回写**规整结构**(非原始帧堆;规整在执行器侧做,判定层拿干净数据):

```json
{
  "session_id": "会话 UUID",
  "run_id": "本轮 runId",
  "thinking": "思考过程正文(stream:thinking 的 data.text 顺序拼接)",
  "tool_calls": [
    {
      "tool_call_id": "...",
      "name": "展示名(可能已中文化)",
      "original_tool_name": "mcp__serper__web_search",
      "is_mcp": true,                 // 执行器按 mcp__ 前缀预判,判定层不再解析
      "mcp_server": "serper",         // 前缀解析出的 server(非 mcp 则 null)
      "args": { },
      "result_text": "工具结果文本",
      "reached_result": true          // 是否跑到 phase:result(未到=中断/失败)
    }
  ],
  "artifacts": [ { "name": "...", "kind": "file|dir", "share_link": "..." } ],
  "answer": "最终回答正文(与顶层 answer 冗余,便于判定单看 trace)"
}
```

判定层消费三块:`thinking`(思考完整性)、`tool_calls`(工具·mcp 调用是否正常)、`artifacts`+`answer`(产物是否符合预期)。

### 5.5 `eval_run.verdict_dims` 的 JSON 结构（判定层回写）

对应用户要的三个判定维度:

```json
{
  "thinking_complete": { "pass": true,  "note": "思考过程完整、有推理链" },
  "tools_ok":          { "pass": false, "note": "mcp__serper__web_search 未返回 result 即中断" },
  "artifact_expected": { "pass": true,  "note": "产出网页符合 expected 描述" }
}
```

总 `verdict` 由三维汇总(任一 false → `fail`);`is_abnormal` 一般 = `verdict==fail`(具体阈值/规则在子项 3 定,此处只存结论)。

## 6. 迁移与两份 schema 同步

1. **3 张新表(`eval_query`/`eval_run`)走 `Base.metadata.create_all` 自动建**——`ensure_*` 是给老表加列用的,新表不需要。**但必须在 `models/__init__.py` 汇总导入**(`from app.models.ai_eval import EvalQuery, EvalRun` + 加进 `__all__`),否则建不全。
2. **`sql/schema.sql` 手工补 2 段 `CREATE TABLE`**(`eval_query`、`eval_run`),字段/类型/索引与模型一致(MySQL:`Text`→TEXT、`String(n)`→VARCHAR(n)、`Boolean`→TINYINT(1)、枚举列→VARCHAR)。这是 CLAUDE.md 强调的"两份 schema 手动同步"。
3. **`AiTask.kind` 加值 `eval_query_gen` 无需迁移**(`String(32)` 非原生 ENUM,区别于 `exec_run.kind` 那种要 `MODIFY COLUMN`)。
4. 外键级联对齐现有:题→执行用 **SET NULL**(留痕),project 用 **CASCADE**,ai_task/user 用 **SET NULL**。

## 7. 影响面与风险

- **零改动现有链路**:纯加表 + `AiTask.kind` 加值,不碰 `test_case`/`exec_run`/`api/ai.py`/`api/exec_queue.py`。现有统计口径(`/stats/ai`、checklist-summary)不受影响。
- **风险 1(可控)**:`trace` 的规整结构依赖 work.n.cn 私有 WS 协议,后端升版可能改字段。缓解:执行器侧解析集中一处(子项 2),用前端 `AGENTS.md` 跟踪;`trace` 存"规整后"结构而非原始帧,协议变只影响执行器的解析层,不影响表结构。
- **风险 2(需现场确认,不阻塞本 spec)**:产物卡片有两个候选 selector(`.chat-file-card` vs `a.chat-artifact-file-card`)——但那是 DOM 路径的事,本方案走 WS 帧的 `stream:tool` result 拿产物,现场确认放子项 2。
- **schema.sql 漏同步**是最常见坑(见既往 spec):本 spec 明确列为交付项,实现时二者一起改。

## 8. 验证方式（本仓库无测试框架,手动端到端）

本 spec 只交付数据模型,验证限于"表能建、能存取":
1. `uvicorn` 启动后端,确认 startup 的 `create_all` 建出 `eval_query`/`eval_run` 两表(SQLite 本地 `.db` 查表结构;或看无异常)。
2. 用 `python -c` / 一次性脚本插入一条 `EvalQuery` + 一条关联 `EvalRun`(含一段样例 `trace`/`verdict_dims` JSON),读回校验 `Text`-JSON 往返无损、枚举取值正确。
3. 确认 `AiTask(kind="eval_query_gen")` 可正常插入(老库兼容)。
4. `sql/schema.sql` 用 MySQL(docker)初始化一遍,确认两段 DDL 语法正确、与模型字段一致。

## 9. 交付清单

- [ ] `app/core/enums.py`:新增 `EvalRunStatus`/`EvalDeviceKind`/`EvalVerdict`
- [ ] `app/models/ai_eval.py`:新增 `EvalQuery`/`EvalRun`
- [ ] `app/models/__init__.py`:汇总导入 + `__all__`
- [ ] `backend/sql/schema.sql`:补 `eval_query`/`eval_run` 两段 `CREATE TABLE`
- [ ] 手动验证(§8)

## 10. 后续子项（本 spec 之外,依赖顺序）

1. 子项 1:平台 AI 生成 query(复用 `AiTask` + claude/deepseek,产物落 `eval_query`;新 prompt/解析)。
2. 子项 2:执行下发协议 + CLI 执行器改造(复用 exec_queue 的 pull 轮询 + device token 模式,走 `eval_run`;CLI 改为拉平台任务 + CDP 截 WS 帧抓轨迹回写)。**工程量最大,含现场确认项。**
3. 子项 3:大模型判定层(消费 `trace`,产出 `verdict_dims`/`verdict`/`is_abnormal`)。
4. 子项 4:飞书回填(搬 CLI `feishu-sheet.js` 写回能力到平台)+ multica 推送(异常会话,API/CLI 待细化)。
