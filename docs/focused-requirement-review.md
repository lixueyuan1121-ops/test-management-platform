# 需求确认减负

基于 origin/main 81543465，新分支 codex/simplify-requirement-review。

- 原文依据有效、条件/操作/结果完整、场景完整且没有未解决问题的规则默认纳入。保留一次整体范围确认。
- 推测内容、未回答的冲突问题、图片不清晰和缺失场景进入待处理区；不能仅通过切换状态跳过校验。
- 规则列表支持全部、待确认、已明确、本期不测筛选，显示数量和空状态。
- 场景无需逐条勾选，不把未勾选视为内容有疑问。生成仍使用已纳入规则对应的场景，人工采纳/执行验证不变。
- 规则用“什么时候、做什么、应该看到什么”展示。新分析和场景提示词要求日常说法，保留来源原文、数字、否定和例外；已有草稿正文不会自动重新调用模型改写。
- 保存后重新评估，未解决项不进入确认版本的生成范围；页面明确显示暂不生成数量。已有确认版本仍保留原始内容。

验证：后端 focused_review、requirement_analysis、requirement_recovery、requirement_performance；前端 focused-review.test.mjs；focused-review-ui.cjs（Vite 5197，PLAYWRIGHT_MODULE 可指定本机依赖）。UI 测试使用虚构需求和请求拦截，不访问真实模型。

## 2026-09-15：部分范围生成与进度修复

- 同意只用已明确规则后，仅所选规则的完整性、关联问题及图片依据参与阻塞校验；待确认、本期不测的规则不进入生成范围，排除原因和整体确认说明为选填。资料警告仍展示。
- Claude 的完整需求理解改用单份 JSON 返回和本地严格校验，避免正文之后再生成一份结构化结果。该阶段校验失败保留原始返回，不自动从头再分析；用户可继续失败部分，已完成结果仍复用。
- 进度分开展示累计接收字数与当前内容字数；最终结果替换或格式整理不使累计值回退。分批场景的有限重试显示尝试次数，日志记录 unit_retry。
- 验证：scripts.test_review_regressions、既有需求分析/恢复/性能/进度/确认测试；frontend/tests/review-regressions-ui.cjs 覆盖 19 条明确、13 条待确认、4 条排除，以及字数替换和重试显示。

## 2026-09-15：Claude 错误信封与诊断

- 将 `assistant.isApiErrorMessage` 识别为模型错误并及时释放调用；错误提示不再计入生成正文，真实部分结果继续保留。
- 需求分析优先处理错误标记，防止带错误标记的 delta 被累计；兼容旧适配器的 API Error 提示。
- 模型调用日志增加 effort（调用参数或 inherited）、structured_output；不记录需求正文和凭据。
- 验证：scripts.test_api_error_messages、scripts.test_generation_trace、scripts.test_requirement_performance。上游长期无正文的真实超时仍可能发生，本次解析修复不能恢复上游未返回的内容。