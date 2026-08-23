# 设计:ai-eval-cli-yt 合并进 qalab-runner(统一执行器、一处配置)

- 日期:2026-08-23
- 状态:已评审(用户 /goal 追加需求 + 逐项确认策略)
- 所属:对话测评链路 · 子项C(执行器统一)
- 范围:把 `D:\code\ai-eval-cli-yt`(对话测评执行器,CommonJS)搬进 `tools/qalab-runner/eval/`,与功能测试点执行器(runner.mjs,ESM)共存一目录、共享一份 `.env`,消除"两个文件夹各自配置"的痛点。

## 1. 背景与问题

平台有两个本地执行器,当前分处两地、各配一套:
- `tools/qalab-runner/`(仓库内,ESM/.mjs,零依赖):跑 **exec_queue**(功能测试点 gui/api/cli,调 Claude Code)。
- `D:\code\ai-eval-cli-yt`(仓库外,CommonJS/.js,依赖 playwright/commander 等):跑 **eval_queue**(对话测评,驱动纳米Work对话+抓 WS 轨迹)。

**痛点(用户提出)**:两者高度同源——都连纳米Work桌面端(CDP 9222)、都轮询同一平台、配置几乎重叠(BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT)。但要在两个文件夹各改一次配置,易漂移、易漏配(本次联调就因 ai-eval-cli-yt 的 .env 缺 BASE_URL/RUNNER_TOKEN 卡过)。

## 2. 目标与非目标

**目标**
- ai-eval-cli-yt 整体搬进 `tools/qalab-runner/eval/`(**保持 CommonJS,不重写 ESM**),纳入平台仓库 git 管理。
- **共享一份配置**:`tools/qalab-runner/.env`(上级)为唯一配置源;eval/ 侧 .env 加载器改为读上级 .env(FEISHU_* 等 eval 专属项追加进上级 .env.example 占位)。
- **各自进程、共享 .env**:功能测试点走 `runner.mjs`,对话测评走 `eval/` 的 `node bin/ai-eval.js platform`;加 `run-eval.cmd`/`run-eval.sh` 启动脚本(从上级 .env 注入环境变量)。
- 保留 A 子项已做的自适应改动(work-frame.js 等——搬的是本机改好的最新版)。

**非目标(YAGNI)**
- **不做 ESM 重写/深度融合**(不把对话测评能力塞进 runner.mjs)——策略明确选"共存不重写"。
- **不做一键拉双进程**(用户选"各自进程")。
- 不改平台后端(eval_queue/exec_queue 各自照旧)。
- 不动 ai-eval-cli-yt 的 accounts/output 等运行时数据(不搬登录态,部署时现录)。
- 原 `D:\code\ai-eval-cli-yt` 目录去留由用户定(本子项只做搬入;搬入后以仓库内为准)。

## 3. 关键决策(用户已确认)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 合并策略 | **共存一目录 + 共享配置**(不 ESM 重写)。 |
| 2 | 落点 | `tools/qalab-runner/eval/`(ai-eval-cli-yt 的 src/bin/config/package.json 等搬入)。 |
| 3 | 启动 | **各自进程**:runner.mjs(功能点)与 eval/ 的 ai-eval platform(对话测评)分别启动。加 run-eval.cmd/.sh。 |
| 4 | 配置 | **eval 读上级 .env**:`tools/qalab-runner/.env` 唯一源;eval/ 加载器改读上级;FEISHU_*/DEEPSEEK_* 等追加进上级 .env.example。 |
| 5 | 依赖 | eval/ 保留自己的 package.json + node_modules(CommonJS 依赖 playwright 等,与上级 mjs 零依赖不冲突;各自 npm install)。 |
| 6 | 搬运范围 | 搬:src/(12 文件)、bin/(ai-eval.js 主;dump-*/record-accounts 辅)、config/default.config.js、package.json、package-lock.json、部署手册.md、README.md、关键 .bat(运行测评/桌面并发验证/录制账号)。**不搬**:node_modules(重装)、accounts/output/evidence(运行时)、_test/eng.traineddata(OCR 大文件,按需)。 |
| 7 | .gitignore | eval/ 下 node_modules/accounts/output 加入忽略(不入 git)。 |

## 4. 搬运清单与结构

搬入后 `tools/qalab-runner/eval/` 结构:
```
eval/
  src/            # 12 文件(desktop-pool/desktop-runner/dialog-runner/task-watcher/
                  #   platform-client/ws-trace/work-frame/logger/reporter/
                  #   diagnostic-reporter/feishu-sheet/context-pool)
  bin/            # ai-eval.js(主) + dump-models/dump-tasklist/record-accounts
  config/         # default.config.js
  package.json / package-lock.json
  README.md / 部署手册.md
  运行测评.bat / 桌面并发验证.bat / 录制账号.bat  # 常用入口(路径改相对本目录)
  .gitignore      # node_modules/ accounts/ output/ .env
```
上级新增:
```
tools/qalab-runner/
  run-eval.cmd    # Windows:从 .env 注入环境变量 → cd eval → node bin/ai-eval.js platform
  run-eval.sh     # Mac/Linux 同款
  .env.example    # 追加 eval 专属项(FEISHU_APP_ID/SECRET、DEEPSEEK_*、eval 专属可选项)
```

## 5. 配置共享(eval 读上级 .env)

- eval/ 侧 .env 加载入口(bin/ai-eval.js 的 loadDotEnv,现读 `cwd/.env` 与 `__dirname/../.env`)**改为优先读上级 `tools/qalab-runner/.env`**:
  - 候选顺序:`../.env`(上级,即 qalab-runner/.env)→ 兜底 `eval/.env`(过渡)。
  - 保持"已存在环境变量不覆盖"语义。
- run-eval.cmd/.sh 亦从上级 .env 读 BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT 注入(与 run.cmd 同款读法),再调 eval 的 ai-eval.js platform。
- 上级 .env.example 追加注释块:eval 专属项(FEISHU_APP_ID/FEISHU_APP_SECRET;DEEPSEEK_* 若 eval 侧用;NAMICLAW_EXE 复用同名——两执行器同一客户端同一路径,正好共享)。
- **命名对齐坑**:ai-eval-cli-yt 的 platformApi 读 `BASE_URL/RUNNER_TOKEN/RUNNER_ID`(与 qalab-runner 完全同名!)——天然共享,无需改名。NAMICLAW_EXE/CDP_PORT 同名同义,也共享。

## 6. 启动脚本(run-eval.cmd/.sh)

`run-eval.cmd`(仿现有 run.cmd 的 .env 读取 + 启动):
```
读 tools/qalab-runner/.env → 设环境变量 → cd eval → node bin/ai-eval.js platform %*
```
- 透传参数(--once/--limit 等)。
- 首次提示:若 eval/node_modules 不存在,提示先 `cd eval && npm install`。

## 7. 影响面与风险

- **隔离**:纯文件搬运 + 配置指向调整;不改 runner.mjs、不改平台后端、不改 ai-eval 业务逻辑(搬的是本机最新版含 A 自适应)。两执行器各自进程、互不干扰。
- **风险1(依赖体积)**:eval/ 带 playwright 等重依赖,node_modules 大。缓解:不入 git(.gitignore),部署时 npm install。
- **风险2(.env 路径)**:eval 加载器改读上级,若上级 .env 不存在需兜底(读 eval/.env 或纯环境变量)。缓解:候选链 + 保持"环境变量优先"。
- **风险3(相对路径)**:.bat 内路径、config 内相对路径(accounts/output/config)搬目录后可能失效。缓解:.bat 用 `cd /d %~dp0eval`;config 相对 cwd 的路径(accounts/output)在 eval/ 下运行即对。
- **风险4(真验证)**:搬入后需在本机 `cd tools/qalab-runner/eval && npm install` 后 `run-eval.cmd --once` 跑通(读上级 .env、连客户端、拉任务)。

## 8. 迁移与验证

1. 文件搬运(cp ai-eval-cli-yt 的 src/bin/config/package*.json/README/部署手册/关键.bat → tools/qalab-runner/eval/)。
2. eval/.gitignore 建好(node_modules/accounts/output/.env)。
3. 改 eval/bin/ai-eval.js 的 loadDotEnv:优先读上级 tools/qalab-runner/.env。
4. 写 run-eval.cmd/.sh。
5. 上级 .env.example 追加 eval 专属项。
6. 本机验证:`cd tools/qalab-runner/eval && npm install`;配好上级 .env;`node bin/ai-eval.js platform --once`(或 run-eval.cmd --once)拉一轮验证读上级 .env + 连客户端 + 拉任务通。
7. git add eval/ 源码(不含 node_modules/accounts/output),提交。

## 9. 交付清单
- [ ] 搬 ai-eval-cli-yt 源码(src/bin/config/package*.json/README/部署手册/关键.bat)→ tools/qalab-runner/eval/
- [ ] eval/.gitignore(node_modules/accounts/output/.env)
- [ ] eval/bin/ai-eval.js loadDotEnv 改优先读上级 .env
- [ ] tools/qalab-runner/run-eval.cmd + run-eval.sh
- [ ] tools/qalab-runner/.env.example 追加 eval 专属项
- [ ] tools/qalab-runner/README.md 补 eval 路(对话测评)启动说明
- [ ] 本机验证(npm install + run-eval --once 读上级 .env 跑通)
- [ ] git 提交(精确 add eval/ 源码,不含运行时/依赖)

## 10. 说明
- 原 `D:\code\ai-eval-cli-yt` 搬入后以 `tools/qalab-runner/eval/` 为准;原目录去留用户定。
- 搬的是本机改好的最新 ai-eval-cli-yt(含子项A 的 work-frame 自适应、子项2 的 platform-client/ws-trace 等)。
- 工作区无关既存改动(run.cmd/__MACOSX/qalab-runner.zip)全程不动。
