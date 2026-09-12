# 对话测评模块 Review 与 Workbuddy-bench 对标

审查日期：2026-09-12。本地版本：`957bb76a`；上游 Tencent/workbuddy-bench：`625b2233093ae4f23e76be28c1f341d41cc70373`。

范围：本地对话执行、判定、复核、统计代码，以及上游执行配置、判分协议、证据、统计代码。通过内存数据库和模拟页面复现边界行为；本次未运行真实客户端任务、完整上游题库或验证线上部署。本次仅新增评审文档，未修改产品实现。

## 结论

当前平台已有真实客户端执行、多轮对话、跨产品对比、多维判定、多票判定、人工复核、重试历史和批次报告。上一轮补上的题目快照和批次隔离是继续升级的基础。

下一阶段优先提高评测结论的可信度：保证实际执行配置一致、判定上下文完整、产物可以核验、统计分母透明、结论可以追溯。Workbuddy-bench 最适合借鉴的是这些协议与统计设计；其 CLI / Docker 执行方式不能直接替代现有桌面客户端 Runner。

## 已确认的改进项

### 1. P1：判定输入缺少显式的原始任务与前序轮次

位置：`backend/app/services/claude_runner.py:1276`，`backend/app/services/eval_judge.py:68`。

`build_eval_judge_prompt` 接收轨迹、期望、维度，读取思考、答案、工具调用和产物名称，没有读取原始 prompt 或多轮 messages。虽然执行 payload 已保存题目快照，判定环节仍没有把它作为明确的任务上下文输入。

隔离复现：向 trace 同时传入“只能使用输入表的第二页，不得使用网络”和“上一轮约束不得超过100字”，生成的判定 prompt 不含两段约束。期望较笼统时，判定器无法可靠核对这些条件。

建议：增加 EvaluationContext，至少包含当前提问、当前轮次、前序用户及助手消息、输入附件信息、任务约束和期望快照。长历史按轮次裁剪并显式报告裁剪范围，保留跨轮约束。不能只靠答案自行复述原始需求。

验收：同一份答案，在不同原始约束或前序轮次下，判定输入必须正确区分；多轮引用与附件限定有独立回归用例。

### 2. P1：模型或模式切换失败后仍可能执行，横评配置不可信

位置：`tools/qalab-runner/eval/src/dialog-runner.js:337`，`tools/qalab-runner/eval/src/workbuddy-runner.js:54`。

纳米 Runner 找不到模型或模式控件时仅告警并返回；选项点击异常也会被吞掉，随后可能打印“已设置”。WorkBuddy 模型选项缺失或切换失败会沿用当前默认模型；该方法只处理 model，未处理另外两项对话选项。两者均缺少切换后的状态读回。

隔离复现：模拟找不到指定控件或模型选项，直接调用两个 Runner 的真实方法，均未抛出失败，日志表明继续使用当前状态。这会使实验记录中的请求配置与实际配置不一致。

建议：明确指定的选项必须读回验证；失败记录为配置错误，禁止进入有效横评样本。保存 requested / observed 两份配置及客户端版本、Runner 版本。不支持的选项在派发前通过能力声明校验；允许“使用当前配置”时也记录实际值。

验收：缺选项、点击无效、读回不一致均不得以指定模型名产出有效测评成绩。

### 3. P2：历史维度统计仍引用当前题目，改题会改历史

位置：`backend/app/api/eval_judge.py:289`、`:333`。

维度统计使用 `EvalQuery.dimension`，并 inner join 当前题目；没有使用已经保存的运行快照。

隔离复现：旧运行的快照维度为 tool_use，修改题目为 creativity 后，旧成绩被归入 creativity；删除题目后，运行仍在，但该统计不再返回它。

建议：以运行快照维度分组；仅旧记录缺少快照字段时回退读取题目；连接保留孤立历史运行。题库版本作为趋势对比条件。

验收：修改或删除题目不改变历史批次维度分布；总体统计与按引擎统计一致。

### 4. P2：重新判定沿用旧人工复核，且缺少判定版本历史

位置：`backend/app/services/eval_judge.py:266`。

重判覆盖 verdict、score、verdict_dims、judged_by 等字段，但不清除或重新绑定 review_mark / review_note。现有重试历史用于执行重试，未记录每次重新判定。多票模式也没有持久化全部独立票据。

隔离复现：旧结论 fail、人工标记 false_positive；重判变成 pass 后，旧 false_positive 和备注仍挂在新结论上，执行历史新增记录数为 0。复核准确率因此可能把旧反馈计入新判定器。

建议：新增不可变的 Judgment 记录，保存判定器实际模型、提示词版本、各票输出、证据版本和聚合方法。人工复核关联 judgment_id；新判定默认待复核，旧复核保留在原判定下。

验收：执行一次后连续重判两次，可查看两份独立结论；复核准确率只使用对应判定版本的反馈。

### 5. P2：通过率缺少覆盖率约束，容易误读为整体成功率

位置：`backend/app/api/eval_queue.py:638`、`:657`。

当前通过率为 pass / (pass + fail)，排除 error、未判定和未完成记录。它作为“有效判定中的通过率”是合理的，但不能单独代表端到端成功率。

隔离复现：一个批次 10 条运行，1 条 pass、9 条 error，接口返回 total=10、judged=1、pass_rate=100%。

建议同时展示：执行完成率、有效判定覆盖率、有效判定通过率、以计划执行数为分母的已确认成功占比，并单列配置错误、执行错误、证据不足和判定错误。覆盖率不足的批次提示不能直接下横评结论。平台采集失败不能直接当作模型能力失败。

验收：上述样本显示“已判定样本通过率100%，判定覆盖率10%，已确认成功占比10%”，保留9条错误的归因。

### 6. P2：算力豆汇总会截断小数，并将缺失视为零

位置：`backend/app/api/eval_task.py:61`。

结果页采集已支持小数，但汇总 `_parse_bean` 仍使用整数正则。隔离复现：23.5→23、0.8→0、1234.5→1234、None→0。

建议：使用 Decimal 或统一精度的数值；未知值保留为空；展示成本采集覆盖率。保留消耗来源和原始值，明确历史账单变动与新结果页消耗的符号转换。重复执行及重试成本分别可查，并提供总实际成本。

## Workbuddy-bench 值得借鉴的设计

| 方向 | 上游源码体现的做法 | 本平台的升级方式 |
| --- | --- | --- |
| 配置可复现 | bench / harness / model / job 分层配置解析为 manifest，并保存配置来源及 SHA256 | 批次绑定题库、输入附件、判定规则、Runner、客户端和模型配置版本；记录实际读回值 |
| 混合验证 | EvaluationPlan、JudgeSpec、EvidenceBundle、JudgeVerdict 等协议分离判分项、验证器与证据 | 规则先验证硬约束，再由 LLM 评价内容；视觉质量需要时引入 VLM；按题型配置验证器 |
| 产物验证 | 提供文件检查工具及结构化证据记录 | 下载实际产物，校验可打开、格式、页数、Sheet、公式、字段、链接可访问性；保存核验证据 |
| 重复执行 | Office 配置 n_attempts=3；统计先在题内聚合尝试，再对题目等权平均 | 新增独立 trial；同题完整执行多次，展示平均得分、成功频率和波动；失败恢复的 retry 单独处理 |
| 分母完整性 | 缺失或错误成绩处理明确；传入 expected_tasks 时，缺失任务仍纳入统计 | 派发时冻结计划样本集合；报告计划、完成、可判定和失败数量，防止失联样本从分母消失 |
| 错误分层与审计 | 区分 review、abstain、build_error、judge_error；结构化结果引用 evidence_ids | 区分能力不达标、证据不足、执行故障和判定故障，并建立判定—复核版本关联 |
| 环境隔离 | CLI / Docker 任务环境与 harness 管理 | 桌面 Runner 保留登录能力，同时显式管理新会话、工作目录、上轮产物和环境状态；记录无法重置的外部依赖 |

上表来源：[配置说明](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/configs/README.md)、[manifest 实现](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/src/workbuddy_bench/runner/resolve_manifest.py)、[判分与证据协议](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/src/workbuddy_bench/judge/core/models.py)、[产物检查](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/src/workbuddy_bench/judge/evidence/artifact_inspector.py)、[Office 配置](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/configs/bench/wb-bench-office-v1.0.yaml)、[统计实现](https://github.com/Tencent/workbuddy-bench/blob/625b2233093ae4f23e76be28c1f341d41cc70373/src/workbuddy_bench/scorer/metrics.py)。

注意两个边界：现有“3/5票”是对同一份答案多次判定，不等于上游对任务多次执行；上游 core 的 LLM / agent runner 部分仍是接口占位，具体实现依赖数据集插件，不能假设复制核心目录就得到全部验证能力。这里建议的置信区间、成本覆盖率和桌面状态管理是针对本平台的扩展建议。

## 建议实施顺序

第一阶段：修复上述六项，先让当前已有测评结果可信。尤其优先补全判定上下文和配置读回，两者直接影响横评是否成立。

第二阶段：用现有 Office / Web 题目建立产物验证试点。建议先选20～30道有明确验收条件的题，不扩大题库。每题配置硬性检查项、语义评分项及证据要求。例如“生成带公式的 Excel”：核验文件可打开、Sheet 和字段存在、关键单元格是公式、计算结果正确，再评价文字解读是否准确。当前 judge 的产物输入仅展开名称，尚不足以完成这些检查。

第三阶段：加入 trial 和冻结的实验 manifest，让每题按同一条件独立执行3次；多轮题的 trial 是完整会话组。先题内聚合，再对题等权聚合，避免重试较多的题权重变大。报告同时给出质量、可靠性、耗时及成本，A/B 限定同题库版本和可比配置。小样本给出不确定性提示，置信区间可作为后续增强。

建议数据关系：Batch / Experiment → Trial（执行）→ Judgment（判定）→ Review（人工复核），Artifacts / Evidence 被 Trial 与 Judgment 引用；执行重试和重判各自留历史。这能在现有队列与真实客户端采集能力上增量演进。
