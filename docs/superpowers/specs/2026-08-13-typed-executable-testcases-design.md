# 类型化·可执行测试用例体系 —— 设计稿

- 日期:2026-08-13
- 状态:待评审
- 关联:`tools/qalab-runner/`(runner + gui-mcp + selectors.json)、`backend/app/services/claude_runner.py`(生成)、`backend/app/api/{ai,exec_queue}.py`、`backend/app/models/ai.py`

## 1. 背景与根因

平台"AI 测试助手"生成的是**给人读的测试点**(category=功能/边界/异常…,steps/expected 为自然语言),
且 `TestCase.exec_kind` 在模型层**默认 gui**、生成时根本不产出。下发执行时:

- 每条用例默认按 gui 派到执行机;
- runner 把用例整体**甩给 claude**(黑盒),claude 读不出"点哪、断言啥"就跑偏(实测 case=1「AI 生成 pptx 预览功能」→ claude 去 `git log`/`grep`/`Read` 研究平台源码、反问用户,52s fail;另一次干等 240s 超时)。

**根因**:用例在**生成源头**就不是"可被机器执行的自动化用例",而 runner 又缺乏确定性执行能力、只能靠 LLM 猜。
症状(超时 / 跑偏 / 无法解析)都由此而来。

## 2. 目标 / 非目标

**目标**
- 生成时即为用例定**类型**(gui/api/cli/e2e/manual)并产出**该类型可执行的结构化步骤**;
- 执行走**混合模型**:能确定性执行的步骤 runner 直接跑,只有主观判定降级 claude;
- 不可自动化的用例标 **manual**,平台**不派发**(从源头消除 case=1 类误派);
- 各 kind 用**不同工具集**(gui 只给 `mcp__gui__*`,不给 Bash);
- gui 生成时引用**选择器库**(`selectors.json`)的语义 key,产出真能跑的步骤;
- e2e 作为**独立类型**,支持多步 + 等待策略,与单点用例区分。

**非目标**
- 不追求 100% 自动生成可执行用例;结构化不了的允许留 manual 或降级 claude。
- 不做通用录制回放;步骤 DSL 只覆盖当前被测形态(纳米Work web / 接口 / 命令行)。
- 本轮不改平台整体架构,只在既有分层内扩展。

## 3. 核心决策(已定)

1. **执行=混合,确定优先**:结构化步骤 runner 确定性执行;`judge` 步(主观判定)降级 claude。
2. **kind=AI 判定 + 人工复核可改**:生成时 AI 定 kind 并给理由,人在现有用例复核流里确认/修改。
3. **e2e 独立类型**:`ExecKind` 增加 `e2e`;e2e 用例显式多步 + 等待策略。
4. **manual 不派发**:`ExecKind` 增加 `manual`;平台入队时拒绝 manual(前端置灰 + 后端兜底校验)。
5. **按 kind 分工具**:gui→`mcp__gui__*`;api/cli→`Bash`;e2e→`mcp__gui__*`(+必要时 Bash);manual→不下发。
6. **runner 护栏保留**:禁止 claude 用 git/grep/Read 研究本地仓库或"研究如何实现"(纵深防御)。

## 4. 架构总览(4 层)

```
生成层  claude_runner.py:  需求 → AI 生成 [{kind, category, title, steps(人读), expected, priority, script(可执行)}]
          │                gui 生成时注入 selectors.json 的 key 清单;AI 给 kind + 理由
存储层  TestCase:          +exec_kind(已存在,扩枚举) +script(新增 JSON 列,结构化步骤) +kind_reason
          │
派发层  exec_queue.py:     enqueue 拒绝 manual;payload 带 {kind, script};按 kind 决定 runner 工具集
          │
执行层  runner.mjs:        StepExecutor 按 script 逐步确定性执行(gui-mcp/http/subprocess);
                           judge 步降级 claude;无 script 的旧用例→claude 兜底(带护栏)
```

## 5. 执行步骤 DSL(本设计的核心)

用例的可执行部分是一个**有序步骤数组** `script`,每步是一个固定 `action`。runner 内置执行器逐步执行,
断言步直接判 pass/fail,`judge` 步降级 claude。

### 5.1 步骤结构

```jsonc
{
  "action": "assert_text",       // 固定动作名(见下表)
  "target": { "key": "navTasks" }, // 或 { "selector": ".xxx" };仅定位类步骤需要
  "args": { "expected": "任务", "contains": true },  // 动作参数
  "desc": "断言主导航『任务』可见"   // 人读说明(也进证据链)
}
```

### 5.2 动作集(v1)

| kind | action | 语义 | 确定性? |
|---|---|---|---|
| gui/e2e | `connect` | 确保客户端带 CDP、下钻业务 iframe | 是 |
| gui/e2e | `click` / `fill` / `wait_for` / `get_text` | 经 gui-mcp 操作(target 用 key 优先) | 是 |
| gui/e2e | `assert_text` / `assert_visible` | 确定性断言,直接判 pass/fail | 是 |
| gui/e2e | `wait_response` | 发消息后等 AI 生成完成(轮询 stopBtn 消失/answerBubble.has-copy) | 是(带上限) |
| gui/e2e | `screenshot` | 存证 | 是 |
| api | `http` | 发请求 | 是 |
| api | `assert_status` / `assert_json` | 校验响应码 / JSON 路径值 | 是 |
| cli | `run` | 起进程 | 是 |
| cli | `assert_exit` / `assert_output` | 校验退出码 / 输出包含 | 是 |
| 任意 | `judge` | 把已捕获上下文交 claude 做主观判定(如"回复是否合理") | 否(降级 claude) |

- **判定**:所有 `assert_*` 步任一失败 → 用例 fail(附该步 desc + 实际值);`judge` 步由 claude 返回 pass/fail+理由。全部通过 → pass。
- **证据**:每步执行结果(命中候选 `via`、实际值、截图路径)累积成证据链回写。

### 5.3 为什么这样

- **确定性优先**:`assert_text`/`assert_status` 这类 runner 直接算,**不经 LLM**——快、稳、省、可复现(根治"claude 猜/跑偏/超时")。
- **judge 兜底**:纳米Work 这类 AI 产品,"回复是否合理"无法确定性断言,`judge` 步把**当前上下文**(而非整条用例)交给 claude,范围小、可控。
- **selectors 库当词汇表**:gui 步的 `target.key` 就是 `selectors.json` 的语义 key,复用失效自愈 + iframe 穿透。

## 6. 数据模型变更

`backend/app/core/enums.py`:
```python
class ExecKind(str, Enum):
    gui = "gui"; api = "api"; cli = "cli"
    e2e = "e2e"        # 新增:多步端到端
    manual = "manual"  # 新增:不可自动化,平台不派发
```

`backend/app/models/ai.py::TestCase`:
- `script: Text | None`(存 JSON 字符串;沿用 payload 存 Text-JSON 的既有约定,兼容 MySQL 5.6 无 JSON 列);
- `kind_reason: String | None`(AI 为何判此 kind,供人工复核参考)。

迁移:`backend/app/db/migrate.py` 加 `ensure_testcase_columns`(`ALTER TABLE test_case ADD COLUMN script/kind_reason`),
并同步 `backend/sql/schema.sql`(两份 schema 手动同步的既有约定)。**枚举扩值无需迁移**(存字符串)。

## 7. 各层详细设计

### 7.1 生成层(`claude_runner.py`)
- prompt 升级:要求 AI 为每条用例输出 `kind` + `kind_reason`,并**按 kind 产出 `script`**(结构化步骤,schema 见 §5)。
- **gui/e2e**:prompt 注入 `selectors.json` 的 key 清单(key+desc),要求 `target.key` 只能取清单内的 key;无合适 key 的元素 → 该用例降级 manual 或标注"需补选择器"。
- **判 manual 的规则**写进 prompt:无法用 gui/api/cli 步骤表达的(纯人工/探索性/主观体验)→ `kind=manual`、`script=[]`。
- `parse_testcases` 扩展:解析 `kind`/`kind_reason`/`script`,校验 script 里 action/target 合法(非法则该条降级 manual 并记原因)。
- **选择器库来源**:后端读取共享的 `selectors.json`(路径配置项 `SELECTORS_PATH`);跨组件耦合点,文档标注。

### 7.2 存储层
- 落库带 `exec_kind`/`kind_reason`/`script`;沿用现有 `_to_case_out` 手写序列化补这几个字段。

### 7.3 派发层(`exec_queue.py`)
- `enqueue`:遇 `kind=manual` → 400 拒绝(前端也置灰 manual 用例的"下发");
- payload 增加 `kind` 与 `script`(runner 要用);
- (可选)按 kind 附上"允许工具集"提示,runner 据此配 `--allowedTools`。

### 7.4 执行层(`runner.mjs`)
- 新增 **StepExecutor**:读 payload.script,按 §5 逐步执行:
  - gui/e2e 步 → 调 gui-mcp(复用现有 server;可加轻量 `wait_response`);
  - api 步 → Node fetch;cli 步 → spawn 校验;
  - `assert_*` → 确定性判定;`judge` → 调 claude(仅传该步问题 + 已捕获上下文)。
- **按 kind 配工具**:gui/e2e 的 claude 降级只给 `mcp__gui__*`;api/cli 给 `Bash`;**gui 不再给 Bash**。
- **无 script 的用例**(旧数据 / AI 没结构化):降级到现有"claude 读 steps/expected 自主执行"模式,但**带护栏**
  (SYSTEM_PROMPT 明确:只测被测产品、禁止 git/grep/Read 研究本地仓库、禁止研究"如何实现"、读不出可执行步骤就直接 fail 说明,不许反问),避免 case=1 式跑偏。
- 超时:保留总硬超时;e2e 的 `wait_response` 用**基于条件的轮询**(见"等待策略"),不是死等。

### 7.5 等待策略(e2e 关键)
- `wait_response`:发消息后轮询 `stopBtn` 消失 + `answerBubble.has-copy` 出现(选择器库已有这些 key),
  上限可配(默认 90s);超时该步 fail,不拖垮整机。避免 case=1 那种"发完干等" 撞总超时。

## 8. 分阶段落地(设计一次成型,建分阶段)

| 阶段 | 内容 | 落地即收益 |
|---|---|---|
| **P1** | 枚举加 manual/e2e;migrate 加列;enqueue 拒 manual;runner 按 kind 分工具 + 无 script 兜底护栏 | case=1 类误派/跑偏当场消失(标 manual 不派;gui 不给 Bash) |
| **P2** | 生成层:AI 判 kind + 理由;人工复核 UI 显示/可改 kind | 新用例源头带正确类型 |
| **P3** | 步骤 DSL:AI 产出 script(gui 注入 selector key);runner StepExecutor 确定性执行 + judge 降级 | 可执行用例 runner 直接跑,快/稳/省 |
| **P4** | e2e:wait_response 等待策略、多步编排 | 覆盖复杂端到端流程 |

## 9. 风险与权衡

- **AI 产出 script 的可靠性**:未必总能结构化。缓解:结构化不了就降级 manual 或 claude 兜底;人工复核可修。
- **DSL 覆盖面**:动作集 v1 有限,遇到没有的动作 → 该用例暂 manual/claude;按需扩 action(集中在 StepExecutor 一处)。
- **selectors.json 跨组件读**:后端生成要读 runner 侧的注册表文件。缓解:路径配置化 + 文档标注;后续可升级为平台托管的选择器 API。
- **两份 schema 同步**:模型 + `schema.sql` + migrate 三处(既有约定),改列时一并改。

## 10. 验收标准

- 生成一批用例,每条带 kind + 理由;不可自动化的为 manual;gui 用例的 script 只引用注册表内 key。
- manual 用例在平台**无法下发**(前端置灰 + 后端 400)。
- 一条结构化 gui 用例:runner **不经 claude** 确定性执行 assert 步,pass/fail 正确、证据链完整。
- 一条含 judge 步的用例:确定性步 runner 跑、judge 步降级 claude,整体判定正确。
- case=1 这类"功能描述"用例:被判 manual 不派;若强行下发,runner 兜底护栏使其**快速 fail 并说明"不可自动化执行"**,不跑偏、不空转 240s。
- 一条 api、一条 cli 用例各自用对应工具执行通过。
