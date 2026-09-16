# WorkBuddy 测评执行稳定性修复

## 报告与定位

报告：https://qalab.claw.qihoo.net/r/421b62d4115df60b

批次 `20260916-123431-d009` 中 WorkBuddy 共 24 条，12 条执行失败均报
`locator.click: Timeout 10000ms exceeded`，耗时约 10 秒；另有 5 条已返回答案但判定证据不足。

WorkBuddy 驱动的新建任务原来使用 `text=新建任务`，固定 10 秒点击超时。
在本机 WorkBuddy 5.5.6 复现：收起侧栏后入口变成无文字图标，旧定位超时，按钮本身可操作。
客户端代码确认 Mac 图标、Windows 标题栏图标和展开侧栏按钮均提供
`data-track-id="agent_new_task_button_clicked"`。

报告未保留 Playwright 的完整 Call log，无法逐条证明所有线上失败时的窗口状态；
本次修复覆盖已复现的故障，并补齐后续定位所需的阶段及 Call log。

## 行为调整

- 通过可见的固定按钮标识选择新建任务，同时覆盖侧栏展开/收起和 Windows 标题栏；不选隐藏按钮或项目内同名按钮。
- 新建后等待首页、空输入框和旧消息退出；失败时在发送前有限重试。无法确认新会话则停止，不能把下一题输入上一条会话。
- 发送后不自动重发。多轮任一轮发生异常，后续轮跳过并清空 trace，避免上下文错位或混入上一题证据。
- 复制消息使用每次独立的剪贴板标记，校验结构化 JSON，成功失败均清理菜单；来源面板读取后明确关闭。
- 将 WorkBuddy 原始消息中的真实工具名称、参数、返回、状态接入 trace，按 requestId 隔离轮次。同一调用去重，失败/执行中保持真实状态。
- DOM 思考区按当前轮采集，已展开的区域不反向折叠；引用来源不再伪装成成功的工具调用。

没有修改纳米 Work 的驱动、WebSocket/trace 采集、共享产物采集器或评分逻辑。
图片/音视频/文件的质量仍须相应产物证据，工具调用成功不等于产物验收通过。

## 验证

- WorkBuddy 77 项自动回归通过，包括 24 次交替展开/收起、Windows 标题栏 DOM、旧会话残留、导航恢复、菜单清理、模型选择、分享、耗时及工具证据解析。
- 纳米 Work 相关 30 项回归通过：trace、WebSocket、对话配置、多轮、分享和结果判定。另 3 项 WorkBuddy DOM 集成在同组测试中通过。
- macOS WorkBuddy 5.5.6 真机：展开和收起入口均验证成功；3 条自编自检任务成功，其中连续两条分别验证真实 Bash 调用证据和下一条工具记录为空。
- 未重跑原批次的私有附件任务，Windows 未做本次真机重放；线上成功率须部署最新 runner 后重跑原批次确认。

执行回归：

```sh
node --test tools/qalab-runner/eval/test/workbuddy-*.test.js
node --test tools/qalab-runner/eval/test/nami-trace.test.js tools/qalab-runner/eval/test/ws-trace.test.js tools/qalab-runner/eval/test/dialog-config.test.js tools/qalab-runner/eval/test/run-conversation-turns.test.js tools/qalab-runner/eval/test/conversation-share-capture.test.js tools/qalab-runner/eval/test/execution-result.test.js
```
