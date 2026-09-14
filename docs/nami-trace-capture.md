# 纳米 Work 对话测评过程采集

## 2026-09-14 故障与原因

在 macOS 纳米 Work 3.14.0 上复现：对话页面已连接且可正常执行，旧 `page.on('websocket')` 采集器刷新后仍收到 0 帧。页面实际使用 `openclaw-app.client.ws`，类型为 `ClawFallbackSocket`，底层是客户端桥接的 EventTarget，浏览器 CDP 看不到对应 WebSocket。

因此，“刷新页面让 WebSocket 重连”无法修复此类缺失。最近的 WorkBuddy 模型选择改动没有把纳米 Work 改成 DOM 采集；根因是纳米采集器未覆盖当前客户端的桥接传输。

## 产品策略与流程

| 产品 | 过程数据来源 | 解析方式 |
| --- | --- | --- |
| 纳米 Work | 当前 gateway 连接的 message 事件，兼容浏览器 WebSocket | 纳米 `agent` / `chat` 协议，聚合思考、工具调用及最终答案 |
| WorkBuddy | `WorkbuddyDomTrace` 读取 WorkBuddy 页面 | WorkBuddy 自有页面结构、折叠内容和完成标记 |

纳米 Work 的执行流程调整为：

1. 连接客户端后，监听现有 gateway，无需为了采集强制刷新页面。
2. 每轮发送前确认过程采集已接上；未就绪报 `TRACE_CAPTURE_UNAVAILABLE`，该轮不发送。
3. 生成期间监听事件，重连时监听新连接；主文档和 work.n.cn iframe 均支持。
4. 收口时先读完页面缓冲，再构建和上传 trace，最后报告任务结果。
5. 逐轮清空数据并保留连接监听；切换设备、断开执行器时清理旧监听。

浏览器 WS 与 gateway 分别聚合，优先选择 gateway，避免同一事件重复计数。`ws_captured` 保留原字段兼容性，`capture_source` 说明实际来源（`nami_gateway` / `browser_websocket`），`capture_health` 记录连接状态、缓冲丢帧和采集错误计数。未知或未返回的思考内容不从页面提示语补造。

## 验证

- 真机两轮独立算术工具测试：分别收集到 27 / 22 帧，均包含 Bash 工具结果和最终答案（1517 / 323）；有会话标识，两轮内容独立，缓冲丢帧及采集错误均为 0。此次模型没有返回 thinking 事件。
- Chromium 合成回归覆盖：已建立的桥接连接、重连首帧、主文档/iframe、设备导航、逐轮清空、收口缓冲、重复事件、监听清理、采集失败阻止发送，以及真实浏览器 WebSocket 的兼容路径。
- WorkBuddy 模型查找、启动、交互、DOM 过程采集及耗时回归单独执行。

真机验证没有向平台创建测评记录或上传这两轮测试结果。修复属于执行器代码，服务端发布后需让执行机获取最新执行器；旧任务已经漏掉的实时过程不会自动补回。
