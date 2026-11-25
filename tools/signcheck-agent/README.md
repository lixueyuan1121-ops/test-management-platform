# signcheck-agent —— Windows 签名校验机

平台「设备与工具 → 签名校验」页面背后的执行端。平台后端跑在 Linux 上，而校验程序 `360Base_test.exe` 是 32 位 Windows 程序，所以需要一台 Windows 机器常驻运行本 agent：

```
浏览器 ──上传文件──▶ 平台后端(Linux) ◀──每 3s 轮询── agent.mjs(Windows)
                      │  sign_check 表(pending)        │ 认领 → 下载文件到 %TEMP%\signcheck-*
                      │                                │ 跑 verifier\360Base_test.exe <文件>
浏览器 ◀──轮询结果── │ ◀──────回传原始输出──────────── │ 删除临时目录
                      └ 解析四项签名状态、删除上传文件
```

agent 只向平台发起请求，校验机不需要对外开放端口。四项签名：微软数字签名（EmbeddedSign）、360 旧签名（Old360Sign）、360 超级签名（New360Sign）、360 第三代签名 S3（S3360Sign）。

## 一、平台侧（一次）

在平台服务器的 `backend/.env` 里加一行并重启后端（`bash scripts/update.sh`）：

```ini
SIGNCHECK_AGENT_TOKEN=<一个长随机串，例如 openssl rand -hex 32 的输出>
```

留空表示功能关闭：页面会提示未启用，上传返回 503。

## 二、校验机侧

校验机要求：Windows 10/11 64 位；已安装 **Microsoft Visual C++ 2008 x86 运行库**（校验程序是 32 位的，x64 运行库不能代替）；系统「非 Unicode 程序的语言」为**中文（简体，中国）**；Node.js 18 及以上；能访问平台地址。

1. 把整个 `tools/signcheck-agent/` 目录拷到校验机，例如 `C:\Tools\signcheck-agent`。路径尽量短、不要太深。
2. 确认 `verifier\` 目录里有 5 个文件：`360Base_test.exe`、`360Base.dll`、`pbusc_d.dll`、`sesafe.dll`、`sesafe64.dll`。它们来自 Windows-SignCheck 工具包的 `verifier` 目录，属于内部工具，**已被 .gitignore 排除、不随仓库分发**，需要人工拷贝。
3. 复制 `.env.example` 为 `.env`，填 `BASE_URL`（平台地址）和 `SIGNCHECK_AGENT_TOKEN`（与第一节相同）。
4. 先本机自检，确认运行库和区域设置没问题（不连平台）：

   ```bat
   run.cmd --selftest C:\Windows\System32\notepad.exe
   ```

   能看到四行 `EmbeddedSign(微软数字签名)-> 签名状态：…` 即正常。
5. 双击 `install.cmd`（会请求管理员权限）。它把 agent 注册成名为 `SignCheckAgent` 的后台计划任务并立即启动，窗口里会打印日志末尾几行；看到 `签名校验机启动 base=… agent=…` 即成功，平台页面顶部随后显示「校验机在线」。

安装只做这一次。之后 agent 随 Windows 开机自动启动，以 SYSTEM 身份在后台运行：没有窗口、不需要有人登录、异常退出会自动重启。日志在 `logs\agent.log`（超过约 5 MB 自动轮转）。

| 操作 | 做法 |
|---|---|
| 改了 `.env`（换 token / 平台地址） | 重新双击 `install.cmd` 即可生效 |
| 看运行情况 | 打开 `logs\agent.log`，或在「任务计划程序」里看 `SignCheckAgent` |
| 停用并移除 | 双击 `uninstall.cmd` |
| 临时前台调试（看实时输出） | 先 `uninstall.cmd`，再双击 `run.cmd`；不要两种方式同时开 |

可以在多台 Windows 机器上各装一份，平台按先到先得分配，互不重复。

## 三、排障

| 现象 | 原因与处理 |
|---|---|
| 页面提示「签名校验未启用」 | 平台 `backend/.env` 没配 `SIGNCHECK_AGENT_TOKEN`，或配置后没重启后端 |
| 页面显示「校验机离线」，上传后一直排队 | 校验机没开机、没执行过 `install.cmd`，或 `BASE_URL` 不通；看 `logs\agent.log` 最后几行。排队超过 30 分钟的记录会自动判失败 |
| agent 日志「平台拒绝了令牌（401）」 | 两端 `SIGNCHECK_AGENT_TOKEN` 不一致（注意首尾空格） |
| 结果「缺少运行依赖」/ 退出码 `0xc0000135`、`14001`、SideBySide | 校验机没装 Visual C++ 2008 **x86** 运行库 |
| 结果「未返回可识别的状态」，原始输出是问号或乱码 | 校验机「非 Unicode 程序的语言」不是中文（简体，中国）。在 控制面板 → 区域 → 管理 里修改并重启 |
| agent 启动即报「缺少校验程序文件」 | `verifier\` 目录不完整，按第二节第 2 步补齐 |
| 文件名含特殊字符的文件校验报「文件不存在」 | 原校验程序按系统代码页读路径，GBK 表示不了的字符（如部分 emoji、生僻字）会失败，改个文件名再传 |

校验过程中强制结束 agent，可能在临时目录里留下 `signcheck-*` 目录，可直接删除（后台任务以 SYSTEM 运行，临时目录是 `C:\Windows\Temp` 或 `C:\Windows\SystemTemp`）。

## 四、脚本 / 流水线调用

复用平台的 CI 钩子令牌（`backend/.env` 的 `CI_HOOK_TOKEN`，见 `docs/ci-integration.md`）：

```bash
# 上传并最多等 120 秒拿结果
curl -sS -X POST "$PLATFORM/api/hooks/signcheck?filename=setup.exe&wait=120" \
  -H "X-CI-Token: $CI_HOOK_TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @setup.exe

# wait 超时或 wait=0 时，拿返回的 data.id 自行轮询
curl -sS "$PLATFORM/api/hooks/signcheck/$ID" -H "X-CI-Token: $CI_HOOK_TOKEN"
```

返回 `data` 的关键字段：

| 字段 | 含义 |
|---|---|
| `status` | `pending` 排队 / `running` 校验中 / `completed` 有结果 / `failed` 没能得到结果（看 `error`） |
| `verdict` | `passed` 验签通过（微软数字签名与 360 S3 签名**都有效**）/ `rejected` 验签不通过（这两项里有无签名）/ `invalid` 有签名无效 / `expired` 有签名过期 / `partial` 这两项没拿到明确状态 / `error` |
| `signatures` | 四项明细 `[{kind, label, state, detail}]`，`state` 取 `valid` / `unsigned` / `invalid` / `expired` / `unsupported` / `error` / `missing` |
| `stdout` / `stderr` / `exit_code` | 校验程序原始输出与退出码 |

流水线门禁一般判断 `status == "completed" && verdict == "passed"`。文件名含中文或空格时请做 URL 编码。

## macOS 安装包静态初筛

在「设备与工具 → 签名校验 → macOS 静态初筛」上传 DMG 或 ZIP，可选填预期 Team ID。平台后台自动读取包内的 Mach-O 文件，并展开支持的嵌套归档。流水线上传沿用 `platform=macos` 和可选的 `team_id`，由同一服务端执行。完成后清理安装包，页面保留明细并支持下载报告和日志。

初筛核对主 CodeDirectory 的代码页摘要、Developer ID Application 证书标识、Team ID、安全时间戳元数据和可执行文件的 hardened runtime 标记。结果分为「初筛未发现问题」「发现签名问题」「检查不完整」或执行异常。报告列出未覆盖的分区、文件或格式。证书信任、系统原生验签和正式公证需另行验证。历史 Mac 原生预检记录可继续查看。

## 五、开发

```bash
node --test tools/signcheck-agent/agent.test.mjs     # agent 单测（假校验程序 + 假平台，Linux/Mac/Windows 都能跑）
cd backend && python -m scripts.test_signcheck       # 后端：输出解析 + 全链路
```

结论口径：只有微软数字签名（EmbeddedSign）和 360 第三代签名（S3360Sign）两项都「签名有效」才算验签通过；任一项无签名即「验签不通过」。360 旧签名、超级签名只展示，不影响通过与否（但任一项「签名无效 / 过期」仍会判为无效 / 过期）。

数据约定：上传的文件只在校验期间保留在平台 `backend/storage/signcheck/`，出结果（或超时判失败、删除记录）即删；`sign_check` 表只存文件名、大小、sha256、四项状态和原始输出。
