# 提测执行（md 任务）——把整份提测文档下发到执行机

## 解决什么

现在提测后的动作是：工作机上让 Claude Code 读 diff → 写一份 md 用例 → 通过 IM 发给测试机 →
人工在测试机上切 hosts、把 md 喂给 codex → codex 执行、报缺陷 → 人工收结果。

这条链路里"AI 分析"和"AI 执行"都能用，卡住的是三处人工：**跨机器投递、切环境、收结果**。
本模块把这三处搬进平台：页面上传 md → 选执行机与环境分组 → 执行机自动切 hosts、跑 agent、回写结果。

## 与现有 exec-queue 的边界

| | exec-queue（已有） | md_run（本模块） |
|---|---|---|
| 下发单位 | 结构化用例（gui/api/cli，逐条断言） | 一整份 md 文档 |
| 结果回流 | 执行结果页 / 质量门禁 / 失败聚类 / 发版质量 | 独立页「提测执行」 |
| 数据表 | `exec_run` | `md_run`（新表） |
| 队列 | `/api/exec-queue` | `/api/md-runs/queue`（独立轮询） |

**不共用表**是刻意的：`exec_run` 被统计、门禁、失败聚类、回归智选、发版质量、自动重试等
约三十处引用，混入"整份文档自由执行"会污染这些口径。做法与性能采集（`perf_run` + `/api/perf/queue`）一致。

## 流程

```
前端「提测执行」页 ──POST /api/md-runs/batch──► 每台执行机一条 md_run(pending)
                                              │
执行机 runner（MD_TASK_ENABLED=1）             │
  GET /api/md-runs/queue?runner=<id> ◄────────┘
  POST /queue/{id}/claim                  → running
  ① 快照系统 hosts
  ② 写本机分组 hosts-groups/<env_group>.hosts
  ③ 校验域名解析（期望值取自分组文件；不过就中止，不启动 agent）
  ④ 完全退出并重启 NAMICLAW_EXE，等待 CDP 就绪后调 agent 执行整份 md（默认 `codex exec`，参数可配）
  ⑤ 读 agent 产出的 result.md → 抽失败用例与缺陷候选
  ⑥ 无论成败：还原 hosts
  ⑦ PATCH /queue/{id} 回写 verdict/result_md/findings/env_check
  期间每 60s POST /queue/{id}/heartbeat → 感知平台取消
```

## 多环境怎么配

一个环境一个文件，文件名就是任务里填的 `env_group`：

```
~/qa-workspace/hosts-groups/
├── _base.hosts          （可选）每个环境都要有的基础行，如 localhost
├── online.hosts         线上：只写注释，不放任何域名映射
├── test.hosts           测试环境
└── pre.hosts            预发环境
```

仓库里 `tools/qalab-runner/hosts-groups.example/` 有这四份的样例，拷一份改名即可。

**写入内容 = 基础行 + 该环境的映射行。** 所以分组文件里只写业务域名，
不必复制 localhost 那几行——SwitchHosts 导出来是什么样就放什么样。
不建 `_base.hosts` 时用内置三行（`127.0.0.1 localhost`、`::1 localhost`、broadcasthost）。

**"线上"不是"跳过切换"，而是一个真实的空分组。** 写进去会清掉此前所有托管映射。
任务里必须显式写 `env_group: online`，不要留空——留空会跳过整个切换流程，
而机器上若还残留上一轮测试的映射，你测的就是"假线上"。

### 先自检，再发任务

```powershell
cd D:\code\test-management-platform\tools\qalab-runner
node bin\hosts-check.mjs                  # 列出每个分组的映射，检查文件名/IP 有没有写错
node bin\hosts-check.mjs test             # 真切一次：快照 → 写入 → 校验解析 → 还原，并核对还原一致
```

它读同目录 `.env`，用的就是 runner 的真实配置，但不连平台、不跑 agent。
会明确打印「期望 / 实际」和「还原前后 hosts 指纹是否一致」——
比发一个任务再从结果里反推快得多，也不占执行机。

校验只看该分组文件里出现的域名。`online.hosts` 里没有 `api.n.cn`，
所以线上任务不做解析校验（没有可比的期望值）——这是刻意的：
线上没有"应该在哪个 IP"的先验，硬编一个反而会误判。

页面可选择 online、pre、test。执行机优先读同名文件；尚未改名时，pre 和 test 分别兼容 work-staging.hosts 和 work-test.hosts。旧任务继续按原分组读取。

## 敏感内容在哪

- **hosts 分组（真实 IP）只在执行机**：`MD_GROUPS_DIR` 下的 `<分组名>.hosts`。平台只存分组名。
- **账号密码只在执行机**：md 里写别名，agent 在本机账号文件里查。
- **agent 完整日志只在执行机**：`MD_RUNTIME_DIR/md-runs/md-<id>/agent.log`，不回传。
- 平台存 md 原文、agent 产出的结果文档、结构化缺陷候选。仓库是公开的，但库不是。

## 缺陷怎么上报

执行机回传 `result.md`。平台按用例表的「结论」列统计失败数，从「## 产品缺陷」下的三级标题提取缺陷候选。每条缺陷的复现步骤、预期和实际结果放在该标题下面。没有产品缺陷时，本节填写「无」。一条负向用例可以执行失败，同时产生零条缺陷。

人在「提测执行」详情页复核后点「上报到极库云」→ `POST /api/md-runs/{id}/report-bugs`
通过附件技能包的 `geelib-bug-report/scripts/create-case.mjs` 建单，使用当前报送人的个人 SSO 授权和任务的项目映射。服务端需要 Node.js 18+。每条成功回执单独保存为 `external_ref`，已上报项会跳过。明确被拒的可重试，回执不明的会提示人工核对。

刻意不做自动上报：环境问题、偶发失败自动灌进缺陷库，会迅速让缺陷单失去可信度。
要全自动可以复用 `GEELIB_AUTO_REPORT` 的开关思路再加，默认不写死。

## 需要执行机做什么（一次性）

1. `.env` 里 `MD_TASK_ENABLED=1`。**默认 0**：runner 靠自升级分发到所有执行机，
   新能力必须由人在该机显式打开，否则每台机器都会去抢这类任务。
2. 放好 hosts 分组文件（`MD_GROUPS_DIR`），从 SwitchHosts 导出即可。
3. 装提权助手：它只做三件事（快照/应用/还原系统 hosts），runner 与 agent 本身都不提权。
   两份脚本随 runner 分发在 `bin/` 下（`qa-hosts-helper-mac.sh` / `qa-hosts-helper-win.ps1`）。
   - Mac：装到 `/usr/local/bin/qa-hosts-helper`，sudoers 只放行这一条命令
   - Win：建计划任务「qa-hosts-helper」指向 `qa-hosts-helper-win.ps1`，最高权限、按需运行、**不要 SYSTEM**

   Windows 上建任务的正确写法（`schtasks /ru` 不给密码会报"拒绝访问"，用 PowerShell 的
   Interactive 登录类型可以避免）：

   ```powershell
   $act = New-ScheduledTaskAction -Execute "powershell.exe" `
     -Argument "-NoProfile -ExecutionPolicy Bypass -File <runner目录>\bin\qa-hosts-helper-win.ps1"
   $pr  = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
     -LogonType Interactive -RunLevel Highest
   Register-ScheduledTask -TaskName "qa-hosts-helper" -Action $act -Principal $pr -Force
   ```

   `LogonType Interactive` = 只在该用户登录时运行（需要桌面会话），`RunLevel Highest` = 提权。
   创建这条任务本身要求当前窗口已提权，否则同样报"拒绝访问"。

   runner 启动时会做一次自检（只读快照 hosts），失败会打
   `!! hosts 提权助手不可用` 并说明原因 —— 比等到切环境时才发现好。

   **替代方案：直写模式。** 专用测试机上，直接给 runner 账号 hosts 写权限，跳过辅助层：

   ```powershell
   icacls "C:\Windows\System32\drivers\etc\hosts" /grant "$($env:USERNAME):(M)"
   ```

   然后 `.env` 设 `MD_HOSTS_DIRECT=1`，计划任务可以整个不要。

   代价要说清楚：agent 与 runner 同账号，它理论上也能改 hosts，权限隔离没了；
   剩下的是「切完校验解析 → 跑完还原 → 崩溃自愈」这三道。专用测试机上值得换来
   计划任务/job 文件/账号匹配这一整层故障面的消失。
4. 配置 `MD_VERIFY_DOMAINS`（需要校验解析的域名）与 `MD_CODEX_*`（agent 调用方式）。

### Windows：agent 命令在内置包目录里

桌面版 Codex 的 CLI 在 Appx 包内，**不进 PATH**，路径带版本号：

```
C:\Program Files\WindowsApps\OpenAI.Codex_<版本>_x64__<hash>\app\resources\codex.exe
```

所以 `MD_CODEX_BIN` 写 `appx:OpenAI.Codex`：runner 每次启动扫 WindowsApps，取版本号最大的、
架构匹配的包，升级后自动跟着走。也可以直接写绝对路径，但升级会失效。

实测环境（2026-11，Windows 11）：

| 安装形态 | 版本 | `workspace-write` 沙箱 |
|---|---|---|
| Appx 包内 `app\resources\codex.exe` | 0.171.0-alpha.13.2 | ❌ `exec_command` 报 `Failed to create unified exec process: runner handshake task failed` |
| Appx 包内 + `-c features.unified_exec=false` | 同上 | ✅ |
| npm 全局 `%APPDATA%\npm\codex.cmd` | 0.172.0 | ✅ 默认即可 |

结论：**优先用 npm 全局安装的 CLI**（`MD_CODEX_BIN=npm:codex`）。与桌面版升级解耦，
不依赖 WindowsApps 的 ACL，也不用为绕一个 alpha 的 bug 关功能开关。

排查过程留档，避免以后重复踩：先用 `-s danger-full-access` 确认 CLI 本身能执行命令
（能跑 → 问题在沙箱或 exec 通道），再分别关 `unified_exec`、换安装形态，一步一变。
所有 MCP（`cua_repl` / `node_repl`）同时在启动时失败，也是同一原因——它们同样要起子进程。

登录：`codex login status` → `Logged in using ChatGPT`。Appx 版与 npm 版共用 `CODEX_HOME` 下的凭据，
不必重新登录；但**桌面版应用内的登录状态不等于 CLI 已登录**，新机器先跑一次 `codex login`。

默认参数按该版本的 `exec` 选项：

```
exec --skip-git-repo-check --sandbox workspace-write -c approval_policy=never
```

若不得不用 Appx 版（0.171-alpha），追加 `-c features.unified_exec=false`。

- `--skip-git-repo-check`：工作目录不是 git 仓库也能跑
- `--sandbox workspace-write`：agent 要写 `result.md`，只读模式写不出来
- `-c approval_policy=never`：非交互下不能停在等授权，否则表现为超时
- **不要加 `--ignore-user-config`**：那会丢掉 `~/.codex/config.toml` 里的 MCP/skill 配置，
  而 agent 正是靠这些去操作客户端

另外 runner 会额外传 `-o <工作目录>/agent-last-message.md`：agent 忘了按约定写 `result.md` 时，
用 CLI 落盘的最后一条回复兜底解析，不至于整轮白跑。

### 沙箱边界（实测）

`--sandbox workspace-write` 的可写范围是 **`-C` 工作目录 + `/tmp` + `$TMPDIR`**，其余一律只读。
实测 agent 会明确告诉你"D 盘只有读取权限，放可写目录我才能写 result.md"。

- 任务文档与结果都在 runner 的工作目录里（`MD_RUNTIME_DIR/md-runs/md-<id>/`），默认没问题。
- 需要把截图、日志、附件落到别处，用 `MD_CODEX_ADD_DIRS` 显式加，**不要**换成
  `--dangerously-bypass-approvals-and-sandbox`：那等于让无人值守的 agent 拥有整机权限。
- 被测客户端目录只读不影响启动与 GUI 操作（MCP 工具不受 shell 沙箱约束），
  只有 agent 自己用 shell 往那儿写才会失败。

### 无人值守时 agent 会反问

实测过一次：给的路径是占位符，agent 没去猜，直接回「请发一下完整路径」然后结束 —— 整轮空跑。
所以 prompt 里写死了：不得提问、不得等确认，缺信息按最合理理解继续，
确实做不下去就把原因和已完成部分写进 `result.md` 再结束。

如果以后仍常见"什么都没做就结束"，优先检查任务文档本身是否缺少必需信息（账号、客户端版本、入口），
而不是放松 prompt 约束。

agent 要联网：代理必须给到 **runner 进程**（Windows 上实测 Clash `127.0.0.1:7897`，`qihoo.net` 走 `NO_PROXY`）。
没配代理的表现是超时而不是报错，很容易被误判成用例失败。

## 状态与取消

`pending → running → passed / failed / blocked / canceled`

- `blocked` = 环境或 agent 本身没跑起来（分组不存在、校验不过、agent 退出码非 0、超时、平台取消）。
  与"产品缺陷"区分开，避免环境问题被当成 bug 统计。
- 取消：平台置 `canceled`；执行机在下一次心跳看到后杀 agent 并还原环境。
  平台不强制杀进程——在切过 hosts 的机器上硬杀，环境会停在测试分组。

## 前端

菜单：功能测试 → 提测执行（`/md-tasks`）。
支持同时选择多台执行机，每台独立认领和回写结果。支持选文件或粘贴；选 md 时会把 frontmatter 里的 `env_group / client_version / req / title`
带进表单，省一次手填。

## 已知边界

- runner 会独占桌面（与 gui 用例同一把锁），所以只适合在这个专用测试机上开。
- agent 的调用参数随 codex 版本可能变化，`MD_CODEX_ARGS` 留了可配置口子。
- 结果表使用「用例」「结论」「说明」三列，结论填写 `pass`、`fail` 或 `blocked`。空报告或只有表头的报告会记为阻塞。
