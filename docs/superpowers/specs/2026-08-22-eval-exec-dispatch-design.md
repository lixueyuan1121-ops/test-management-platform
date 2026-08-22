# 设计:对话测评链路 · 子项 2 —— 执行下发 + CLI 执行器改造(WS 轨迹回写)

- 日期:2026-08-22
- 状态:已评审(用户 /goal 授权"剩余子项按推荐执行完";决策由 AI 自主拍板并记录于此供审)
- 所属大工程:对话测评链路(生成→**下发/执行/回写**→判定→回填/multica)
- 本 spec 范围:**仅子项 2**。平台把 eval_query 下发到执行器 → 执行器(CLI)拉任务、驱动 work.n.cn 对话、抓 WebSocket 事件帧规整成轨迹 → 回写 eval_run(轨迹 + 分享链接 + 耗时/算力豆)。**不含大模型判定(子项3)、飞书回填/multica(子项4)。**
- 依赖:子项 0(eval_query/eval_run)、子项 1(eval_query 已可生成)。均已合入 main。
- 关联代码:平台 `backend/app/api/exec_queue.py`(下发样板)、`models/exec_queue.py`、`core/deps.py`(require_runner_ctx)、`models/ai_eval.py`(eval_run)、`db/migrate.py`;执行器 `D:\code\ai-eval-cli-yt` `bin/ai-eval.js`、`src/dialog-runner.js`、`src/desktop-pool.js`、`src/context-pool.js`、`src/feishu-sheet.js`;协议权威 `D:\code\namiwork\openclaw360-web\src\ui\AGENTS.md`。

## 1. 背景与问题

子项 1 后平台能生成对话测评 query(落 eval_query),但**没有任何东西去执行它们**。`ai-eval-cli-yt` 能驱动 work.n.cn 对话并抓分享链接/耗时/算力豆,但:①任务来源是飞书、回写目标是飞书,与平台零耦合;②**不抓会话全过程轨迹**(思考/工具·mcp 调用/产物)——而子项 0 已定"CLI 抓 WebSocket 事件帧规整成 trace 回传,判定层吃这份 trace"。

本子项把两者打通:平台成为任务源与结果汇,CLI 退化为纯执行器(拉平台任务→驱动对话→抓 WS 轨迹→回写平台),并新增 WS 帧抓取能力。

**关键前提(子项 0 深读已确认)**:work.n.cn 对话数据走一条 WebSocket(`/api/claw/v2/ws`,自定义 JSON 帧),外部 CDP 可截获(`page.on('websocket')`→`framereceived`)。事件 `{type:"event",event:"agent",payload:{stream,data,runId,sessionId,...}}`:思考=`stream:"thinking"` 的 `data.text`;工具=`stream:"tool"` 的 `data.{name,originalToolName,phase,toolCallId,args,result}`;MCP 靠 `originalToolName` 的 `mcp__<server>__<tool>` 前缀辨识;nami_panel 是信封需展开一层;同一 toolCallId 跨 start/update/result 多帧需聚合。解析逻辑可移植前端 `app-tool-stream.ts`/`app-gateway.ts`。

## 2. 目标与非目标

**目标**
- 平台新增 `/api/eval-queue`(enqueue/pending/claim/report + trace 上传),下发 eval_query、收执行结果,落 eval_run。仿 exec_queue 的 pull 轮询 + require_runner_ctx 双通道鉴权。
- eval_run 加 `target_engine` 列(被测引擎,子项 0 预留)。本子项实现 `namiwork`(work.n.cn 经 CDP)。
- CLI 新增"平台模式":拉平台任务→驱动对话(复用 DialogRunner/DesktopRunner)→**抓 WS 轨迹**→回写平台。与现有"飞书模式"并存(配置切换),不破坏 CLI 独立可用性。
- 新增 WS 帧抓取模块:挂 `page.on('websocket')`,按协议规整成 trace JSON(思考/工具·mcp/产物 + session_id),回写 eval_run。

**非目标(YAGNI)**
- **不做 codex/claude CLI 等非 work.n.cn 被测引擎**(它们无 WebSocket、是 subprocess 形态,抓取机制完全不同)。target_engine 字段预留,本子项只实现 `namiwork`。这些留作后续增量/独立子项。
- 不含大模型判定(子项3 读 trace 产 verdict)、不含飞书回填/multica(子项4)。
- 不改 exec_queue 及功能测试点执行链路(隔离,新建 eval-queue)。
- 平台前端只做**最小下发入口**(query 列表勾选→下发到执行机,见 §6.7)——否则生成的 query 无从触发执行、链路不可端到端验。不做下发历史/进度看板等富 UI(后续)。
- 不改 CLI 的飞书模式行为。

## 3. 关键决策(AI 自主拍板)

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 下发接口 | **新建 `/api/eval-queue`**(独立 router `api/eval_queue.py`),落 eval_run,仿 exec_queue。隔离,不污染功能测试点执行。 |
| 2 | target_engine | eval_run **加 `target_engine` 列**(migrate 补老库)。本子项只实现 `namiwork`。 |
| 3 | trace 存储 | **走独立 multipart 端点 `POST /api/eval-queue/{id}/trace` 存磁盘**,eval_run.trace 存文件 URL(仿 screenshot,避免 MySQL5.6 TEXT 64KB 截断——trace 含思考+多工具调用易超)。判定层(子项3)按 URL 读文件。 |
| 4 | CLI 模式 | **新增"平台模式"与飞书模式并存**(config.source='platform'\|'feishu' 或 CLI 子命令)。平台模式换任务源(拉 /api/eval-queue)+回写目标(POST 平台),执行层(DialogRunner/DesktopRunner)复用。 |
| 5 | WS 抓取 | **新建 `src/ws-trace.js`**,挂 page.on('websocket'),规整 trace。挂载点:Web 版 dialog-runner.js:45(newPage 后)、桌面版 desktop-pool.js:170(拿主 page 后)。 |
| 6 | 执行载体范围 | 本子项走 **desktop(CDP 连 Electron)** 为主(WS 抓取在真实客户端最稳,且 CLI desktop 模式已复用同一 DialogRunner);web 模式同源可带。 |
| 7 | 被测引擎范围 | 只 `namiwork`。codex/claude CLI 明确 out-of-scope(§2)。 |
| 8 | HTTP 栈 | CLI 复用 node-fetch@2(feishu-sheet.js 已用);仿 runner.mjs 的 api() 封装 + .env(BASE_URL/RUNNER_TOKEN/RUNNER_ID)。 |

## 4. 数据模型:eval_run 加 target_engine

- `models/ai_eval.py`:EvalRun 加 `target_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)`(被测引擎;本子项落 "namiwork";留空兼容)。放 device_kind 附近。
- `sql/schema.sql`:eval_run 加 `\`target_engine\` VARCHAR(32) NULL`。
- `migrate.py`:新增 `ensure_eval_run_target_engine()`(仿 ensure_eval_query_dimension,幂等 ALTER),main.py::init_db 调用。
- trace 已有 `trace` 列(Text)——改为存"文件 URL"(≤512 字,原 Text 列容得下,不改类型;语义从"内联 JSON"变"URL",注释更新)。

## 5. 平台接口(`api/eval_queue.py`,新建,仿 exec_queue)

Router 前缀 `/api/eval-queue`,`{code,msg,data}` 信封 + `ok()` + 手写 `_to_out`。

| 方法 URL | 鉴权 | 请求 | 响应 data |
|---|---|---|---|
| `POST /enqueue` | 用户 JWT + assert_project_role(admin/member) | `EvalEnqueueIn{project_id, runner, target_engine?, eval_query_ids[]}` | `{run_ids[], batch_id}` |
| `GET ""` (list_pending) | require_runner_ctx | query runner/limit | `[_to_out(r)]` pending |
| `POST /{run_id}/claim` | require_runner_ctx | query runner | `_to_out(r)`(→running) |
| `PATCH /{run_id}` (report) | require_runner_ctx | query runner;body `EvalReportIn{status, share_link?, artifact_share_link?, answer?, reported_duration?, bean_cost?, tokens?, session_id?, reason?, duration_ms?}` | `_to_out(r)` |
| `POST /{run_id}/trace` | require_runner_ctx | multipart file(JSON,≤N MB) + query runner | `{trace_url}` |
| `GET /history` | 用户 JWT(含 guest) | query project_id 等 | 历史列表 |

- enqueue:遍历 eval_query_ids,校验存在/同项目;每条建 EvalRun(eval_query_id/project_id/batch_id/runner/target_engine/device_kind=desktop/status=pending);payload 快照 query 的 prompt/attachments/dialog_options/conversation_group/turn_index(runner 执行要用,避免执行时漂移)。
- list_pending/claim/report:照 exec_queue 的 require_runner_ctx + 设备 token 锁 runner_id + 归属校验(r.runner==runner 否则 403)。
- report:status 映射 EvalRunStatus(done/failed);写 share_link/answer/耗时/算力豆等;**不含判定字段**(verdict 等留子项3)。
- trace 端点:存 `uploads/eval_traces/{run_id}.json`,回写 eval_run.trace = `/uploads/eval_traces/{run_id}.json`。
- payload `_payload_of`:{eval_query_id,title,prompt,attachments,dialog_options,conversation_group,turn_index,target_engine}。
- 注册:router.py include eval_queue.router。

## 6. CLI 执行器改造(`ai-eval-cli-yt`)

### 6.1 平台客户端 `src/platform-client.js`(新建)
仿 runner.mjs 的 api():node-fetch@2 + BASE_URL/RUNNER_TOKEN/RUNNER_ID(读 .env 或 config)+ Bearer 头 + {code,msg,data} 解封。方法:`fetchPending()`(GET /api/eval-queue?runner=)、`claim(runId)`、`report(runId, body)`(PATCH)、`uploadTrace(runId, traceObj)`(multipart POST /trace)。

### 6.2 WS 轨迹抓取 `src/ws-trace.js`(新建)
`attachWsTrace(page)`:挂 `page.on('websocket', ws => ws.on('framereceived', ...))`,累积本会话帧;`buildTrace(sessionId?)` 按协议规整(移植 app-tool-stream.ts 逻辑):
- 只认 `type:"event"` 帧;展开 nami_panel 信封一层。
- event=="agent":`payload.stream=="thinking"`→累积 data.text 到 thinking;`=="tool"`→按 toolCallId 聚合 {tool_call_id,name,original_tool_name,is_mcp(mcp__ 前缀),mcp_server,args,result_text,reached_result(phase 到 result)}。
- 产物:从 tool result / ai_output 提取(尽力;抓不到不阻断)。
- 返回子项0 §5.4 的 trace JSON:{session_id,run_id,thinking,tool_calls[],artifacts[],answer}。
- 健壮:帧解析异常单帧跳过、不崩;WS 拿不到则 trace 退化为空壳(仍回写,标记 ws_captured=false)。

### 6.3 平台模式编排(`bin/ai-eval.js` 加子命令 `platform` 或 run --source platform)
新增 action:
1. `new PlatformClient()` → 循环 `fetchPending()` 拉一批 → 逐条 claim。
2. 把平台 payload 转成 CLI 的 testCase 结构(caseId=run_id、question=prompt、attachments、conversationId=conversation_group、turnIndex),复用现有分组/执行。
3. 执行:走 desktop 模式(DesktopPool 连客户端)——page 拿到后 `attachWsTrace(page)`;每条对话跑完 `buildTrace()`。
4. 回写:`report(runId, {status, share_link, answer, ...})` + `uploadTrace(runId, trace)`。替代 feishu writeResult。
5. 复用 DialogRunner 的抓取(分享链接/耗时/算力豆)——这些仍从 DOM 抓(WS 抓的是思考/工具轨迹,两者互补)。

### 6.4 挂载点
- 桌面:desktop-pool.js:170 拿到 work.n.cn page 后 `attachWsTrace(page)`(与 :180 page.on('dialog') 并列)。
- Web:dialog-runner.js:45 newPage 后(与 :47 并列)——web 模式带。

### 6.5 配置 `.env`/config
加 BASE_URL/RUNNER_TOKEN/RUNNER_ID(平台设备 token,用户在平台"我的设备"注册);config 加 platform 段。飞书凭据在平台模式下不需要。

### 6.6 保留飞书模式
run/desktop 现有行为不变;平台模式是新增路径。不改 feishu-sheet.js 的读写。

### 6.7 平台最小下发前端(否则不可端到端验)
在对话测评 query 列表(子项1 的 AIEvalGen 结果 或 新列表页)加"勾选 → 发送到执行机"按钮,调 `POST /api/eval-queue/enqueue`(选 runner 设备)。最小实现:AIEvalGen.vue 生成结果表加多选 + 下发按钮 + runner 选择(复用 /api/devices 我的设备)。api/index.js 加 enqueueEvalQueries。

## 7. 迁移与 schema 同步

1. eval_run 加 target_engine:模型 + schema.sql + migrate `ensure_eval_run_target_engine`(老库补列,幂等)。
2. 无其它 schema 变更(trace 列复用,语义变 URL)。
3. uploads/eval_traces/ 目录:trace 端点首次写时 mkdir。

## 8. 影响面与风险

- **隔离**:新建 eval_queue router + CLI 平台模式,不改 exec_queue / gen_testcases / CLI 飞书模式。
- **风险1(WS 协议私有)**:work.n.cn 后端升版可能改帧字段。缓解:解析集中 ws-trace.js 一处,用 AGENTS.md 跟踪;trace 抓不全不阻断执行(标记 ws_captured),分享链接/答案仍从 DOM 抓保底。
- **风险2(执行环境)**:平台模式要执行器机器装 Namiwork 客户端 + 带 CDP 端口 + 平台设备 token。文档说明(部署手册补一节)。
- **风险3(trace 体积)**:复杂会话 trace 可能 MB 级。走磁盘文件(决策3)规避 DB 限制;端点限大小(如 ≤20MB)。
- **风险4(端到端验证依赖真客户端+真账号)**:同 CLI 现状,本子项交付"链路通、结构对";真跑质量验证需真环境(与子项1 smoke 同,本机 claude 被 hook 污染不影响此处——此处是 CDP 连 Namiwork,非 claude CLI)。

## 9. 验证方式(本仓库无测试框架,手动+脚本)

1. 平台侧:一次性脚本插 eval_query → 调 enqueue → 查 eval_run(pending);模拟 runner GET pending/claim/report/trace → 查 eval_run 落库(status/share_link/trace URL)、trace 文件落磁盘。
2. eval_run.target_engine 列:migrate 幂等(新库/老库/重启)。
3. CLI 侧:ws-trace.js 用录制的样例帧(或构造帧序列)喂 buildTrace,断言规整出正确 trace(思考拼接/工具聚合/mcp 辨识/nami_panel 展开)——纯函数可脱机验。platform-client 用 mock 端点验 fetch/claim/report/uploadTrace。
4. 前端:enqueue 按钮 npm build 通过 + (有环境时)手动下发。
5. 端到端(有 Namiwork 客户端 + 平台设备 token 环境时):平台下发 → CLI 平台模式拉取执行 → 回写 → 平台看 eval_run 有轨迹。本机若无环境记录为待验。

## 10. 交付清单

- [ ] 平台:eval_run 加 target_engine(模型+schema.sql+migrate+main.py)
- [ ] 平台:api/eval_queue.py(enqueue/pending/claim/report/trace/history)+ schemas + router 注册
- [ ] 平台:trace multipart 端点存磁盘 + eval_run.trace 存 URL
- [ ] CLI:src/platform-client.js(HTTP 对接)
- [ ] CLI:src/ws-trace.js(WS 帧规整 trace)+ 挂载点(desktop-pool/dialog-runner)
- [ ] CLI:bin/ai-eval.js 平台模式编排(拉→执行→WS抓→回写)
- [ ] CLI:.env/config 平台段;保留飞书模式
- [ ] 前端:AIEvalGen 下发按钮 + runner 选择 + api enqueueEvalQueries
- [ ] 手动/脚本验证(§9)

## 11. 后续子项

- 子项 3:大模型判定层(读 eval_run.trace 文件,产 verdict_dims/verdict/is_abnormal)。
- 子项 4:飞书回填(搬 CLI feishu-sheet 写回到平台)+ multica 推送(异常会话)。
- 后续增量:codex/claude CLI 等非 namiwork 被测引擎的执行(subprocess + stdout 解析,与 WS 抓取并列)。
