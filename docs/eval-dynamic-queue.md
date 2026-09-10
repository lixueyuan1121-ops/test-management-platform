# 测评队列动态补领

## 行为

- 下发时仍按会话轮数初始分片，同时保存每个产品本批次允许的 runner 列表。
- 新版 runner 默认每次取一个完整会话组。优先领取自己的队列，空闲时补领选定机器名下尚未开始的会话组，无需等待原机器离线。
- 补领检查产品、候选机器范围和整组状态。指定 target_device 的任务不跨机器转移，避免切换到本机不可访问的 VM。
- 多轮会话按项目、批次、产品、目标设备、A/B 组和 conversation_group 隔离；单轮独立。
- 整组条件更新认领；任一轮已被认领则整组回滚。回填、轨迹上传和心跳均校验本次 claim_token。
- 执行时每分钟发送心跳；回收按最后执行心跳/开始时间计算，旧记录回退 updated_at，不使用排队创建时间。
- 多轮失败重试会重置整组，包括已成功的前序轮次，以重新建立上下文。仍在执行或已取消的组不能局部重试。

## 升级顺序

1. 先升级并重启后端，启动迁移自动补充 eligible_runners、started_at、heartbeat_at、claim_token 四个可空字段。
2. 再更新各机器的 qalab-runner；已有自更新流程会在执行轮次之间检查更新，不打断正在运行的会话。
3. 新下发的批次开始记录候选机器范围。旧批次没有这份快照，仍留在原机器队列，不根据当前在线机器擅自扩大执行范围。需要动态补领的旧批次应停止后重新下发。

旧 runner 仍能按原协议领取自己的任务；只有新版 runner 主动补领。不要将新版 runner 接入未升级的后端。
运行中的会话不会自动迁移，异常执行在超时收口后需要整组重试。

## 验证

后端：`cd backend && .venv/bin/python -m scripts.test_eval_dynamic_queue`

执行器：`node tools/qalab-runner/eval/test/conversation-group.test.js`、`node tools/qalab-runner/eval/test/platform-client-queue.test.js`

线上验收：选择五台同产品机器，下发多于五个会话组；让一台先完成，确认它能领取其他机器尚未开始的组。检查运行中的组不迁移、组内 runner 一致、每轮只产生一份有效回填。另测原机器离线、不同产品、指定 VM 和同用例跨批次下发。
