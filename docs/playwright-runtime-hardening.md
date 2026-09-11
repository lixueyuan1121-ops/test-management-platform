# Playwright 执行与导出优化

执行设备、GUI MCP 与 Playwright 导出共用 `backend/app/services/playwright_runtime.mjs`。
后端直接嵌入该文件；runner 分发包把它放到 `gui-mcp/playwright-runtime.mjs`，源码工作区通过
`runtime-loader.mjs` 读取原文件。修改核心语义只需改一处，不需要生成或维护第二份实现。

## 定位和断言

- 单目标操作及文本/可见断言默认要求唯一匹配。候选按稳定类型优先、同类保留登记顺序；不用 `.or().first()`。
- 重复控件用 `target.within` 指定所属记录，用 `has_text` 缩小范围；确实按顺序操作才显式指定从 0 开始的 `nth`。
- `frame: shell/vm/url:子串` 与原始 CSS 都走统一作用域。指定 URL frame 必须唯一，不能回落到另一个 frame。
- XPath 候选与录制功能兼容；输入框、文本域和选择框按当前 value 读取与断言。
- 文本/可见/消失断言在 `args.timeout_ms` 内重试，默认 10 秒。消失断言检查全部匹配及候选；未知 key、无效 CSS、查询异常不算消失。
- 有效定位查询后未出现预期 UI，属于业务断言失败；定位歧义、无效配置及查询异常属于执行阻塞。
- `press` 使用目标 Locator 发键，兼容 `key` 和 MCP 的旧 `target_key`。自定义输入组件只向唯一可编辑子节点填入，避免追加到错误输入框。
- 无断言的结构化用例在操作前被拒绝。定位失败后的自学习候选进入评审，不能直接改变本条测试目标或结果。

```json
{
  "action": "click",
  "target": {
    "key": "taskMenuButton",
    "within": {"key": "taskRow", "has_text": "任务 A"}
  }
}
```

容器 key、回复信号 key 也计入选择器引用统计、缺失检查与删除影响范围。

## 回复等待

结构化脚本把 `wait_response` 放在提交用的最后一个 `click` / `press` 之后，可以在中间插入
`wait_for`。执行器在提交前记录完成标志数量，等待生成状态结束且本轮新增的完成标志可见。
旧消息重新变得可见不会被当成新回复。每次等待消费其基线，失败或下一条用例不会复用。

默认兼容 `stopBtn` / `abortButton` 和 `answerBubble` / `chatMsgActions`；不同产品可用
`args.stop_key`、`args.complete_key` 指定已登记信号。完成信号必须确实表示 AI 回答完成，
不能用用户气泡或普通消息容器代替。若页面虚拟化会复用或移除旧完成标志，应提供适配该页面的完成信号，
并做实际客户端冒烟验证。

MCP 自由执行时先 `gui_capture_response`，再提交，最后 `gui_wait_response`，两次使用相同信号配置。
AI 兜底和前置导航进程接收当前项目选择器快照及 CDP 地址，不再单独读取内置表。

## 执行追踪和复位

`GUI_TRACE=failures`（默认）录制复位及执行，失败才保存；`all` 保存全部，`off` 关闭。
报告提供“下载执行追踪”，下载后可用 Playwright Trace Viewer 查看。上传失败会保留执行机本地文件并报告路径；
录制失败也会显示原因，不覆盖业务断言结果。

Trace 单独存于后端 `artifacts/exec-traces/`，不放公共静态目录。上传校验执行机归属，下载校验项目访问权限；
上限 100MB，随新上传惰性清理超过 `EXEC_SHOT_RETENTION_DAYS`（默认 14 天）的文件。

复位及重启后都必须通过首页就绪检查。仅点击过首页、锚点缺失或检查异常都不能放行。
跨项目切换会清理注册表/回复基线的遗留状态，Mock 清理失败会阻塞执行。

刷新和回首页不等于清空服务器数据或浏览器持久存储。用例仍需准备独立业务记录并显式清理自己创建的数据，
不能通过删除整个已登录客户端配置来伪造隔离。主进程、IPC 和原生窗口测试仍属于独立测试层。

## 导出与验证

导出保留否定断言、多行输入、作用域、候选优先级、回复基线和 Mock 命中检查。
未支持动作（如依赖外部模型判定的 `judge`）直接拒绝导出，不生成跳过检查后仍可通过的 TODO 模板。
独立导出运行前需要恢复与平台相同的登录态、首页和业务数据，同一客户端必须串行运行。

本地测试命令（仓库根目录）：

```sh
node --test tools/qalab-runner/gui-mcp/*.test.mjs tools/qalab-runner/step-executor.test.mjs tools/qalab-runner/reset-home.test.mjs tools/qalab-runner/exec-trace.test.mjs tools/qalab-runner/precond-nav.test.mjs tools/qalab-runner/record-capture.test.mjs
```

浏览器测试使用独立 Context 和合成页面，不连接真实产品。需要安装与 Playwright 配套的 Chromium；
也可以通过 `PLAYWRIGHT_TEST_EXECUTABLE` 指定已安装的无头 Chromium。

后端在 `backend/` 内运行：

```sh
.venv/bin/python -B -m scripts.test_playwright_export
.venv/bin/python -B -m scripts.test_export_playwright_api
.venv/bin/python -B -m scripts.test_exec_trace
.venv/bin/python -B -m scripts.test_script_target_contract
.venv/bin/python -B -m scripts.test_selector_usage_delete
.venv/bin/python -B -m scripts.test_retry_enqueue
.venv/bin/python -B -m scripts.test_record_flow
.venv/bin/python -B -m scripts.test_edit_script_selector_fix
```

上线时需要同步后端、runner 分发文件及前端产物。当前改动不需要数据库迁移；设备必须取得新版 runner 包。
旧用例若依赖“多匹配自动取第一项”或“点击首页后无条件放行”，需要补定位范围或就绪锚点。
