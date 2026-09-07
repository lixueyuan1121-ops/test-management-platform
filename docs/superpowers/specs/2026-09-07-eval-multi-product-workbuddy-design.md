# 设计:对话测评链路 · 多产品横评——接入 WorkBuddy 与纳米Work 对比

- 日期:2026-09-07
- 状态:待评审
- 所属大工程:对话测评链路(生成→下发/执行/回写→判定→回填/推送)。**本工程新增"被测产品"维度,让同一套测评题+同一套标准同时测多个 AI 办公产品并横向对比。**
- 首个对比对:**纳米Work(work.n.cn/openclaw 系)** vs **WorkBuddy(腾讯,`com.tencent.workbuddy.mac`,Electron v5.5.3)**。
- 关联代码:`backend/app/models/ai_eval.py`(EvalRun.target_engine 已存在,复用)、`backend/app/api/eval_task.py`(dispatch fan-out 加产品维度)、`backend/app/api/eval_queue.py`(enqueue/挑机)、`backend/app/services/dispatcher.py`(挑机按 engine)、`backend/app/services/eval_judge.py`(判定层零改动 + 对比统计加 by_engine)、`backend/app/services/eval_pipeline.py`(结果汇总加产品对比)、`frontend/src/views/EvalTasks.vue`+`EvalResults.vue`、CLI `tools/qalab-runner/eval/`(新增 WorkBuddy 执行器 + DOM 抓取)。

## 1. 背景与问题

平台已有完整的对话测评链路,但**被测对象只有纳米Work 一个**。用户诉求:用**同一套测评题**发给 WorkBuddy,**回收对话内容**,用**同一套评测标准**判定,得出 **WorkBuddy vs 纳米Work 的对比结果**。

这套链路的分层天然支持多产品——四段里三段产品无关:

| 层 | 载体 | 与被测产品的关系 |
|---|---|---|
| ① 出题 | `eval_query` 表 | **产品无关**,同一批题直接复用 |
| ② 执行/回收 | CLI 执行器 + `eval_run` | **产品相关**,纳米Work 死绑 work.n.cn WS 帧,WorkBuddy 需新执行器 |
| ③ 判定 | `eval_judge` + prompt 常量 rubric | **产品无关**,消费规整后的统一 trace JSON |
| ④ 对比 | `eval_run` 按 batch 聚合 | **缺产品分组维度**,现有 A/B 比的是同产品两配置 |

**关键支点**:`eval_run.target_engine` 字段(`models/ai_eval.py` L73)当初就为多产品预留(注释:`namiwork/codex/claude...本阶段只实现 namiwork`),`_to_out`/`enqueue`/`dispatch_task_runs` 已透传该字段。本工程把这个"埋好的坑位"真正启用。

## 2. WorkBuddy 客户端实测结论(2026-09-07 真机 spike,非推测)

本机 `/Applications/WorkBuddy.app` 启动带调试端口 + Playwright `connectOverCDP` attach,全部坐实:

- **attach 开关**:环境变量 `WORKBUDDY_REMOTE_DEBUGGING_PORT=<端口>`(客户端主进程 `appendSwitch("remote-debugging-port",...)` + `remote-allow-origins`)。默认允许 origin 含 `http://127.0.0.1`、`http://localhost`,Playwright attach **默认放行**,无需额外配置。
- **UI 形态**:本地 `app.asar/renderer/index.html`,**单 page 单 frame**(比纳米Work 的远程 iframe 嵌套简单)。登录态持久(spike 时已登录)。
- **输入框**:`div[contenteditable="true"][role="textbox"]`(类名 `_editable_xxx` 是 CSS Module 哈希,**不可用作选择器**,用语义定位)。
- **发送**:`Enter` 直接发出。
- **回答**:渲染在 `conversation-*` 容器,可从 DOM 抓到正文。
- **元信息**:`conversation-finished-footer` = 「共消耗 8.39 / 均衡 (Deepseek-V4-Pro) / 时间」——**消耗、模型、时间都在 DOM**。
- **⚠️ 对话数据走主进程 HTTP,渲染进程 CDP 截不到**:spike 挂 CDP Network 抓不到任何 `lkeap.cloud.tencent.com/messages` 请求(只有本地 asar JS)。WorkBuddy 的对话请求由主进程(Node 侧)发出经 IPC 回渲染进程。**结论:WorkBuddy 的 trace 必须抓 DOM,不能截流**——这是与纳米Work(渲染进程 WS 帧,CDP 可截)的根本差异。
- **无 vm/多设备概念**:WorkBuddy 无 `clawDeviceService`,`target_device` 对 WorkBuddy 恒空、切设备逻辑跳过。
- **模型可配(实测坐实)**:输入工具栏 `cr-input-toolbar` 内,挨着语音输入(`button.cr-voice-trigger`)有模型下拉 `button.cr-model-selector__trigger`(aria-label=`Select model`),选项 `.cr-model-selector__item` 含 快速(0.21x)/均衡(0.65x,默认)/极致(1.20x)/Hy4 preview/Hy3/GLM-5.3 等(带算力倍率)。选择器为稳定业务类名(非哈希)。→ **`dialog_options.model` 对 WorkBuddy 天然适用**,执行器点开 trigger 按 model 选对应 item 即可,无需新字段。

## 3. 关键决策(已与用户确认)

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 驱动方式 | **CDP 驱动客户端 UI 完整路径**(不走底层 HTTP 直连),测真实用户体验,与纳米Work 口径一致,对比才公平 |
| 2 | trace 抓取源 | **抓 DOM**(WorkBuddy 主进程发 HTTP,渲染进程截不到流);规整成与纳米Work **同结构** JSON |
| 3 | trace 完整度 | **四块全抓齐**(thinking/tool_calls/artifacts/answer)+ **任务耗时**,与纳米Work 对齐,对比维度最全 |
| 4 | 产品选择粒度 | **任务级勾选产品**:测评任务上选测哪几个产品,下发时每题对每个勾选产品各 fan-out 一条 run |
| 5 | 同机并行 | **分机跑**:纳米Work 与 WorkBuddy 分派到不同执行机,靠现有派单机制自然隔离(不做同机双客户端并行) |
| 6 | 数据模型 | **复用 `eval_run.target_engine`**,不新建字段;新增取值 `workbuddy` |
| 7 | 判定/标准 | **零改动复用** `eval_judge` + rubric prompt 常量(前提:trace 规整成统一结构) |
| 8 | 对比展示 | 现有 A/B(同产品两配置)之外,**新增按 `target_engine` 分组**的跨产品对比(通过率/均分/逐维/逐题并排) |

## 4. 数据模型(几乎零改动)

### 4.1 `eval_run.target_engine` 启用多值(已存在,不迁移)
- 字段已在 `models/ai_eval.py` L73(`String(32) NULL`)。新增取值 `"workbuddy"`,纳米Work 沿用 `"namiwork"`。
- **无需迁移**:`String` 列加字符串值老库直接兼容(非原生 ENUM)。
- `schema.sql` 该列已存在,无需改。

### 4.2 `EvalTask` 加 `target_engines`(任务级勾选的产品集合)
- 新增列 `target_engines: Mapped[str | None]`(`Text`,JSON 数组如 `["namiwork","workbuddy"]`;NULL/空=仅 namiwork,向后兼容)。
- **迁移**:`migrate.py` 加 `ensure_eval_task_target_engines()`(仿现有 `ensure_*` 模式),startup 调用;`schema.sql` 的 eval_task 段补该列。
- 理由:产品选择是任务的属性(决策 4),存任务上,再执行/定时回归都复用同一组产品。

### 4.3 引擎注册表(新增,产品无关抽象)
新建 `backend/app/services/eval_engines.py`:定义被测产品注册表(YAGNI:仅存展示名 + 是否需要 target_device + 默认 device_kind),供前端渲染产品勾选、后端校验 engine 合法性。
```python
EVAL_ENGINES = {
    "namiwork":  {"label": "纳米Work",  "needs_device": True,  "device_kind": "desktop"},
    "workbuddy": {"label": "WorkBuddy", "needs_device": False, "device_kind": "desktop"},
}
```
`GET /api/ai/eval-engines` 下发给前端(仿现有 `/api/ai/eval-dimensions`)。

## 5. 执行层:新增 WorkBuddy 执行器(最大工程量)

> **术语澄清(重要)**:本文"执行器/CLI"一律指**执行机上的 Node 程序** `tools/qalab-runner/eval/bin/ai-eval.js`(由 `run-eval.sh` 启动),它**通过 CDP 连接并驱动被测产品的 Electron 客户端**——纳米Work 与 WorkBuddy 走的都是这条 CDP 路径。**不是**指 WorkBuddy 自带的 `codebuddy` 命令行工具(那条终端 REPL 路径已否决,不采用)。WorkBuddy 接入 = 在这个 Node 程序里新增一个"CDP 驱动 WorkBuddy 客户端"的分支。

CLI 仓库 `tools/qalab-runner/eval/`。纳米Work 执行器是 `desktop-pool.js`(CDP 连 Electron)+`desktop-runner.js`(驱动对话)+`ws-trace.js`(截 WS 帧)。WorkBuddy 复用 CDP 连接骨架(spike 已验证 `WORKBUDDY_REMOTE_DEBUGGING_PORT` attach 可行),但对话驱动与 trace 抓取要新写。

### 5.1 复用与新增划分

| 组件 | 纳米Work | WorkBuddy | 处置 |
|---|---|---|---|
| CDP 连接骨架 | `desktop-pool.js`(spawn+connectOverCDP+waitPort) | 同套(换 env `WORKBUDDY_REMOTE_DEBUGGING_PORT`+executablePath) | **抽公共基类/工具**,两产品共用 |
| 设备切换 | `switchTo(vm)` clawDeviceService | 无 vm 概念 | WorkBuddy 跳过 |
| 对话驱动 | `desktop-runner.js`+`dialog-runner.js` | 新增 `workbuddy-runner.js`(输入框/发送/dialogOptions 选择器不同) | **新写** || trace 抓取 | `ws-trace.js` 截 WS 帧 | 新增 `workbuddy-dom-trace.js` 抓 DOM | **新写**,输出同结构 |
| 分流 | `bin/ai-eval.js` platform 命令 | 按 `run.target_engine` 路由到对应 runner | **改** |

### 5.2 执行器分流(`bin/ai-eval.js`)
- `runOnce` 拉到 pending run 后,按 `item.target_engine` 分流:`namiwork`→现有 DesktopRunner;`workbuddy`→新 WorkBuddyRunner。
- 一台执行机**只处理它能跑的 engine**(见 §6 挑机);混到不认识的 engine → fail-closed 回写 failed(reason:该执行机不支持引擎 X),不裸跑。
- config 抽产品相关项:`chatUrl`/`executablePath`/`expectedAgentName`/env 变量名按 engine 选择(现 `default.config.js` 死绑纳米Work,拆成 per-engine 配置块)。
- **WorkBuddyRunner 的 `_applyDialogOptions`**:按 `payload.dialog_options.model` 点开 `button.cr-model-selector__trigger` → 选匹配的 `.cr-model-selector__item`(选择器已坐实);未指定则用当前默认档。选中后从 `conversation-finished-footer` 读回实际模型名记进 trace 元信息(供报告标注,亦作选择成功校验)。

### 5.3 WorkBuddy trace 抓取(`workbuddy-dom-trace.js`)
产出与 `ws-trace.js` **完全同结构**的 JSON(判定层不感知产品差异):
```json
{
  "session_id": null,          // WorkBuddy 无会话 UUID 可用轮次/对话 id 兜底或留空
  "thinking": "思考过程正文",   // 抓思考区 DOM(触发思考的对话才有)
  "tool_calls": [ {"name","original_tool_name","is_mcp","mcp_server","args","result_text","reached_result"} ],
  "artifacts": [ {"name","kind","share_link"} ],
  "answer": "最终回答正文",
  "ws_captured": false,        // WorkBuddy 恒 false(非 WS);另加 dom_captured:true 标来源
  "reported_duration": "任务耗时(秒)",  // 决策3:抓 conversation-finished-footer 的耗时/消耗
  "bean_cost": "消耗(8.39)",
  "model": "均衡 (Deepseek-V4-Pro)"
}
```
- **DOM 选择器策略**:多候选 + DOM dump 兜底(沿用 `attachment-ready.js` 的既有做法),因 CSS Module 哈希类名会变版。思考/工具/产物三块的具体选择器**在实现时真机联调坐实**(需发触发思考+工具+产物的 prompt);先落 answer + 元信息(spike 已验证可抓),其余按同结构补齐。
- **抓取时机**:等回答完成信号(`conversation-finished-footer` 出现 = 本轮结束),再抓全量 DOM,避免抓到流式中间态。

### 5.4 回写链路(复用)
- `reportRun` 顺序不变:**先 uploadTrace 落盘,再 report(done)**(否则一条龙判定在 trace 落盘前跑,空壳判分偏低——既往教训)。
- `eval_run` 回写字段全部复用:`answer`/`trace`(URL)/`reported_duration`/`bean_cost`/`session_id`/`reason`。WorkBuddy 的 `share_link`/`artifact_share_link` 若无分享能力则留空(判定不依赖分享链,依赖 trace)。

## 6. 派单与挑机:分机跑(改 `dispatcher.py` + `eval_task.py`)

### 6.1 执行机声明支持的 engine
- 现有 `runner_device` 表按运行时感知 func/eval(`last_exec_at`/`last_eval_at`)。多产品需再分一层:**这台 eval 机跑的是哪个产品**。
- 方案:执行机上报时带 `engine`(CLI `.env` 加 `EVAL_ENGINE=namiwork|workbuddy`,platform 命令上报到心跳)。落 `runner_device` 新增列 `eval_engine: Mapped[str|None]`(migrate 补列;NULL=兼容老机,视作 namiwork)。
- `online_eval_runners(db)` 增补按 engine 过滤:`online_eval_runners(db, engine="workbuddy")` 只返回声明跑 WorkBuddy 的在线机。

### 6.2 dispatch fan-out 加产品维度(`dispatch_task_runs`)
现有 `variants=[("A",opts),("B",opts_b)]` 是 A/B 维度。多产品是**正交的另一维**:
- 读 `task.target_engines`(勾选的产品),对每个 engine × 每个 A/B variant × 每题 → 一条 run。
- 每条 run 的 `target_engine` 落对应值;挑机时**按 engine 分别 `_resolve_runners`**(engine=workbuddy 的 run 只分给 WorkBuddy 机,engine=namiwork 的只分给纳米Work 机)。
- **会话组隔离再加一层**:多轮 `conversation_group` 已加 `#A/#B`,跨产品须再加 engine 前缀(如 `namiwork::grp#A`),否则不同产品的同名组会被误判同机连发。
- `batch_id` 仍是一个(同批横评);结果页按 `target_engine` + `compare_group` 双维聚合。

### 6.3 挑机失败处理
- 勾了 workbuddy 但无在线 WorkBuddy 机 → 该产品的 run 建成 pending 但无机可拉(或下发时告警"WorkBuddy 无在线执行机,已跳过/排队")。**决策**:下发前校验每个勾选 engine 至少一台在线机,否则 400 明确报"引擎 X 无在线执行机";避免建一堆永不执行的 pending。

## 7. 判定层:零改动(复用 §3 决策 7)

- `eval_judge.judge_run` 消费 `run.trace`(统一结构 JSON)+ `EvalQuery.expected`/`dimension`,调 rubric prompt 常量判三维 + 1-5 分。**产品无关,一行不改。**
- 前提:§5.3 的 WorkBuddy trace 必须规整成同结构。若某块(如 tool_calls)WorkBuddy 抓不到而为空,判定 prompt 已有"思考未捕获/截断"防误判规则(`claude_runner.py` build_eval_judge_prompt),不会把"没抓到"误判成"没做"——但这会影响该维得分公平性,故决策 3 要求四块全抓齐。

## 8. 对比展示:新增按产品分组(改 `eval_judge.py` 统计 + `EvalResults.vue`)

### 8.1 后端统计加 `by_engine`
- `GET /eval-judge/dimension-stats`:现按 `dimension` 聚合,加 `group_by=target_engine` 选项 → 每产品逐维通过率。
- `GET /eval-queue/trend`:批次趋势加按 engine 拆线(纳米Work vs WorkBuddy 两条通过率/均分线)。
- 新增 `GET /eval-tasks/{id}/engine-compare`(或复用 pipeline `_result_summary`):同批内按 `target_engine` 分组算 `engine_line`(各产品通过率、均分、逐维得分),仿现有 `ab_line`(`eval_pipeline.py` `_result_summary`)。

### 8.2 前端对比视图(`EvalResults.vue`)
- 复用现有 A/B 并排弹窗 `openAbCompare` 的形态,新增**产品并排对比**:左右分栏从"A 配置/B 配置"换成"纳米Work/WorkBuddy",同题对照回答+判定+得分。
- 结果列表加"产品"列/筛选(`target_engine`),维度通过率条按产品分组渲染。
- 综合评价(`summary_html`):`build_eval_task_summary_prompt` 喂入时带产品分组,让 AI 总评里包含横向对比结论(prompt 增补,非结构改动)。

## 9. 影响面与风险

- **隔离性好**:①②③④ 中判定层零改动、数据模型仅加 2 列(1 迁移)+复用 target_engine;主要新增在 CLI 执行器(独立新文件)与前端对比视图。
- **风险1(DOM 选择器脆弱)**:WorkBuddy CSS Module 哈希类名随版本变。缓解:语义选择器(role/contenteditable/稳定业务类名如 `conversation-finished-footer`)+ 多候选 + DOM dump 兜底 + 抓不到 fail-closed 记 warning(沿用纳米Work 附件抓取的既有教训)。
- **风险2(trace 四块抓全需真机迭代)**:思考/工具/产物 DOM 结构 spike 未覆盖(闲聊没触发)。缓解:实现期发触发型 prompt 真机联调逐块坐实;先 answer+元信息保底(已验证),其余增量补。
- **风险3(挑机复杂度)**:多了 engine 维度,挑机/会话组隔离要同时正确。缓解:engine 前缀进会话组 key;下发前校验在线机;充分脚本验证 dispatch 分配。
- **风险4(对比公平性)**:两产品**模型均可选**(纳米Work 与 WorkBuddy 都经 `dialog_options.model` 配置;WorkBuddy 下拉档位 快速/均衡/极致/Hy4/Hy3/GLM 等已坐实)。因此对比可做两档:①**同档受控对比**(两边都选中档,比"相近算力下谁强");②**默认档对比**(各用产品默认,比"开箱体验")。计费口径不同(WorkBuddy 按算力倍率),报告统一注明各产品实际用的模型(元信息 `conversation-finished-footer` 已抓)。任务下发时 A/B 的 `dialog_options`/`dialog_options_b` 可分别为两产品指定档位。
- **风险5(登录态维护)**:WorkBuddy 客户端登录态过期需人工续。与纳米Work 同类问题,执行器检测未登录 fail-closed 并告警。

## 10. 交付清单

- [ ] `models/ai_eval.py`:`EvalTask.target_engines` 新列;`migrate.ensure_eval_task_target_engines()` + startup 调用;`schema.sql` 补列
- [ ] `models/runner_device`:`eval_engine` 新列 + migrate 补列 + schema.sql
- [ ] `services/eval_engines.py`:引擎注册表 + `GET /api/ai/eval-engines`
- [ ] `services/dispatcher.py`:`online_eval_runners(engine=...)` 按 engine 过滤;心跳记 eval_engine
- [ ] `api/eval_task.py`:`dispatch_task_runs` 加产品维度 fan-out + 按 engine 挑机 + 会话组 engine 前缀;`EvalTaskCreate/Update` 收 target_engines;下发前校验在线机
- [ ] `api/eval_queue.py`:enqueue 直发也支持指定 engine(与任务级一致)
- [ ] `services/eval_judge.py`:dimension-stats/trend 加 by_engine;`eval_pipeline._result_summary` 加 engine_line
- [ ] CLI `tools/qalab-runner/eval/`:抽 CDP 连接公共层;`workbuddy-runner.js`(驱动);`workbuddy-dom-trace.js`(DOM 抓取,四块+耗时);`bin/ai-eval.js` 按 target_engine 分流;per-engine config;`.env` EVAL_ENGINE
- [ ] `run-eval.sh`:支持 WorkBuddy 执行机启动(env 变量)
- [ ] 前端 `EvalTasks.vue`:任务编辑加产品勾选(拉 /eval-engines);`EvalResults.vue`:产品并排对比 + 产品筛选/分组;`api/index.js` 加 listEvalEngines
- [ ] 综合评价 prompt 增补产品横评口径
- [ ] 验证(§11)

## 11. 验证方式(本仓库无测试框架,手动+脚本)

1. **数据模型**:startup create_all/migrate 后确认 eval_task.target_engines、runner_device.eval_engine 建出;插入含多 engine 的任务读回。
2. **dispatch(脚本)**:构造 target_engines=["namiwork","workbuddy"] + A/B 的任务,跑 `dispatch_task_runs`,断言每题生成 engine×variant 条 run、target_engine 落对、会话组 key 带 engine 前缀、按 engine 分到对应机。
3. **挑机**:模拟一台 namiwork 机 + 一台 workbuddy 机在线,断言各 engine run 只落对应机;无对应在线机时下发 400。
4. **WorkBuddy 执行器(真机)**:CLI platform 拉一条 workbuddy run → attach 客户端 → 发对话 → 抓 DOM trace(四块+耗时)→ 回写。发触发思考+工具+产物的 prompt 坐实四块。
5. **判定复用**:WorkBuddy run 的 trace 走 `judge_run`,确认三维+评分正常产出(不因产品不同报错)。
6. **对比展示**:同批双产品结果,EvalResults 产品并排弹窗左右对照、by_engine 统计正确;综合评价含横评结论。
7. **端到端**:一个任务勾两产品下发 → 两台机分别执行 → 判定 → 综合评价 → 报告短链含 WorkBuddy vs 纳米Work 对比。

## 12. 后续(本 spec 之外)

- 第三个产品(如 codex/claude):加 EVAL_ENGINES 一项 + 一个执行器 + 一台机,数据/判定/对比零改动——本设计的复用红利。
- WorkBuddy 多轮会话、附件上传、分享链抓取:本期先覆盖单轮+基础多轮;附件/分享按纳米Work 既有能力增量。
