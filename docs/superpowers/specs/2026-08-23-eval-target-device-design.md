# 设计:对话测评链路 · 补丁——指定设备(vm)执行

- 日期:2026-08-23
- 状态:已评审(用户确认整体架构 + "一气呵成按推荐执行")
- 所属大工程:对话测评链路(生成→下发/执行/回写→判定→回填/推送)。**本补丁修子项2 执行层的多设备缺口。**
- 本 spec 范围:纳米 Work 客户端存在**多个设备(vm)**,平台下发时需**指定目标设备**,CLI 平台模式执行前**切到该设备**再跑对话。
- 依赖:子项 0-4(已全部合入 main)。本补丁在其上增量。
- 关联代码:`backend/app/models/ai_eval.py`(eval_run 加列 + 新表)、`backend/app/api/eval_queue.py`(enqueue 带 target_device)、新建 `backend/app/api/eval_devices.py`(设备上报/查询)、`backend/app/db/migrate.py`(加列)、`backend/sql/schema.sql`、`frontend/src/views/AIEvalGen.vue`(下发选设备)、CLI `src/desktop-pool.js`(读设备列表 + 切换)、`src/platform-client.js`(上报端点)、`bin/ai-eval.js`(platform 命令编排切设备)。

## 1. 背景与问题

子项2 的 CLI 平台模式假设"纳米 Work 客户端只有一个活跃 vm",`DesktopPool._resolveMainPage` 盲取第一个 `work.n.cn` 页面就执行。但实测客户端里有**多个设备(vm)**,同窗口列表切换。碰上非目标/离线设备时,对话输入框不可用 → "新建对话未干净/`waitFor Timeout 10000ms`"、全部 run 回写 failed、`ws_captured=false`。

**根因**:缺"指定设备 + 切换到指定设备"的能力。这是执行属性(同一 query 可派给不同设备),归属执行侧(子项2 补丁)。

## 2. 已验证的地基(2026-08-23 实测,非推测)

深读 work.n.cn 前端源码 `openclaw360-web` + 本机(lili-win, namiwork-desktop/3.6.0)真机探测,全部坐实:

- **读设备列表**:`window.clawDeviceService.getDeviceList(true)` 返回数组,每项 `{id(32位hex核), name(显示名), status, type, url(带前缀子域,首段=切换label)}`。服务恒挂 `window.clawDeviceService`。实测读到 6 个设备(4 在线/2 离线),字段齐全。
- **在线判据**:`status ∈ {"online","active"}`(**不是** URL 上的 `?status=offline`——源码确证那是 Electron 父壳写的、web 不读,不能当真值)。
- **切换设备**:本机环境 `isInIframe=false`,走 `window.location.assign('<label>.work.n.cn/claw?vm_id=<label>')`,label = `device.url` 首段(带前缀,如 `p8a45…`/`n4137…`)。实测切到目标 vm **1.5s** 内完成,`currentDevice` 更新为目标,对话输入框 `chat-compose-rich-textarea` 无 `disabled`、`.chat-compose-rich__content` `contenteditable="true"`。切回无副作用。
- **当前 vm 判定**:`location.hostname` 首段(32位=vmId;33位去首字符=vmId=device.id),或 `clawDeviceService.currentDevice.id`。

## 3. 关键决策(AI 自主拍板,用户已认可架构)

| # | 决策 | 选择与理由 |
|---|---|---|
| 1 | 机器↔设备关系 | **一对多**:一台执行机按不同任务切不同设备。平台下发时指定设备。 |
| 2 | 设备可见性 | **平台自动列出可选设备**(前端下拉,不用手记 hex id)。靠 CLI 上报设备列表实现。 |
| 3 | 设备标识 | `vm_id`(32位hex核=`device.id`)作跨端标识;`label`(带前缀子域)仅切换用,一并存。 |
| 4 | 读列表方式 | 注入 `window.clawDeviceService.getDeviceList(true)`(稳,字段全)。**不抓 DOM**(列表懒渲染、无 data-vm-id)。 |
| 5 | 切换方式 | 注入 `location.assign('<label>.work.n.cn/claw?vm_id=<label>')`(实测本机 isInIframe=false 路径)。**兼容兜底**:若注入后 URL 未切到位,记 warning 并 fail-closed(不裸跑错设备)。 |
| 6 | 上报时机 | CLI platform 每轮 `runOnce` 连上客户端后上报一次(保持新鲜);轻量、随轮询自然刷新。 |
| 7 | 离线目标处理 | 目标设备离线:**云设备(type=0)** 尝试 `startCloudDevice(vm_id)` 唤醒后再切;**本地/wsl/elec** 无法远程唤醒 → **fail-closed** 回写 failed(reason=目标设备离线),不裸跑污染判定。 |
| 8 | 未指定设备 | `target_device` 空 → 沿用旧行为(用当前 vm,不切),向后兼容子项2 已下发的老 run。 |
| 9 | 数据模型 | 新表 `eval_client_device`(设备快照)+ `eval_run` 加 `target_device` 列。不复用 runner_device(那是物理执行机,与 vm 是两层)。 |

## 4. 数据模型

### 4.1 新表 `eval_client_device`(CLI 上报的客户端设备快照)

`backend/app/models/ai_eval.py` 追加:

| 列 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | |
| runner | String(64), index | 所属执行机 runner_id |
| vm_id | String(64) | 设备32位hex核(=device.id) |
| label | String(96) | 带前缀子域label(=device.url首段,切换用) |
| name | String(128) | 显示名 |
| status | String(16) | online/offline/pending/... |
| device_type | Integer | 0云/1本地/2盒子/3wsl/4elec |
| last_report_at | DateTime | 最近上报时间(默认 utcnow) |

- 唯一约束 `uk_runner_vm (runner, vm_id)`:同一执行机同一 vm 只一行,上报走 upsert。
- 走 `create_all` 自动建(模型在 `models/__init__.py` 汇总导入即可);`schema.sql` 手工补 CREATE TABLE。

### 4.2 `eval_run` 加列 `target_device`

- `target_device` String(64) NULL:目标设备 vm_id。空=不指定。
- `migrate.py` 加 `ensure_eval_run_target_device()`(仿 `ensure_eval_run_payload`),startup 调用。
- `schema.sql` 的 eval_run 段补该列。
- 模型 `EvalRun` 加 `target_device: Mapped[str | None]`。

## 5. 后端端点(新建 `api/eval_devices.py`)

沿用 {code,msg,data} 信封、手写 `_to_out`、`require_runner_ctx`(上报)/`get_current_user`(查询)。

- `POST /api/eval-devices/report`(runner 鉴权 `require_runner_ctx`):
  - 入参 `EvalDeviceReportIn{runner, devices:[{vm_id, label, name, status, device_type}]}`。
  - 设备 token 时 runner 锁定为 `ctx.device.runner_id`(防冒充,仿 eval_queue)。
  - 对每个 device upsert(按 runner+vm_id):存在则更新 name/status/label/device_type/last_report_at,不存在则插入。
  - 上报列表外的旧设备**不删**(保留,靠 last_report_at 判新鲜;YAGNI 不做删除)。
  - 返回 `{reported: n}`。
- `GET /api/eval-devices?runner=X`(用户 JWT `get_current_user`):
  - 查该 runner 的设备列表,按 status(在线优先)+name 排序。
  - 返回 `[{vm_id, name, status, device_type, label, last_report_at}]`,供前端下拉。
  - **不做项目级鉴权**:设备是执行机资源,登录用户均可见其列表(与 listMyDevices 同级别;下发时的项目鉴权在 enqueue 把关)。
- router 注册进 `api/router.py`。

## 6. enqueue 带 target_device(改 `api/eval_queue.py`)

- `EvalEnqueueIn` 加 `target_device: str | None = None`。
- `enqueue` 建 `EvalRun` 时 `target_device=body.target_device`。
- **payload 保持题面快照纯粹**(不塞执行参数);`target_device` 作为执行参数由 `_to_out` 顶层回显。
- `_to_out` 加 `"target_device": r.target_device`。CLI fetchPending 从 `item.target_device` 顶层字段读(与 `target_engine` 同级)。

## 7. CLI 改造

### 7.1 `src/desktop-pool.js` 加设备能力

- `async listDevices()`:在 clawDeviceService 所在 frame 注入 `getDeviceList(true)`,返回 `[{vm_id:id, label:url.split('.')[0], name, status, device_type:type}]`。找不到服务返回 `[]`。
- `async currentVmId()`:注入读 `clawDeviceService.currentDevice?.id`,兜底 `location.hostname` 首段解析。
- `async switchTo(vmId)`:
  1. listDevices 找到目标 dev;找不到 → 抛错。
  2. 若已是当前 vm → 直接 return（读 currentVmId 比较）。
  3. 目标离线且 type=0(云)→ 注入 `clawDeviceService.startCloudDevice(vm_id)`,轮询等 status 变 online(上限如 60s);其它类型离线 → 抛错(上层 fail-closed)。
  4. 注入 `location.assign('<label>.work.n.cn/claw?vm_id=<label>')`。
  5. 轮询等 `page.url()` host 首段含 label 且 currentVmId==vmId(上限如 30s)。
  6. 重新 resolve 主 page(导航后 page/frame 变)+ 等对话输入框 enabled(复用 `_resolveMainPage` 的输入框就绪等待,或新加 `_waitComposerReady`)。
  7. 切换后重挂 attachWsTrace(导航后新 page,顺带争取修 ws_captured;见 §9)。
- frame 解析辅助:现有 `_resolveMainPage` 只找 page,新增找 clawDeviceService frame 的 helper(逐 frame 探 `typeof window.clawDeviceService`)。

### 7.2 `src/platform-client.js` 加上报

- `reportDevices(devices)`:`POST /api/eval-devices/report`,body `{runner:this.runnerId, devices}`,复用 `_api`。

### 7.3 `bin/ai-eval.js` platform 命令编排

- `runOnce` 内 `pool.init()` 后:`const devices = await pool.listDevices(); if (devices.length) await client.reportDevices(devices).catch(log warn);`(上报,失败不阻断执行)。
- 每条 run 执行前:读 `item.target_device`(顶层字段,`_to_out` 已回显)。若非空且 ≠ 当前 vm:
  - `try { await pool.switchTo(target); } catch(e){ 回写 failed(reason:`目标设备切换失败:${e.message}`); continue; }`
  - 切换成功后 `runner` 需重新绑定主 page(switchTo 内部已更新 pool.mainPage;runner 通过 `pool.getMainPage()` 取——**注意**:DesktopRunner 构造时传入了 page 引用,切换后 page 变了,需让 runner 用新 page。方案:switchTo 后**重建 DesktopRunner**(轻量,单条执行),或 DesktopRunner 支持 rebind。**决策:每条 run 若发生切换,重建 runner 绑定新 mainPage**(最稳,避免陈旧 page 引用)。
- target_device 空 → 不切,沿用当前 vm(旧行为)。

## 8. 前端(`AIEvalGen.vue`)

- 下发区(dispatch-bar)在"选执行机"下拉后,加"选设备"下拉:
  - 选执行机(chosenRunner)变化时,调 `GET /api/eval-devices?runner=X` 拉设备列表 `clientDevices`。
  - 设备下拉:显示 `name`(带在线/离线标),value=`vm_id`。在线设备优先、置顶。
  - `chosenDevice` 为空时:提示"未选设备(将用执行机当前设备)"——允许不选(向后兼容),但推荐选。
- `enqueueEvalQueries` payload 加 `target_device: chosenDevice`。
- `api/index.js` 加 `listEvalDevices(runner)`。
- 无设备时下拉提示"该执行机未上报设备(请确认 CLI platform 已连上客户端并上报)"。

## 9. 附带:ws_captured 争取修复(非本 spec 强目标)

切换设备会触发页面导航,switchTo 在导航后的新 page 上重挂 `attachWsTrace`。**若** attach 时机早于对话 WS 建立(切换后新建对话才连 WS),则 ws_captured 有望变 true。本 spec **不把它列为强验收项**(受真机时序影响),但切换重挂是顺带的正确做法,联调时观察 `ws=` 日志确认。

## 10. 迁移与 schema

- 新表 `eval_client_device`:`create_all` 自动建(模型汇总导入)+ `schema.sql` 补 CREATE TABLE。
- `eval_run.target_device`:模型加列 + `migrate.ensure_eval_run_target_device()` + `schema.sql` 补列。
- 模型在 `models/__init__.py` 汇总导入(新表 EvalClientDevice)。
- `main.py` startup 调 `ensure_eval_run_target_device()`(新表无需额外 migrate,create_all 建)。

## 11. 影响面与风险

- **隔离**:新增设备上报/查询端点、eval_run 加列、CLI 加设备切换,不改 eval 生成/判定/回填链路,不动 exec_queue。
- **风险1(切换时机/环境差异)**:实测本机 isInIframe=false 走 location.assign。若换机器 isInIframe=true(企业版/不同壳),切换走 postMessage 给父壳,注入 location.assign 可能无效。缓解:switchTo 后严格校验 URL 到位,未到位 fail-closed(回写 failed,不裸跑),日志暴露;真机联调覆盖目标环境。
- **风险2(page 引用陈旧)**:切换导航后 mainPage 变,陈旧 runner 会操作旧 page。缓解:切换后重建 runner 绑新 page(§7.3 决策)。
- **风险3(离线设备)**:本地/wsl/elec 离线无法唤醒 → fail-closed。云设备唤醒有延迟,startCloudDevice 后轮询等上线(上限保护)。
- **风险4(真验证)**:读列表+切换已在本机(lili-win)实测通过;端到端(下发选设备→CLI 切→跑对话→回写)需真机联调。本机 claude CLI 被 SessionStart hook 污染不影响本补丁(纯 Node CLI + 平台 API)。

## 12. 验证方式(本仓库无测试框架)

1. 后端:插 eval_client_device → `GET /api/eval-devices` 返回排序正确;`POST /report` upsert(同 vm_id 更新不重复插)。脚本验证(仿子项做法,一次性 Python 脚本)。
2. enqueue:带 target_device → eval_run.target_device 落库 + `_to_out` 回显 + payload 含。
3. CLI:`listDevices`/`switchTo` 已在本机真机验证(读到6设备、切换1.5s到位、输入框enabled)。实现后再跑一次 platform --once 真机联调(拉任务→切设备→跑→回写)。
4. 前端:选执行机→拉设备下拉→选设备→下发带 target_device;npm build 过。
5. 端到端(真机):下发一条指定"云龙虾A"→ lili-win 上 CLI 切过去执行 → 回写 done + 看 ws。

## 13. 交付清单

- [ ] models/ai_eval.py:EvalClientDevice 新表 + EvalRun.target_device 列;models/__init__.py 汇总导入
- [ ] migrate.py:ensure_eval_run_target_device() + main.py startup 调用
- [ ] sql/schema.sql:eval_client_device 建表 + eval_run 加 target_device 列
- [ ] api/eval_devices.py:POST /report + GET / + schemas + router 注册
- [ ] api/eval_queue.py:EvalEnqueueIn 加 target_device;enqueue 落库;payload/_to_out 带 target_device
- [ ] CLI src/desktop-pool.js:listDevices / currentVmId / switchTo / clawDeviceService frame helper / 输入框就绪等待
- [ ] CLI src/platform-client.js:reportDevices
- [ ] CLI bin/ai-eval.js:platform 命令上报设备 + 每条 run 前切设备(重建 runner)+ fail-closed
- [ ] 前端 AIEvalGen.vue:设备下拉 + enqueue 带 target_device;api/index.js listEvalDevices
- [ ] 手动/脚本验证(§12)

## 14. 说明

CLI 仓库 `D:\code\ai-eval-cli-yt` 非 git,改动为文件交付(该仓库自行纳管)。平台侧改动走本分支 `spec/eval-target-device`。工作区无关既存改动(run.cmd/__MACOSX/qalab-runner.zip)全程不动。
