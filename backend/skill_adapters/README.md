# 任务日报 skill 接入

用户安装包独立上传到服务器，不进入 Git。默认安装目录为 backend/.skills/tuitui-daily-report；可通过 TASK_DAILY_REPORT_SKILL_DIR 指定其他完整目录。

一次性安装（在仓库根目录执行）：

```sh
backend/.venv/bin/python backend/scripts/install_task_daily_report_skill.py /path/to/tuitui-daily-report-skill-latest.zip --sha256 <安装包校验值>
```

安装器保留原始脚本副本和全部参考文件，为原生成函数增加 platform 输入，并增加 --tasks-json stdin 模式。已有推推命令行入口保持兼容。相同包重复安装幂等；适配器升级使用 --upgrade，先保留旧目录备份再安装；不同原包拒绝覆盖。

编写日报接口在校验项目权限和任务归属后，将所选任务及当天反馈传入安装后的脚本。状态以数据库字段为准，全部选中任务都按状态分组；姓名来自平台。生成不拉取群消息、不发送消息。发布 HTML 时通过 --html-json 调用同一个已安装 skill，读取原 references/template.html 样式并安全渲染当前编辑正文。HTML 快照存储在新增 task_digest_publication 表，/d/<code> 为平台短链；读取接口必须通过平台登录和项目查看权限校验。发布自动保存正文，重复发布同一快照复用链接，编辑后发布产生新链接，旧链接仍展示原快照。已有日报和已删除任务的历史快照继续兼容。

生成时校验安装清单和脚本哈希，使用独立 Python 子进程和 JSON 标准输入，不拼接 shell 命令、不继承数据库或机器人凭据。缺失安装、超时、异常和超长结果均明确报错，不回退到旧模板。

代码发布仍执行原来的 bash scripts/update.sh；该脚本没有修改。首次接入后须完成上述私有安装步骤。本次版本升级后执行同一安装命令并追加 --upgrade，自动备份已有安装。

验证：在 backend 目录执行 .venv/bin/python -m unittest scripts.test_task_daily_report_skill scripts.test_task_ledger。设置 TASK_DAILY_REPORT_SKILL_ARCHIVE=/path/to/package.zip 可使用真实安装包验证；无该变量时使用最小接口夹具，CI 不依赖私有包。
