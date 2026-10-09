# 将需求转换为平台可执行用例

交付对象是完整的平台 DSL 脚本。Codex 负责理解需求、探测现场、生成和转换脚本；平台下发后由 StepExecutor 执行同一份脚本，不依赖原对话、临时 JS 文件、手工拦截或再次调用模型补步骤。

## 先确定每条用例的执行方式

拆解后维护场景清单：验收点、脚本文件、实际结果、导入回执或未导入原因。补充诊断通过只能记录为诊断证据，不能计入“已交付用例”。已有工具可表达的场景继续转换和复测；缺能力则明确缺少的动作，不悄悄漏掉。主流程优先，用户明确要求的失败/重试/异步规则也是验收范围，不为减少边界数量省略。

1. 从真实页面得到 frame、容器及唯一定位；从实际请求得到 path、method、query 和响应字段，不猜接口。
2. 前置操作、视口设置、读取动态值、模拟注册、触发操作、业务断言及恢复都写进 DSL。使用 `get_text.save_as` + `assert_text.expected_from` 比较动态名称或编辑内容。
3. 先检查目标平台 `GET /api/verified-imports/capabilities` 和执行机代码能力。本文网络动作需要 `network-scenarios-v1`；旧平台/旧 Runner 需要升级，不能把动作改成文字前置、删除断言或用模型判断替代。
4. 同一完整脚本走 Runner `--verify-import` 两次。只有其原始成功包可以提交。任何脚本变化都要重新完整验证；不得把诊断报告粘贴成执行报告。
5. 导入后从平台执行入口下发同一脚本。当前批次若未做平台队列重跑，如实区分“Runner 同路径验证”和“平台下发实测”。异步接收后仍按主技能规则结束，不轮询。

## 网络场景 DSL（network-scenarios-v1）

这些动作是确定性 Runner 能力，不要求执行机启用 Codex。适用于指定 frame 当前文档的**同源 fetch**。原有 `mock_route` 仍适用于 Playwright 路由拦截；不要假定 fetch 模拟覆盖 XHR、原生桥接、后台进程或跨域请求。模拟必须命中；无法命中先排查真实传输方式。整页刷新后本场景失效，不能继续声称模拟通过；SPA 路由跳转可继续。

所有网络操作用 `args.id` 引用同一用例内已注册且唯一的标识。`frame` 显式为 `shell` 或 `url:<唯一 frame URL 子串>`。`path` 是不含域名/查询串/通配符的精确路径，`method` 显式大写。可用 `query` 字符串字典缩小范围。仅捕获计数、状态及指定列表字段，不采集账号、请求头、token 或完整业务响应。

| action | 必填 args | 说明 |
|---|---|---|
| `watch_network` | `id, frame, path, method` | 在触发操作之前注册；需比较列表时同时提供 `response_path, item_field`（点分路径） |
| `fault_route` | `id, frame, path, method, mode` | `network_error` 模拟连接失败；`response` 还需 `status, body`；`hold_response` 还需 `timeout_ms:100–30000`，真实请求到达服务端、仅延迟结果交给页面 |
| `release_fault` | `id` | 恢复该规则的后续请求并释放挂起响应，保留命中统计 |
| `wait_network` | `id, phase, count` | `requested` 已发起；`received` 已收到服务端响应；`completed` 已交回页面（含请求异常）；可用 `timeout_ms`，最多30000 |
| `assert_fault_hits` | `id, expected` | 验证模拟实际命中数；暂停响应超时自动释放视为失败 |
| `assert_network_count` | `id, expected` | 完整观察 `settle_ms` 后比较请求数，默认500ms，最多30000ms；报告明确这个观察窗口，不宣称无限时间无重复 |
| `assert_list_from_response` | `id` + 常规 `target` | 按指定响应列表字段与 DOM 文本逐项比较，保持接口顺序；默认空列表不通过，确需验证空列表显式 `allow_empty:true` |

`click.args.click_count:2` 表达真实双击。不能用两次有间隔的普通点击代替并发双击。对列表断言必须明确 frame 和列表行定位，支持常规 key/selector/within，不能取首项冒充整表比较。

### 例：重试列表不重复发布

以下路径、定位、响应结构只是示例，必须替换为当前应用实测值。

```json
[
  {"action":"watch_network","args":{"id":"publish","frame":"shell","path":"/api/publish","method":"POST"}},
  {"action":"fault_route","args":{"id":"listFailure","frame":"shell","path":"/api/list","method":"GET","mode":"response","status":503,"body":{"error":"unavailable"}}},
  {"action":"click","target":{"selector":"#publish","frame":"shell"}},
  {"action":"assert_visible","target":{"selector":"#retry","frame":"shell"}},
  {"action":"assert_fault_hits","args":{"id":"listFailure","expected":1}},
  {"action":"release_fault","args":{"id":"listFailure"}},
  {"action":"click","target":{"selector":"#retry","frame":"shell"}},
  {"action":"assert_visible","target":{"selector":"#my-list","frame":"shell"}},
  {"action":"assert_network_count","args":{"id":"publish","expected":1,"settle_ms":500}}
]
```

上例必须补上真实前置导航和发布内容准备。发布失败保留内容场景，应在发布前读取字段并在失败后逐一比较。迟到响应场景使用 `hold_response`，等待 `received` 后离开页面，再释放并等待 `completed`，给页面处理结果的观察窗口后断言当前页面未被改变。列表顺序场景只观察真实接口返回，不伪造排序结果。

Runner 在成功和失败出口恢复本用例的模拟。未命中、暂停超时、清理失败都不能标为通过。fetch 会话超过60秒没有协议操作会自动恢复，防止进程意外退出后长期残留；该次验证随之失效。不要在模拟期间插入长时间生成等待。发布、删除等真实服务端副作用仍按用户授权范围处理，`hold_response` 不会阻止服务端写入。
