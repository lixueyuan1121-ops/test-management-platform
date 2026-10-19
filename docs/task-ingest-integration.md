# 提测自动录入与人工派单

平台地址：`https://qalab.claw.qihoo.net`。提测转发群：`7652669649127481`。管理员指派后的通知群：`7652944526714832`。

## 行为

- 原机器人继续识别并转发提测内容，同时提交 `POST /api/tasks/ingest`。
- 入库时负责人为空、状态为 `unassigned`（待分配），不创建派单、验收或执行流程。
- 按项目、转发群与原消息 ID 去重。重复提交返回已有任务，不覆盖人工编辑或负责人。
- 项目/平台管理员在任务编辑中填写或更换有效项目成员后保存，发送一条任务派单消息。支持多人负责人，合并为一条通知；相同负责人重复保存、普通字段修改、自动录入均不发送。
- 待分配任务不计入个人工作量、日报应交名单，也不通过“复制昨日”重复生成。
- 派单记录和通知在同一事务提交；后台立即发送，每 30 秒扫描待重试记录。只有推推明确返回 `errcode=0` 才算成功，失败按退避重试。未发出的过时指派会取消。
- 派单使用推推机器人顶层 `at` 数组提醒全部测试负责人。服务端 `.env` 的 `TUITUI_ACCOUNT_MAP` 是 JSON 对象（平台用户名 → 已核实的推推账号），不能用平台数字 ID、姓名或猜测的拼音代替。账号映射缺失时保留通知并记录原因，补齐配置重启后自动重试；不发送缺少部分负责人提醒的消息。账号映射不提交 Git。

推推机器人接口没有已确认的服务端幂等键；在“推推已接收但回执丢失/进程崩溃”的极端情况下，重试可能产生重复消息。正常重复保存和并发处理有数据库去重与认领保护。

## 自动录入接口

使用现有平台管理员账号 Bearer access token，或配置专用 `TASK_INGEST_TOKEN`、`TASK_INGEST_USER_ID` 后传 `X-Task-Ingest-Token`。专用令牌仅适用于录入接口，关联账号必须对目标项目有管理员权限。

```json
{
  "project_id": 1,
  "source_group_id": "7652669649127481",
  "source_message_id": "原始源消息ID（字符串）",
  "title": "搜索功能提测",
  "description": "来源群、发送人、北京时间、原消息ID及完整提测原文",
  "developer": "原消息中的开发负责人",
  "requirement_url": "https://example.com/需求文档",
  "target_version": "3.28.0"
}
```

`project_id`、`source_message_id`、`title`、`description` 必填。`assigned_date` 不传则使用北京时间当天；其他未知信息可省略，优先级缺省为 P2。不接受 `assigned_to`、`status`、`co_testers` 等分配/工作流字段。`source_group_id` 表示本次提测转发通道；四个原始源群号保留在 description 中。原消息 ID 不能转换为 JavaScript Number。

成功信封：`{"code":0,"data":{"created":true,"task":{...}}}`。重复提交 `created=false`，两种均视为录入成功。

## 纳米 Work 自动化接线

已定位现有任务“推推提测消息收集转发（已停自动派单）”，工作日 10–22 点每 15 分钟执行。其旧提示词明确禁止平台上报，需要替换为同目录 `task-ingest-automation.txt` 的内容。旧“平台同步-派单与状态上报”保持暂停。

复用现有 Mac 桥接和账号配置（不修改、不回显凭据）：

```bash
python3 /Users/lixueyuan/code/test-management-platform-fresh/scripts/ingest_submissions.py \
  --base-url https://qalab.claw.qihoo.net \
  --input /tmp/qa-ingest-batch.json
```

输入为上述提测对象的数组，可省略 project_id；脚本始终按已有 `qa-dispatch-agent/config/platform.json` 中的 `projectCode=nami_pc` 定位项目。输出逐项 `ok/source_message_id/task_id/created`，失败项必须保留在云端独立队列，下次只补录，不重复转发。

`--probe` 只检查接口版本、登录与项目，不写任务。接口未上线时不回退旧 `/api/tasks`，也不虚构负责人。

## 上线顺序

1. 发布后端及任务页面，启动时自动补来源字段、允许负责人为空并建立通知表。迁移保留已有任务、日报、验收关系及索引。
2. 服务端配置现有推推机器人 `TUITUI_BOT_APPID` / `TUITUI_BOT_SECRET`，确认可向派单群发送；设置 `PLATFORM_BASE_URL=https://qalab.claw.qihoo.net`。专用派单群默认即为用户指定值，不受旧 `NOTIFY_TASK_ASSIGN=false` 影响。
3. 在 Mac 执行桥接脚本 `--probe` 确认 ready；再更新原自动化的提示词，核对原执行时间、模型、专家及启用状态没有变化。客户端编辑器显示的每周 11:00 与原自定义周期不一致，保存前需确认能保留原周期。
4. 用后续真实提测观察入库；管理员分配负责人后观察派单群。不要为验证把测试消息发到业务群。

## 验证

```bash
cd backend
.venv/bin/python -m unittest scripts.test_task_ingest scripts.test_ingest_bridge scripts.test_task_ledger scripts.test_task_ready_online scripts.test_task_multi_assignee scripts.test_task_dates
```

覆盖未分配录入、精确去重、鉴权/越权、拒绝自动派人、人工派单、重复保存、更换/清空负责人、失败重试、失效通知取消、统计、旧 SQLite 外键关联迁移与桥接失败保留。前端 `node frontend/tests/task-ledger.test.mjs` 覆盖待分配筛选与展示。

## 发布记录（2026-10-19）

发布基于线上 `8d9f20cd`，保留日期范围、单日顺延、多选筛选和多人负责人功能。独立发布分支 `codex/task-ingest-release-20261019`，不合入主工作区的其他未提交改动。

服务器：用户指定跳板机资产 `p92524v.hulk.bjwdt.qihoo.net`；目录 `/home/lixueyuan1/test-management-platform`；使用 `bash scripts/update.sh` 拉取并重启。线上数据库为 MySQL，任务 ID 为 INTEGER，负责人已允许为空；本次新增来源列、唯一键和派单通知表。

47 项后端检查通过，前端筛选检查与生产构建通过。已在隔离库用实际构建验证自动录入、负责人留空、管理员填写负责人及详情跳转，推推发送使用模拟器。
