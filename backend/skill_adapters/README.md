# 任务日报 skill 接入

用户安装包独立上传到服务器，不进入 Git。默认安装目录为 backend/.skills/tuitui-daily-report；可通过 TASK_DAILY_REPORT_SKILL_DIR 指定其他完整目录。

安装（在仓库根目录执行，升级时追加 --upgrade）：

```sh
backend/.venv/bin/python backend/scripts/install_task_daily_report_skill.py /path/to/tuitui-daily-report-skill-latest.zip --sha256 <安装包校验值>
```

安装器保留原始脚本副本和全部参考文件，增加平台 JSON stdin 入口。已有推推命令行入口保持兼容。相同包重复安装幂等；升级先保留旧目录备份，不同原包拒绝覆盖。版本 3 校验脚本、SKILL.md 和原 HTML 模板哈希。

编写日报接口校验项目权限和任务归属后，将筛选任务及当天反馈保存为输入快照，交给平台现有 AI 队列。安装后的 skill 通过 --summary-prompt-json 读取原内容提炼规则，加入保留全部任务、按平台状态分组的要求。平台现有 AI 服务先进行语义总结，再按 skill 规则对照原始任务审校，避免把要求写成已完成或把建议当作已有安排；skill 通过 --validate-summary-json 校验任务 ID、完整覆盖和状态一致，再通过 --tasks-json 生成正文。正文包含整体进展、已有依据的风险和下一步安排、逐项摘要；平台姓名、版本和统计由程序填入，goodcase 保留原文。多任务分批总结后统一归纳；失败明确报错，不回退到字段拼接。生成不拉取群消息、不发送消息。

生成输入和结果使用独立 task_digest_generation 表的 LONGTEXT 字段，避免 AI 队列 TEXT 长度限制；仅生成者且仍有项目写权限可读取结果。前端显示排队和总结阶段，完成后可继续编辑、保存和导出 Markdown/文本。

发布 HTML 时通过 --html-json 调用同一 skill，读取原 references/template.html 样式并安全渲染当前编辑正文。HTML 快照存储在 task_digest_publication 表。日报与测评报告共同调用 report_publishing.publish_html，复用已有 Nami 部署服务、配置和凭据；链接保存在 task_digest_deployment 表。服务不可用时回退到平台 /d/<code> 短链，此读取接口仍要求登录和项目查看权限。Nami 链接沿用现有分享方式，持有链接可查看。是否使用 Nami 短链由原 NAMI_SHORTLINK_ENABLED 配置决定，本功能不修改配置。

发布自动保存正文，同一快照的成功 Nami 链接直接复用；编辑后发布产生新链接，旧链接仍展示原快照。已有日报及已删除任务的历史快照继续兼容。skill 子进程只接收 JSON，不继承数据库或机器人凭据。

代码发布仍执行原来的 bash scripts/update.sh；该脚本没有修改。发布后执行上述私有安装命令并追加 --upgrade。

验证：在 backend 目录执行 .venv/bin/python -m unittest scripts.test_task_daily_report_skill scripts.test_task_ledger scripts.test_report_publishing。设置 TASK_DAILY_REPORT_SKILL_ARCHIVE=/path/to/package.zip 可使用真实安装包验证；无该变量时使用最小接口夹具，CI 不依赖私有包。测试替代 AI 和在线发布调用，实际模型与部署结果另在平台页面验证。
