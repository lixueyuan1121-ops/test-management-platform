# qalab-runner 设备端部署指南

把测试平台勾选的用例,拉到**你自己的电脑**上执行、回写结果。本文面向"要在自己机器上跑 runner"的成员。

## 一、要拷什么 / 不拷什么

把整个 `tools/qalab-runner/` 文件夹拷到你的电脑即可(或 `git clone` 后进该目录)。注意:

- ❌ **不要拷** `.env`(每台配置不同,到本机重新建)、`gui-mcp/node_modules`(体积大、平台相关,本机重装)。`.gitignore` 已忽略。
- ✅ **要装**(见第三节):Node 18+、gui-mcp 依赖(仅 gui/e2e 需要)、Claude ​Code CLI、被测客户端(仅 gui 需要)。

## 二、前置依赖

| 依赖 | 用途 | Mac | Windows |
|---|---|---|---|
| **Node.js 18+** | runner 运行时(用内置 fetch) | `brew install node` 或官网 | 官网安装包,勾选加入 PATH |
| **Claude ​Code CLI** | 执行用例的大脑,须已登录 | 装好后 `claude -p "echo hi"` 能无交互输出 | 同左;确保 `claude` 在 PATH |
| **gui-mcp 依赖** | 仅跑 gui/e2e 用例需要 | 在 `gui-mcp/` 下 `npm install` | 同左 |
| **被测客户端 Namiwork/namiclaw** | 仅跑 gui/e2e 用例需要 | `/Applications/Namiwork.app` | 安装后记住 .exe 路径 |

> runner 本身**零第三方依赖**(纯 Node 内置模块)。只跑 api/cli 用例,可跳过 gui-mcp 依赖与被测客户端。

## 三、一次性准备

```bash
# 1) 装 gui-mcp 依赖(playwright-core 不下载浏览器,连的是被测客户端自带 Chromium;仅 gui/e2e 需要)
cd qalab-runner/gui-mcp && npm install && cd ..

# 2) 确认 Claude Code 能无交互跑
claude -p "echo hi"

# 3) 从样例建配置
cp .env.example .env        # Windows: copy .env.example .env
```

## 四、拿设备 token 并填 .env

1. 登录测试平台 → 左侧 **任务执行 → 我的设备** → **注册设备**(填 `runner_id`,如 `你的名-mac`;和设备名)。
2. 复制弹出的**专属 token**(只显示一次)。
3. 编辑本机 `.env`:

```ini
BASE_URL=http://11.120.81.7:4173          # 平台后端地址(问管理员)
RUNNER_TOKEN=<第2步复制的设备专属 token>
RUNNER_ID=你的名-mac                       # 必须与注册时填的 runner_id 完全一致
NAMICLAW_EXE=/Applications/Namiwork.app/Contents/MacOS/Namiwork   # 不跑 gui 可留空
CDP_PORT=9222
CLAUDE_TIMEOUT_MS=240000
```

> Windows 的 `NAMICLAW_EXE` 填如 `D:\Program Files\namiclaw\Application\namiclaw.exe`。

## 五、启动

| | Mac / Linux | Windows |
|---|---|---|
| 首次 | `chmod +x run.sh` | — |
| 启动 | `./run.sh` | `run.cmd`(或 `node runner.mjs`) |
| 只验连通(不真跑) | `./run.sh --dry` | `run.cmd --dry` |

启动后应看到:`runner 启动 base=... runner=<你的id> dry=false`,之后每 5s 轮询。

## 六、平台侧怎么下发到你

在**用例库 / 已采纳用例 / 任务清单**勾选用例 → 执行机下拉选**你自己的设备** → 发送。你的 runner 会拉到并执行、回写结果。

## 七、平台差异与注意

- **冷启动被测客户端**:跑 gui 用例前,runner 会先杀掉旧客户端进程,再带 `--remote-debugging-port` 重启它(单实例锁所致)。**这会打断你手动开的客户端**。Mac 用 `pkill`+spawn、Windows 用 PowerShell `Start-Process`,代码已分别适配。
- **登录态**:被测客户端需保持登录(冷启动后一般沿用已保存会话)。
- **token 泄露/轮换**:token 泄露或换机,在「我的设备」**重置 token** 后更新 `.env`。
- **manual 用例不下发**:人工/不可自动化用例平台不会派给设备。
