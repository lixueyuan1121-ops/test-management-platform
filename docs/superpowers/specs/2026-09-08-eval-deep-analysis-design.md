# 对话测评「深度归因分析」综合评价升级 设计

## 背景与问题

对话测评已有一套 **AI 综合评价**(`EvalTask.summary_html`,SSE 流式产出 HTML,前端 `EvalResults.vue` 展示)。当前产出的"结论"不够细致:结论停留在"相当比例用例因反复试错(多次 Bash 报错、路径/依赖/语法错误、多轮调参)",但**没有分析为何会这样、应怎么调优**;多品对比只说"哪个产品更好",没说**具体好在哪、对手能学什么**。

根因是两处:
1. **素材缺过程数据**:`_summary_items`(eval_task.py)喂给 LLM 的每条用例只有 `answer`(最终正文)+ `verdict_reason`,**没喂过程数据**——WorkBuddy 的 `raw_message`(完整思维链 reasoning + 工具调用序列)和纳米的 `trace`(tool_calls + thinking)。LLM 看不到"怎么想的、调了哪些工具、试错几次",只能泛泛而谈。
2. **prompt 太浅**:`build_eval_task_summary_prompt`(claude_runner.py)只要求"典型问题分析挑 2-5 个说根因",没要求利用过程数据做归因(试错模式识别、工具选择合理性),对比也无"向对手学习"的要求。

## 目标

升级现有综合评价(不新建功能、不改 SSE/HTML 消毒/前端),让它:
- **单品**:对失败/低分用例,从过程数据归因**为何反复试错**(报错模式、路径/依赖/语法错、多轮未收敛、工具选择错误),并给**具体可执行的调优建议**。
- **多品对比**:不只判优劣,而是从 WorkBuddy 的 `raw_message`(怎么思考、选哪些工具、如何组织结果)提炼**对手可借鉴的具体做法**。
- **工具使用质量**:工具选择是否合理、有无多余/缺失调用、调用顺序是否高效。

## 数据来源与兼容

- **WorkBuddy**:`EvalRun.raw_message` = 「复制 message」结构化 JSON,含 `messages[].content[]`(type=reasoning 的思维链)、`toolCallCount`、工具调用。已回填(见 commit c2f7ba23)。
- **纳米(namiwork)**:走 `EvalRun.trace`(磁盘 JSON,`_load_trace` 已有读取+路径遍历防护),含 `tool_calls`(每条带 name/original_tool_name/reached_result/result_text)、`thinking`。
- **兼容**:raw_message 目前仅 WorkBuddy 有,纳米靠 trace。prompt 明确告知 LLM 两种来源,**缺的不臆断**。

## 架构:三处改动(全在既有代码内)

### 1. 新增纯函数 `_extract_process_signals(raw_message, trace) -> dict`(claude_runner.py)

从 raw_message JSON / trace 提取"过程信号",供素材使用。**纯函数,不读磁盘不发网络,可单测**。产出:

    {
      "thinking_summary": str,   # 思维链摘要(WorkBuddy reasoning / 纳米 trace.thinking);头尾截断
      "tool_calls": [str, ...],  # 工具调用序列,每项如 "read_file ×1" / "bash ×3（⚠ 多次调用）"
      "tool_call_count": int,    # 总调用次数
      "retry_signal": str,       # 试错信号:如 "bash 调用 3 次,含报错重试" / "" (无)
      "source": str,             # "raw_message" | "trace" | "none"(供 prompt 说明数据完整度)
    }

**提取逻辑**(复用判定 prompt 的成熟聚合模式 `build_eval_judge_prompt` 1199-1218):
- raw_message:`json.loads` → 遍历 `messages[].content[]`,type==reasoning 的 text 拼成 thinking_summary;工具调用(tool_use/function_call 块)按名聚合次数。解析失败(非 JSON)→ 退化:thinking_summary 取原串头尾截断,source="raw_message"。
- trace:取 `trace.thinking`;`trace.tool_calls` 按 `original_tool_name||name` 聚合次数,标 `⚠ 多次调用`。
- retry_signal:任一工具次数>1 拼"X 调用 N 次";result_text 含报错关键词(error/Error/报错/失败/traceback)时标"含报错重试"。
- 两者都空 → 全空,source="none"。

### 2. `_summary_items`(eval_task.py)— 素材加过程数据

现有每条 item 追加 `process`(调 `_extract_process_signals`)。`raw_message` 取 `r.raw_message`;`trace` 复用 `_load_trace(r)`(eval_judge.py,import 或内联同款读取)。保持单一实现(SSE 端点与无头一条龙共用)。

### 3. `build_eval_task_summary_prompt`(claude_runner.py)— prompt 升级

items 每条新增 `process` 字段,渲染进用例块:

    - 思考过程:{process.thinking_summary}
    - 工具调用:{process.tool_calls 用；连接}(共 {tool_call_count} 次)
    - 试错信号:{process.retry_signal or "无明显试错"}

HTML 输出结构升级(在现有 h2 章节基础上):
- `<h2>总体结论</h2>`(保留)
- `<h2>分维度表现</h2>`(保留 table)
- `<h2>失败根因深挖</h2>`(升级原"典型问题分析"):对失败/低分用例,**结合过程数据**分析为何反复试错(报错模式/路径依赖语法错/多轮未收敛/工具选择错误),每个根因给**具体可执行调优建议**(改 prompt?补工具?调预设?)。引用用例标题。
- `<h2>工具使用质量</h2>`(新增):工具选择是否合理、有无多余/缺失调用、顺序是否高效。数据不足则说明。
- `<h2>产品横向对比</h2>`(升级,**仅多产品时输出**):除优劣结论外,**从 WorkBuddy 的思考过程/工具选择/结果组织中提炼对手可借鉴的具体做法**。各产品模型可能不同,对比整体表现不臆断。
- `<h2>亮点</h2>`(保留)
- `<h2>改进建议</h2>`(保留,要求引用具体证据)

prompt 头部说明数据来源差异:"部分产品(WorkBuddy)有完整思考过程与工具调用记录可深挖;部分(纳米)有工具调用轨迹;缺失过程数据的用例只据结果分析,不臆断。"

## 测试

- `test_extract_process_signals.py`(新):raw_message JSON(有 reasoning/有工具/多次调用)、trace(tool_calls 聚合)、纯文本 raw_message(非 JSON 退化)、两者皆空、脏数据容错。断言 thinking_summary/tool_calls/retry_signal/source。
- prompt 质量:`build_eval_task_summary_prompt` 输出含新章节指令("失败根因深挖"/"工具使用质量"/"借鉴"或"学习")、含 process 字段渲染。
- 真机:对含 WorkBuddy+纳米 run 的任务生成一次综合评价,人工看产出是否"一眼看懂根因+调优+对手可学"。

## 不改

SSE 流式(`summarize_task`)、无头一条龙(`generate_task_summary_headless`)、HTML 消毒(`_sanitize_html`/`extract_html_fragment`)、前端展示、判定链路(`eval_judge`)。仅升级素材组装 + prompt。

## 风险

- **token 成本**:过程数据增大 prompt。缓解:`_extract_process_signals` 内头尾截断;`_summary_items` 现有 per_max 单条限额机制保留。
- **raw_message 解析**:结构可能随版本变。缓解:纯函数容错(解析失败退化,不抛错)。
- **纳米无 raw_message**:预期,prompt 已说明按可得数据分析。
