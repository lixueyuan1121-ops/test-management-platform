# 重复元素定位与执行效率

## 操作前消除匹配冲突

同名 key、重复 testid 和可复用 CSS 都不代表唯一元素。按当前业务状态依次收窄：实际 frame → 当前 Tab/弹窗/列表容器 `within` → 具体控件 → 文本。

`has_text` 是包含匹配；区分“视觉PPT制作”和“视觉PPT制作企业版”时使用 `has_text:"视觉PPT制作", has_text_exact:true`。完整文本匹配规范首尾及连续空白，大小写敏感，字面量不按正则执行。过滤作用于整个目标文本；含额外按钮/介绍的卡片应先定位标题，或用已观察到的标题子节点约束所属卡片，例如 CSS `:has(.真实标题类:text-is("业务名称"))`。所有类名、属性和 frame 都须先观察，不照搬示例。

不要生成 `xpath=//*[contains(normalize-space(.), '视觉PPT制作')]` 作为点击目标：它会匹配 html/body、祖先容器和真实控件，追加相同 has_text 仍不唯一。改为已观察到的控件选择器，再加文本/作用域。以下仅展示字段结构，testid 和 frame 必须现场核实：

```json
{"action":"click","target":{"selector":"[data-testid=\"skill-card-title\"]","has_text":"视觉PPT制作","has_text_exact":true,"within":{"selector":"[data-testid=\"my-skills-panel\"]","frame":"vm"}},"desc":"点击当前我的技能列表中的视觉PPT制作卡片标题"}
```

在上一导航步骤完成后检查当前目标，不在首页检查尚未出现的深层页面元素。直接复用 guiCore 时调用 `await gui.inspectTarget(target)`；通过 Codex/Claude GUI MCP 时调用 `gui_inspect`，读取 `unique`、`match_count` 和最多20条标签、文本、frame。检查不点击、不修改业务数据。`unique=true` 后再操作；仍重复时依据真实 DOM 补充容器/角色/稳定属性。检查与点击之间 DOM 可能变化，最终操作继续严格检查唯一性。

仅需求明确“首张/第 N 条”时使用 nth，限定真实列表、验证非空，并在步骤说明序号语义；不把 nth=0 当作通用消歧，不删除同名注册表记录或断言来提高通过率。

旧 Runner 不支持 `has_text_exact` / `gui_inspect` 时先升级 Runner；若不能升级，使用现场验证的精确 CSS `:text-is(...)`、精确文本定位或限定元素类型的 XPath，不能仅更新 skill 后假定执行器认识新字段。

`has_text_exact` 与对应 `has_text` 写入最终 target（包括 within），参与执行指纹。它们不同于输入内容 `args.text` 和断言预期 `args.expected`。修改最终 DSL 后完整重跑，重新生成实际报告及执行指纹；旧失败用例不会随技能升级自动改变。

## 确定性脚本优先

完整 script 交给 StepExecutor，保留业务等待、复位和截图证据，不为“使用 Codex”把每一步改成模型调用。Runner 支持 `GUI_AI_ENGINE=codex` 时，动态前置导航、judge 或无脚本兜底可以使用 Codex；同样必须有真实定位和断言证据。

不要把“账号已登录、位于任意页面”等环境描述写成自然语言 precondition，触发多余模型导航。能确定性实现的导航放进 script。完整最终脚本连续执行两次验证独立性；不只在 Codex 对话里手动点通一次就回填。

新 Runner 默认将执行队列空闲检查间隔缩短为 min(POLL_MS,1000) 毫秒，可用 EXEC_POLL_MS 覆盖；探测/perf 保持原 POLL_MS 节奏，一批完成后立即检查下一批。网络、客户端准备和正在执行的任务仍会增加等待，不能将轮询间隔变化宣称为整条用例的提速比例。使用 execution_timings 区分启动、复位、脚本、模型和上传耗时。
