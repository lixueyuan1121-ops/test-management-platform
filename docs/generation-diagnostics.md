# 生成问题日志排查

适用于需求分析、按验收规则生成用例及兼容的旧版分片生成。

## 日志位置

- `backend/logs/generation.jsonl`：结构化诊断日志，UTF-8，每行一个 JSON。
- `backend/logs/backend.log`：现有异常堆栈及服务日志。
- 每个日志文件最大 10 MB，保留 5 个轮转备份。需要排查旧任务时同时检索备份。
- 时间使用带时区的 UTC；例如 12:05 UTC 对应北京时间 20:05。

## 如何定位

页面生成进度中显示任务编号；以 `job_id` 查找对应事件，再用 `batch_id` 和 `call_id` 区分并发批次与模型调用。

链路：`job_enqueued` → `job_started` → `phase_changed` / `stage_start` → `model_slot_wait` / `model_slot_acquired` → `model_process_started` → `model_first_transport` → `model_first_text` / `model_wait` → `model_process_end` / `model_call_end` → 校验 → 保存 → `job_done` 或 `job_failed`。

- 已启动进程、一直无 `model_first_transport`：本机模型 CLI 尚未返回任何输出。
- 有传输行但无正文：检查 `transport_lines`、`parsed_events`，区分协议记录和可展示正文；不记录思考内容。
- `model_wait`：每 30 秒记录耗时、无新正文时长、传输行数、心跳及正文长度。心跳不是模型正文。
- `upstream_stream_idle_timeout`：模型调用链返回流空闲超时；不能仅凭此错误确定网关或模型内部的根因。
- `case_validation_done`：已解析用例数量、分配验收条件数、未覆盖数及警告数。
- `case_persistence_done`：保存是否成功及保存数量；`persistence_retry` 表示数据库重连重试。
- `checkpoint_reused`：复用了之前通过校验的分析阶段。

日志只写任务关联信息、时间、数量、错误类别和请求文本的长度/摘要，不写完整需求、模型正文、令牌、Cookie 或网关凭据。完整业务输出继续由原任务记录保存并受既有权限控制。日志失败不会中断生成。

## 本次失败（历史任务 #17）

2026-09-14 北京时间 20:05:46 开始用例生成，20:16:03 失败，历时约 617 秒。模型返回 `Stream idle timeout - no chunks received`，缺少 R1-C1、R1-C2 的用例。原有日志没有中间传输指标，无法追溯上游内部卡点。

新日志只对部署后的请求生效，不会补造历史任务数据。错误信封不会覆盖此前的真实正文，也不会将 `API Error` 提示当作生成用例。未收到完成标记的输出不能按完整结果采纳。本次不修改本地模型网关配置，不以增加超时宣称修复上游问题。

## 后续任务 #18 的实际根因

任务 #18 在约 95 秒收到非正文块后被本地解析器中断，属于 unsupported_text，不是超时。受限复现捕获到 text 字段内的 reasoning 列表；随后真实正文正常返回，约 140 秒以 success 结束。现在协议入口过滤 reasoning，且不把它计为正文或用于重复响应去重。真实响应离线回放得到 5 条用例，覆盖 R1-C1、R1-C2，校验无警告；未写入业务库。model_nontext_ignored 表示已忽略非正文块，后续仍继续等待有效正文。此结论不覆盖 #17 的独立上游流空闲超时。
