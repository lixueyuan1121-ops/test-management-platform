# md-submit —— 工作机直接下发提测文档

Claude Code 分析完 diff、写好用例 md 后，一条命令下发到平台「提测执行」，不用开页面上传。
纯 Node 18+，零依赖；不在 runner 分发包里，不会被推到执行机。

## 一次性准备

```powershell
mkdir $HOME\.qalab
copy md-submit.example.json $HOME\.qalab\md-submit.json   # 改 project_id、runners 映射
node md-submit.mjs login                                   # 输平台账号密码；只缓存令牌（约 7 天），密码不落盘
```

`runners` 是「platform → 执行机 runner_id」的映射，runner_id 取自平台「我的设备」。
`project_id` 填了，之后才能在页面上把缺陷候选上报极库云。

## 用法

```powershell
node md-submit.mjs 任务.md              # 按 frontmatter 下发
node md-submit.mjs 任务.md --dry-run    # 只看会下发什么
node md-submit.mjs 任务.md --wait       # 等执行完，打印结论和结果文档
```

md 开头的 frontmatter（正文照常写）：

```
---
req: REQ-1234
env_group: test        # test | pre | online —— 线上也必须显式写 online
client_version: 3.2.1.88
platform: win          # mac | win | both，按配置里的 runners 映射找执行机
---
```

命令行可覆盖：`--runner a,b` `--env pre` `--project 3` `--title …` `--req …` `--timeout 90`（--wait 的分钟数）。

退出码：`0` 已下发（`--wait` 时为全部通过）、`1` 有失败用例、`2` 阻塞/取消/超时、`3` 参数或登录问题。

## 接到 Claude Code

在工作仓库的 `CLAUDE.md` 里加一段即可：

```
写完提测用例 md 后（开头必须带 frontmatter：req / env_group / client_version / platform），执行
  node <本目录>/md-submit.mjs <md 路径> --dry-run
把输出给我确认；我同意后再去掉 --dry-run 下发。不要自行加 --env online。
```

先 dry-run 再下发是故意的：下发会真的占用测试机并切它的 hosts。
无人值守场景可设环境变量 `QALAB_USERNAME` / `QALAB_PASSWORD` 代替 `login`。
