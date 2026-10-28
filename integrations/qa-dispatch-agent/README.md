# Mac 提测回填入口

这里保存 Mac 实际执行的回填代码。平台 `update.sh` 不会更新另一个目录下的 `qa-dispatch-agent`，仅发布平台或生成补丁不能完成回填入口升级。

2026-10-28 修复：

- `platform-report.ts` 使用任务说明全文填入功能/需求，不再生成带提测单号的占位标题。没有说明时保留待回填。
- 所有新任务调用 `/api/tasks/ingest`，原消息 ID 从显式 `sourceMessageId` 或旧 `YYYY-MM-DD-…-消息ID` 单号解析，始终保持字符串。没有消息 ID 的旧记录使用 `qa-dispatch:单号` 作为稳定幂等键。
- 同一消息重试、不同 run 名或并发回填由服务端唯一键去重。接口失败时不回退普通创建接口。
- 兼容旧单号、旧标题中的原消息 ID 和保存的 `platformTaskId`。对没有来源字段的人工任务，仅在完整说明与开发/需求链接足够确认时关联；短标题相同但信息冲突时返回 `ambiguous-existing-task`，不新建、不猜测合并。
- 回填不传负责人，不覆盖已有任务的标题、说明和负责人。状态同步保留人工领先状态、阻塞和关闭状态；新录入保持未分配。
- `sync-platform.ts`、`backfill-report.ts` 仅将平台确认的单条记录标记为成功并保存关联 ID，失败项保留重试。

## 更新实际入口

先确认平台已发布 `07a6452a` 或更新版本（支持完整 20000 字任务说明）。在本仓库根目录执行：

```sh
python3 integrations/qa-dispatch-agent/install.py \
  --target /Users/lixueyuan/code/daily_test/qa-dispatch-agent
python3 integrations/qa-dispatch-agent/install.py \
  --target /Users/lixueyuan/code/daily_test/qa-dispatch-agent --apply
```

第一条只检查。第二条在同步互斥锁内更新四个源文件，备份到目标目录 `.platform-report-backups/`，校验源文件及结果哈希；遇到其他本地修改则中止。重复安装不重复写入。保留配置、账号、看板和运行记录，不启动同步，不更改自动化周期。脚本每次执行重新加载源码，无常驻服务需要重启。

旧 `docs/qa-dispatch-description-title.patch` 已被这套完整源码及安装器替代，避免只留补丁而实际入口仍执行旧代码。

## 验证

使用已有 `qa-dispatch-agent/node_modules` 的 tsx 运行 `lib/platform-report.test.ts`；全部接口为本机模拟，真实同步命令使用临时配置和看板，不访问生产平台或发送消息。覆盖完整长说明、同消息重试和并发、历史单号、人工任务、匹配冲突、删除关联及混合成功失败结果落盘。

2026-10-28 验证结果：实际 Mac 目录中的 24 项回填测试通过，4 个更新文件的 TypeScript 类型检查通过，平台 `test_task_ingest` 与 `test_ingest_bridge` 共 23 项测试通过。扩大到原 agent 全套测试时为 52/53；唯一失败是旧 `dispatch-cli.test.ts` 仍期望未指定负责人时默认分配 `lixueyuan`，实际早已改为 `unassigned`。已在修改前源码上复现该失败，本次未改动这项旧测试或登记逻辑。

历史来源不完整且匹配冲突的条目，应核对后设置看板条目的 `platformTaskId`（平台已有任务 ID）；不要靠改标题消除错误，也不要运行全量回填来验证。
