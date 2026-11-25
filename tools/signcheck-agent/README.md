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
5. 双击 `run.cmd` 常驻运行。看到 `签名校验机启动 base=… agent=…` 后，平台页面顶部会显示「校验机在线」。

需要开机自启时，用「任务计划程序」建一个「登录时」触发、操作为 `run.cmd` 的任务即可。可以同时开多台校验机，平台按先到先得分配，互不重复。

## 三、排障

| 现象 | 原因与处理 |
|---|---|
| 页面提示「签名校验未启用」 | 平台 `backend/.env` 没配 `SIGNCHECK_AGENT_TOKEN`，或配置后没重启后端 |
| 页面显示「校验机离线」，上传后一直排队 | 校验机上 `run.cmd` 没在运行，或 `BASE_URL` 不通。排队超过 30 分钟的记录会自动判失败 |
| agent 日志「平台拒绝了令牌（401）」 | 两端 `SIGNCHECK_AGENT_TOKEN` 不一致（注意首尾空格） |
| 结果「缺少运行依赖」/ 退出码 `0xc0000135`、`14001`、SideBySide | 校验机没装 Visual C++ 2008 **x86** 运行库 |
| 结果「未返回可识别的状态」，原始输出是问号或乱码 | 校验机「非 Unicode 程序的语言」不是中文（简体，中国）。在 控制面板 → 区域 → 管理 里修改并重启 |
| agent 启动即报「缺少校验程序文件」 | `verifier\` 目录不完整，按第二节第 2 步补齐 |
| 文件名含特殊字符的文件校验报「文件不存在」 | 原校验程序按系统代码页读路径，GBK 表示不了的字符（如部分 emoji、生僻字）会失败，改个文件名再传 |

校验过程中强制结束 agent，可能在校验机 `%TEMP%` 里留下 `signcheck-*` 目录，可直接删除。

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
| `verdict` | `passed` 验签通过 / `invalid` 有签名无效 / `expired` 有签名过期 / `unsigned` 四项均无签名 / `partial` 只拿到部分结果 / `error` |
| `signatures` | 四项明细 `[{kind, label, state, detail}]`，`state` 取 `valid` / `unsigned` / `invalid` / `expired` / `unsupported` / `error` / `missing` |
| `stdout` / `stderr` / `exit_code` | 校验程序原始输出与退出码 |

流水线门禁一般判断 `status == "completed" && verdict == "passed"`。文件名含中文或空格时请做 URL 编码。

## 五、开发

```bash
node --test tools/signcheck-agent/agent.test.mjs     # agent 单测（假校验程序 + 假平台，Linux/Mac/Windows 都能跑）
cd backend && python -m scripts.test_signcheck       # 后端：输出解析 + 全链路
```

数据约定：上传的文件只在校验期间保留在平台 `backend/storage/signcheck/`，出结果（或超时判失败、删除记录）即删；`sign_check` 表只存文件名、大小、sha256、四项状态和原始输出。
