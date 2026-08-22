# 设计:对话测评链路 · 子项 3 —— 大模型判定层

- 日期:2026-08-22
- 状态:已评审(用户 /goal 授权"剩余子项按推荐执行完";决策由 AI 自主拍板并记录于此供审)
- 所属大工程:对话测评链路(生成→下发/执行/回写→**判定**→回填/multica)
- 本 spec 范围:**仅子项 3**。读 eval_run 的会话轨迹(trace)+ 关联 eval_query.expected,用大模型判定三维(思考完整/工具·mcp调用正常/产物符预期),写 verdict_dims/verdict/verdict_reason/judged_by/is_abnormal,status→judged。**不含飞书回填/multica 推送(子项4)。**
- 依赖:子项 0(eval_run 的 verdict/verdict_dims/verdict_reason/judged_by/is_abnormal 列 + EvalRunStatus 的 judging/judged 态,均已建)、子项 2(eval_run.trace 存 trace 文件 URL,执行器已回写)。均已合入 main。
- 关联代码:`backend/app/models/ai_eval.py`(eval_run)、`backend/app/api/eval_queue.py`(判定端点加此或新建)、`backend/app/services/claude_runner.py`(判定 prompt/解析)、`backend/app/services/generators/`(复用 stream_generate)、`backend/app/core/enums.py`(EvalVerdict/EvalRunStatus)。

## 1. 背景与问题

子项 2 后,执行器把会话全过程轨迹(trace:思考/工具·mcp调用/产物/正文)回传并存磁盘,eval_run.trace 存文件 URL,status=done。但**没有东西判定这些会话质量**——子项 0 已定"大模型对完整轨迹判定三维",eval_run 也建好了判定字段,只差判定链路。

本子项建判定链路:读 trace 文件 + expected,喂大模型,产出结构化三维判定,回写 eval_run,status→judged。异常会话(判定 fail)标 is_abnormal,供子项 4 推 multica。

**关键前提**:trace JSON(子项0 §5.4)= `{session_id,run_id,thinking,tool_calls[{original_tool_name,is_mcp,args,result_text,reached_result}],artifacts[],answer,ws_captured}`;eval_query.expected = 期望产物/行为(子项1 生成时产出)。判定引擎复用 claude/deepseek(生成引擎同一套)。

## 2. 目标与非目标

**目标**
- 新增判定服务:输入(trace + expected + dimensions)→ 大模型 → 三维判定 JSON。三维:思考过程完整性 / 工具·mcp 调用是否正常 / 产物是否符合 expected。
- 新增判定触发:端点 `POST /api/eval-queue/{run_id}/judge`(单条)+ 批量判定 done 状态的 run。写 eval_run 的 verdict/verdict_dims/verdict_reason/judged_by/is_abnormal,status→judged。
- is_abnormal 规则:三维任一 fail → verdict=fail → is_abnormal=true(供子项4 推 multica)。
- 复用生成引擎(claude/deepseek 的 stream_generate,子项1/2 已参数化 prompt_builder/system_prompt),判定累积文本后解析——**零改引擎抽象层**。
- 前端:判定结果展示 + 手动触发判定入口(最小)。

**非目标(YAGNI)**
- 不含飞书回填、multica 推送(子项4)。
- 不做判定的自动触发(report 后自动判)——本子项手动/批量触发,自动化留后续。
- 不改引擎抽象层(stream_generate 已够通用);不改 exec_queue/gen_testcases/生成链路。
- 不做判定结果的复核/申诉/人工改判 UI(先能判、能看)。
- ws_captured=false(轨迹没抓到)的 run:判定时标记"轨迹缺失,仅凭 answer 判"或跳过——见 §5 降级,不额外建流程。

## 3. 关键决策(AI 自主拍板)

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 判定引擎调用 | **复用 stream_generate**(传 judge prompt_builder + judge system_prompt,累积 delta 文本后 parse)。零改引擎抽象层,与 ai_eval 生成同模式。判定不需真流式,累积一次用即可。 |
| 2 | 判定触发 | **端点 `POST /api/eval-queue/{run_id}/judge`(单条)+ `POST /api/eval-queue/judge-batch`(批量 done)**。手动/批量,不自动。用户可控判定成本/引擎。 |
| 3 | 判定归属 | 判定是**平台侧**动作(读 trace 文件 + 调引擎),用**用户 JWT + assert_project_role**(非 runner token)。区别于执行(runner)。 |
| 4 | 三维 + is_abnormal | verdict_dims={thinking_complete,tools_ok,artifact_expected} 各 {pass,note};任一 pass=false → verdict=fail → is_abnormal=true。 |
| 5 | 判定引擎记录 | judged_by 记判定用的 provider(claude/deepseek);可与执行引擎不同(判定用强模型)。 |
| 6 | 轨迹缺失降级 | ws_captured=false 或 trace 拉不到:仍判定但只凭 answer + expected,verdict_dims 的 thinking/tools 标 note="轨迹未捕获,无法判定该维",不因此崩。 |
| 7 | status 流转 | done →(judge 开始)judging →(完成)judged;判定引擎出错 → 保持 done + 记错误,不进 judged(可重判)。 |

## 4. 判定 prompt 与解析(claude_runner 新增)

### build_eval_judge_prompt(trace_obj, expected, dimensions) -> str
构造"判定对话测评会话质量"的 prompt:
- 喂给模型:①思考过程(trace.thinking)②工具调用列表(trace.tool_calls,标出哪些是 mcp、是否 reached_result、args/result 摘要)③产物(trace.artifacts)④最终答案(trace.answer)⑤期望(expected)⑥要判的维度。
- 要求模型输出**三维判定 JSON**(见 §5 结构),每维给 pass(bool)+note(简短理由)。
- 长文本截断:thinking/result_text 过长时截断(判定看要点,避免 prompt 超限)。
- **不注入** selector key / testcase 相关(判定与测试点无关)。

### EVAL_JUDGE_SYSTEM_PROMPT(常量)
中性判定 persona:"你是 AI 对话质量评审专家,严格依据提供的会话轨迹与期望,客观判定各维度是否达标。只输出要求的 JSON。"(仿 EVAL_SYSTEM_PROMPT 中性化,不带测试工程师人设。)

### parse_eval_verdict(raw) -> dict
复用 _extract_cases_array 类似的 JSON 提取(判定输出是单个对象非数组,用对应提取:```json fence / 裸 {} / salvage)。产出:
```
{
  "thinking_complete": {"pass": bool, "note": str},
  "tools_ok":          {"pass": bool, "note": str},
  "artifact_expected": {"pass": bool, "note": str},
  "summary": str  # 判定总结理由(可选)
}
```
健壮:字段缺失 → 该维 pass=None/false + note="判定未给出";非 dict → 返回全维 error。用 str() 防非字符串标量(吸取子项1 Task2 教训)。

## 5. 判定服务(`services/eval_judge.py`,新建)

`judge_run(db, run, provider=None) -> dict`:
1. 取 eval_query.expected(经 run.eval_query_id);取 trace:从 run.trace URL 反解磁盘路径读 JSON 文件(uploads/eval_traces/{...}.json);拉不到/ws_captured=false → 降级(§3 决策6),trace 用 {answer: run.answer} 兜底。
2. provider = normalize_provider(provider 或 默认);engine=get_provider;is_available 否则报错。
3. 累积判定文本:`raw=""; for evt in engine.stream_generate(<trace摘要占位>, prompt_builder=lambda: claude_runner.build_eval_judge_prompt(trace, expected, DIMS), system_prompt=claude_runner.EVAL_JUDGE_SYSTEM_PROMPT): if evt.type=='delta': raw+=evt.text; result/error 处理`。
4. dims = claude_runner.parse_eval_verdict(raw)。
5. verdict:三维任一 pass=false → EvalVerdict.failed("fail");全 true → passed("pass");解析 error → EvalVerdict.error。
6. is_abnormal = (verdict == fail)。
7. 写 run:verdict/verdict_dims(json.dumps dims)/verdict_reason(dims.summary 或拼接)/judged_by(provider)/is_abnormal;status=judged。commit。
8. 返回判定结果 dict。

判定失败(引擎错/解析 error):status 保持 done,记 verdict_reason=错误,不进 judged(可重判)。

## 6. 判定端点(`api/eval_judge.py`,新建)

**决策:新建独立 `api/eval_judge.py`**——判定是独立关注点,与下发/执行(eval_queue.py)分离,便于子项4 在判定之后接 multica。

| 方法 URL | 鉴权 | 请求 | 响应 |
|---|---|---|---|
| `POST /api/eval-judge/{run_id}` | 用户 JWT + assert_project_role(admin/member) | body {provider?} | 判定结果 dict(verdict/verdict_dims/is_abnormal) |
| `POST /api/eval-judge/batch` | 同上 | {project_id, run_ids[]?或 batch_id?} | {judged: n, results:[...]} |
| `GET /api/eval-judge/abnormal` | 用户 JWT(含 guest) | query project_id | 异常会话列表(is_abnormal=true 的 run,供子项4/人工复核) |

- 单条:取 run,校验存在/项目/status(done 才判,或允许重判 judged);调 judge_run;返回。
- 批量:取项目下 done(或指定)的 run,逐条 judge_run(容错:单条失败不断批)。
- abnormal:查 is_abnormal=true 的 run(为子项4 推 multica 与前端"异常墙"备)。
- 注册 router.py。

## 7. 前端(最小)

- api/index.js:judgeEvalRun(runId,provider?) / judgeEvalBatch(payload) / listAbnormalEvalRuns(projectId)。
- eval 执行结果页(新建 或 复用):展示 eval_run 列表(status/verdict/is_abnormal)+ "判定"按钮(单条/批量)+ verdict_dims 三维展示(思考/工具/产物 各 pass+note)。**最小实现**:在 AIEvalGen 下发后 或 新建 ExecEvalResults.vue 简单列表。本子项交付"能触发判定 + 能看三维结果"。

## 8. 迁移与 schema

- **无 schema 变更**:eval_run 的判定字段(verdict/verdict_dims/verdict_reason/judged_by/is_abnormal)子项0 已建。本子项纯逻辑。

## 9. 影响面与风险

- **隔离**:新建 eval_judge service + api,复用 stream_generate(不改引擎层)。不改 exec_queue/gen_testcases/eval 生成/下发。
- **风险1(判定质量)**:判定 prompt 是全新的,判定准确性未知。缓解:本子项交付"链路通、三维结构对",prompt 措辞可迭代;判定结果人工可复核(abnormal 列表)。
- **风险2(判定引擎成本)**:每次判定调一次大模型。手动/批量触发(非自动),用户控成本;judged_by 记录供成本统计。
- **风险3(trace 读取)**:trace 存磁盘,判定服务需能按 URL 反解路径读文件。缓解:路径从 run.trace(如 /uploads/eval_traces/xxx.json)映射回 backend/uploads/eval_traces/xxx.json,读失败降级(§3决策6)。
- **风险4(真引擎验证)**:同子项1/2,本机 claude 被 hook 污染,判定真跑无法验语义。交付"链路通、解析对"(脱机构造样例 trace + mock 引擎输出验解析/落库);真判定质量待干净环境。

## 10. 验证方式(本仓库无测试框架)

1. parse_eval_verdict:构造样例判定输出(fence/裸对象/缺字段/非dict)→ 断言解析出三维结构、缺失降级、非字符串 str() 防护。
2. judge_run:mock engine.stream_generate(yield 固定 delta=判定JSON)+ 构造样例 trace 文件 → 断言落库 verdict/verdict_dims/is_abnormal/status=judged 正确;三维任一 fail → is_abnormal=true;全 pass → false。
3. 降级:ws_captured=false/trace 文件缺失 → judge_run 不崩,verdict_dims 标轨迹缺失。
4. 端点:插 eval_run(done)→ POST judge → 查落库;batch;abnormal 列表。端点注册冒烟。
5. 前端:判定按钮 + 三维展示 npm build 过。
6. 端到端(真引擎+真trace,有环境时):下发→执行→judge→看三维。本机待验记录。

## 11. 交付清单

- [ ] claude_runner.py:build_eval_judge_prompt + EVAL_JUDGE_SYSTEM_PROMPT + parse_eval_verdict(deepseek import 复用)
- [ ] services/eval_judge.py:judge_run(读trace+调引擎+解析+落库+降级)
- [ ] api/eval_judge.py:POST /{run_id}、POST /batch、GET /abnormal + router 注册
- [ ] 前端:api 三函数 + 判定触发/三维展示(最小页)
- [ ] 手动/脚本验证(§10)

## 12. 后续子项

- 子项 4:飞书回填(搬 CLI feishu-sheet 写回到平台)+ **multica 推送(读 is_abnormal=true 的异常会话,调 multica API/CLI 创建分析任务)**。judge 产出的 is_abnormal + 分享链接是子项4 的输入。
