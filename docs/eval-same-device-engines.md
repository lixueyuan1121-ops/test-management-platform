# 纳米Work 与 WorkBuddy 测评能力

`run-eval.cmd` / `run-eval.sh` 启动的测评 runner 默认支持纳米Work。已安装并配置 WorkBuddy 时，在 `.env` 设置 `EVAL_ENGINE=workbuddy`，即可额外支持 WorkBuddy；它不会关闭纳米Work，也不要求再启动一个仅纳米Work的进程。现有 WorkBuddy 配置无需改动。

`35d074b6` 错误地将 `engine=workbuddy` 解释为仅支持 WorkBuddy，使同一 runner 无法领取纳米Work任务，并导致双引擎下发误报“纳米Work当前无在线执行机”。本次修正在线判定、队列过滤及认领的能力解释；任务本身的 `target_engine` 仍指定唯一产品，执行器按它进入对应产品的执行路径。

服务端按 `(device_id, engine)` 记录 UTC 能力心跳：

- 默认或 `engine=namiwork`：刷新纳米Work能力。
- `engine=workbuddy`：同时刷新纳米Work和WorkBuddy能力。
- 同一设备启动多个进程时，默认 runner 的轮询不会覆盖仍在线的 WorkBuddy 能力；如果只有默认 runner 继续运行，WorkBuddy 能力在 3 分钟在线窗口后过期。
- 领取及整组认领均检查能力，默认 runner 不能领取 WorkBuddy 任务；已启用 WorkBuddy 的 runner 可领取两种产品的任务。原有设备分配范围、固定 VM 和会话整组限制继续生效。
- 执行期心跳携带 runner 的能力声明，即使正在执行纳米Work任务，也续期已启用的 WorkBuddy 能力；无效认领不能保活。旧客户端不带声明时，沿用最近轮询声明，正在执行 WorkBuddy 任务也证明它支持两种产品。
- 历史 `eval_engine=workbuddy` 字段或只包含 WorkBuddy 的有效心跳记录同样按双能力解释，后端更新即可恢复现有 runner 的纳米Work可用性。

发布时更新并重启平台后端。已有 `runner_eval_heartbeat` 表无需迁移；缺少该表时，启动会自动补建。执行器通过现有自更新流程获得携带能力声明的执行期心跳代码，无需调整 `.env` 或另起进程。

验证：`cd backend && .venv/bin/python -m unittest scripts.test_same_device_eval_engines scripts.test_eval_dynamic_queue scripts.test_device_board_accuracy -v`。

隔离数据库回归覆盖单 runner 双能力、两题×两引擎自动下发与认领、默认能力限制、可选能力过期、执行期续约、无效认领、共享 token、旧客户端和建表幂等性。客户端 HTTP 回归：`node tools/qalab-runner/eval/test/platform-client-queue.test.js`。
