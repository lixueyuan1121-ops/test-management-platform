# 同一设备运行纳米Work 与 WorkBuddy 测评

两个测评 runner 可以共用平台中的设备身份。每个进程分别设置 `EVAL_ENGINE=namiwork` 或 `EVAL_ENGINE=workbuddy`；进程环境变量优先于 `.env`，无需为此更换设备 token。

旧版只记录 `runner_device.eval_engine`，最后一次轮询会覆盖另一进程的引擎标记，双引擎自动下发因此可能报“当前无在线执行机”。另外，领取自身设备的任务原先跳过引擎检查，同机两个进程可能拿到彼此的任务。

修复后的服务端按 `(device_id, engine)` 独立记录 UTC 心跳：

- 设备 token 和共享 token 均更新对应引擎。
- 队列轮询、执行期有效认领心跳都可刷新引擎在线状态；失效认领不能保活。
- 一个进程持续轮询不会替另一个已停止的引擎保活；现有在线窗口为 3 分钟。
- 双引擎自动下发允许选中同一设备，领取及整组认领仍严格按请求引擎隔离。
- 升级后尚未上报新心跳的旧设备沿用原单引擎标记；历史无 engine 参数按纳米Work 处理。

发布只需更新并重启平台后端。启动时 `Base.metadata.create_all` 会补建 `runner_eval_heartbeat` 表，原表及历史数据保持不变；两个正在轮询的 runner 会在下一次心跳登记各自引擎，不需要更新执行器代码。

验证：`cd backend && .venv/bin/python -m unittest scripts.test_same_device_eval_engines -v`。
隔离数据库回归覆盖交错心跳、两题×两引擎自动下发、分别领取、单引擎过期、执行期续约、无效认领、共享 token、旧客户端和新表幂等创建。动态队列、设备看板归属及现有分机测试同步回归。
