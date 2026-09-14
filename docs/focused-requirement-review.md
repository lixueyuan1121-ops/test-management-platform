# 需求确认减负

基于 origin/main 81543465，新分支 codex/simplify-requirement-review。

- 原文依据有效、条件/操作/结果完整、场景完整且没有未解决问题的规则默认纳入。保留一次整体范围确认。
- 推测内容、未回答的冲突问题、图片不清晰和缺失场景进入待处理区；不能仅通过切换状态跳过校验。
- 规则列表支持全部、待确认、已明确、本期不测筛选，显示数量和空状态。
- 场景无需逐条勾选，不把未勾选视为内容有疑问。生成仍使用已纳入规则对应的场景，人工采纳/执行验证不变。
- 规则用“什么时候、做什么、应该看到什么”展示。新分析和场景提示词要求日常说法，保留来源原文、数字、否定和例外；已有草稿正文不会自动重新调用模型改写。
- 保存后重新评估，未解决项不进入确认版本的生成范围；页面明确显示暂不生成数量。已有确认版本仍保留原始内容。

验证：后端 focused_review、requirement_analysis、requirement_recovery、requirement_performance；前端 focused-review.test.mjs；focused-review-ui.cjs（Vite 5197，PLAYWRIGHT_MODULE 可指定本机依赖）。UI 测试使用虚构需求和请求拦截，不访问真实模型。
