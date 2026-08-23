# ai-eval-cli-yt 合并进 qalab-runner 实现计划(子项C)

> **For agentic workers:** 控制者内联执行。文件搬运 + 少量配置改;搬入后为 git 内改动(平台仓库),需本机 npm install + run-eval --once 验证。步骤用 checkbox 跟踪。

**Goal:** ai-eval-cli-yt 搬进 tools/qalab-runner/eval/,共享上级 .env,一份配置、统一目录。

**Architecture:** 整体复制(保持 CommonJS,不 ESM 重写)到 eval/;eval 的 .env 加载器优先读上级 tools/qalab-runner/.env;加 run-eval.cmd/.sh 启动入口。功能测试点(runner.mjs)与对话测评(eval/ai-eval)各自进程、共享配置。

**Tech Stack:** Node CommonJS(eval) + 现有 ESM runner.mjs(不动)。

## Global Constraints
- 搬的是**本机最新** ai-eval-cli-yt(含子项A work-frame 自适应)。源目录 `D:\code\ai-eval-cli-yt`。
- **不搬** node_modules / accounts / output / evidence / eng.traineddata / _test_ddddocr.py(运行时/大文件/OCR)。
- eval/ 加 .gitignore:node_modules/ accounts/ output/ .env。
- BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT 两执行器同名同义,天然共享;无需改名。
- **不碰 run.cmd**(用户未提交的既存改动)、不碰 tools/__MACOSX/、tools/qalab-runner.zip。git add 精确到 eval/ 源码 + 新增脚本,绝不 -A。
- 提交结尾 Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>。
- 本机验证:`cd tools/qalab-runner/eval && npm install`(重装依赖)后 `node bin/ai-eval.js platform --once`。

---

## Task 1: 搬运 ai-eval-cli-yt 源码到 eval/

**Files:** 复制到 `tools/qalab-runner/eval/`;新建 `eval/.gitignore`。

- [ ] **Step 1: 建目录 + 复制源码(排除运行时/依赖)**

```bash
cd /d/code/test-management-platform/tools/qalab-runner
mkdir -p eval
# 源码目录
cp -r /d/code/ai-eval-cli-yt/src eval/
cp -r /d/code/ai-eval-cli-yt/bin eval/
cp -r /d/code/ai-eval-cli-yt/config eval/
cp -r /d/code/ai-eval-cli-yt/tools eval/ 2>/dev/null || true
# 元数据/文档
cp /d/code/ai-eval-cli-yt/package.json eval/
cp /d/code/ai-eval-cli-yt/package-lock.json eval/
cp /d/code/ai-eval-cli-yt/README.md eval/
cp /d/code/ai-eval-cli-yt/部署手册.md eval/ 2>/dev/null || true
# 常用入口 bat(可选,路径搬后需确认)
cp /d/code/ai-eval-cli-yt/运行测评.bat eval/ 2>/dev/null || true
cp /d/code/ai-eval-cli-yt/桌面并发验证.bat eval/ 2>/dev/null || true
cp /d/code/ai-eval-cli-yt/录制账号.bat eval/ 2>/dev/null || true
ls -1 eval/
```
Expected: eval/ 下有 src bin config package.json 等,无 node_modules/accounts/output。

- [ ] **Step 2: eval/.gitignore**

创建 `tools/qalab-runner/eval/.gitignore`:
```
node_modules/
accounts/
output/
evidence/
.env
*.log
```

- [ ] **Step 3: 确认搬运完整 + 无运行时数据**

```bash
cd /d/code/test-management-platform/tools/qalab-runner/eval
echo "=== src 文件数 ==="; ls src/*.js | wc -l
echo "=== 关键文件 ==="; ls src/work-frame.js src/desktop-pool.js bin/ai-eval.js config/default.config.js
echo "=== 不应存在的运行时目录 ==="; ls -d node_modules accounts output 2>/dev/null || echo "干净(无运行时目录)"
```
Expected: src 12 个 js;关键文件在;无 node_modules/accounts/output。

---

## Task 2: eval 的 .env 加载器改读上级

**Files:** Modify `tools/qalab-runner/eval/bin/ai-eval.js`(loadDotEnv 函数)。

**当前代码(ai-eval.js:18-40)**候选链:`cwd/.env` → `__dirname/../.env`(= eval/.env)。

- [ ] **Step 1: 改候选链优先读上级 qalab-runner/.env**

把 `bin/ai-eval.js` 的 loadDotEnv 里 candidates 数组:
```javascript
  const candidates = [
    path.resolve(process.cwd(), '.env'),
    path.resolve(__dirname, '..', '.env')
  ];
```
改为:
```javascript
  const candidates = [
    path.resolve(process.cwd(), '.env'),
    path.resolve(__dirname, '..', '..', '.env'),  // 上级 tools/qalab-runner/.env(与功能测试点 runner 共享一份配置)
    path.resolve(__dirname, '..', '.env')          // 兜底:eval/.env(过渡/独立运行)
  ];
```
(`__dirname` = eval/bin,`../../.env` = tools/qalab-runner/.env。保持"命中第一个存在的 .env 即停"+"已存在环境变量不覆盖"语义不变。)

- [ ] **Step 2: 语法自检**

Run: `cd /d/code/test-management-platform/tools/qalab-runner/eval && node -c bin/ai-eval.js && echo OK`
Expected: `OK`。

---

## Task 3: run-eval 启动脚本 + 配置样例

**Files:** Create `tools/qalab-runner/run-eval.cmd`、`tools/qalab-runner/run-eval.sh`;Modify `tools/qalab-runner/.env.example`(追加 eval 专属项)。

- [ ] **Step 1: run-eval.cmd(仿 run.cmd)**

创建 `tools/qalab-runner/run-eval.cmd`:
```
@echo off
REM 对话测评执行器启动脚本(ai-eval platform 模式)。与 run.cmd(功能测试点)共用同一套平台配置。
REM 首次使用前:cd eval && npm install(装 playwright 等依赖,见 eval/README)。

setlocal
REM 平台连接(与 run.cmd 保持一致的值;或改为读 .env)
set "BASE_URL=https://qalab.claw.qihoo.net"
set "RUNNER_ID=lili-win"
set "RUNNER_TOKEN=REPLACE_WITH_RUNNER_TOKEN"
set "NAMICLAW_EXE=D:\Program Files\namiwork\Namiwork.exe"
set "CDP_PORT=9222"
REM 对话测评专属(飞书导出/生成引擎等,按需填)
set "FEISHU_APP_ID="
set "FEISHU_APP_SECRET="

cd /d "%~dp0eval"
echo [run-eval] starting 对话测评 executor (base=%BASE_URL% runner=%RUNNER_ID%)
node bin/ai-eval.js platform %*
```

- [ ] **Step 2: run-eval.sh(Mac/Linux)**

创建 `tools/qalab-runner/run-eval.sh`:
```bash
#!/usr/bin/env bash
# 对话测评执行器启动脚本(ai-eval platform 模式)。与 run.sh(功能测试点)共用同一套平台配置。
# 首次:cd eval && npm install。
set -e
export BASE_URL="${BASE_URL:-https://qalab.claw.qihoo.net}"
export RUNNER_ID="${RUNNER_ID:-mac-01}"
export RUNNER_TOKEN="${RUNNER_TOKEN:-REPLACE_WITH_RUNNER_TOKEN}"
export NAMICLAW_EXE="${NAMICLAW_EXE:-/Applications/Namiwork.app/Contents/MacOS/Namiwork}"
export CDP_PORT="${CDP_PORT:-9222}"
cd "$(dirname "$0")/eval"
echo "[run-eval] starting 对话测评 executor (base=$BASE_URL runner=$RUNNER_ID)"
node bin/ai-eval.js platform "$@"
```

- [ ] **Step 3: .env.example 追加 eval 专属项**

在 `tools/qalab-runner/.env.example` 末尾追加:
```
# ---- 对话测评执行器(eval/,ai-eval platform 模式)专属 ----
# 平台连接项(BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT)与上方功能测试点 runner 共用,无需重复。
# 飞书导出(对话测评把结果导出到飞书表时用;不导出可留空):
FEISHU_APP_ID=
FEISHU_APP_SECRET=
# DeepSeek 生成引擎(若对话测评 query 生成用 deepseek;平台侧生成一般在后端,这里通常留空):
# DEEPSEEK_API_KEY=
# DEEPSEEK_BASE_URL=
```

- [ ] **Step 4: README 补 eval 路启动说明**

在 `tools/qalab-runner/README.md` 的"目录"小节后,追加一段:
```markdown
## 对话测评执行器(eval/)

`eval/` 是对话测评执行器(原 ai-eval-cli-yt,CommonJS)——跑 **eval_queue**(对话测评:驱动纳米Work对话、抓 WS 轨迹、回写平台)。与本目录的功能测试点 runner(runner.mjs)**共用同一套平台配置**(BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE),各自独立进程。

一次性准备:`cd eval && npm install`(装 playwright 等依赖)。
启动:`run-eval.cmd`(Windows)/ `run-eval.sh`(Mac),或 `cd eval && node bin/ai-eval.js platform`。
配置:eval 侧优先读上级 `tools/qalab-runner/.env`(与 runner 共享),兜底 eval/.env。
```

---

## Task 4: 本机验证 + 提交

- [ ] **Step 1: npm install(装 eval 依赖)**

Run: `cd /d/code/test-management-platform/tools/qalab-runner/eval && npm install 2>&1 | tail -5`
Expected: 依赖装好(playwright/commander 等),无 error。

- [ ] **Step 2: 验证读上级 .env + 语法**

Run: `cd /d/code/test-management-platform/tools/qalab-runner/eval && node -e "require('./src/work-frame'); require('./src/platform-client'); console.log('模块加载 OK')"`
Expected: `模块加载 OK`(CommonJS 模块在新位置可加载)。

- [ ] **Step 3: platform --once 联调(读上级 .env → 连客户端 → 拉任务)**

前置:上级 tools/qalab-runner/.env 存在(或临时导出 BASE_URL/RUNNER_TOKEN/RUNNER_ID 环境变量);平台有 pending 或验证"平台无待执行任务"即算通(证明拉取链路通)。
Run: `cd /d/code/test-management-platform/tools/qalab-runner/eval && BASE_URL=http://11.120.81.7:4173 RUNNER_TOKEN=<token> RUNNER_ID=lili-win node bin/ai-eval.js platform --once 2>&1 | tail -15`
Expected: 出现"已上报 N 个客户端设备"/"拉到 N 条待执行" 或 "平台无待执行任务"——证明配置读取 + 平台连通 + 设备上报链路在新位置正常。

- [ ] **Step 4: git 提交(精确 add,不含 node_modules/运行时)**

```bash
cd /d/code/test-management-platform
git add tools/qalab-runner/eval/src tools/qalab-runner/eval/bin tools/qalab-runner/eval/config
git add tools/qalab-runner/eval/package.json tools/qalab-runner/eval/package-lock.json
git add tools/qalab-runner/eval/.gitignore tools/qalab-runner/eval/README.md
git add tools/qalab-runner/eval/部署手册.md 2>/dev/null || true
git add "tools/qalab-runner/eval/运行测评.bat" "tools/qalab-runner/eval/桌面并发验证.bat" "tools/qalab-runner/eval/录制账号.bat" 2>/dev/null || true
git add tools/qalab-runner/eval/tools 2>/dev/null || true
git add tools/qalab-runner/run-eval.cmd tools/qalab-runner/run-eval.sh tools/qalab-runner/.env.example tools/qalab-runner/README.md
# 确认没混入 node_modules/accounts/output
git status --short | grep -E "eval/(node_modules|accounts|output)" && echo "⚠️ 有运行时被 add,需 reset" || echo "干净"
git commit -m "feat(runner): ai-eval-cli-yt 合并进 qalab-runner/eval(统一执行器+共享配置)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: 确认 run.cmd 等无关改动未动**

Run: `git status -sb | head`
Expected: tools/qalab-runner/run.cmd 仍是 M(未提交)、tools/__MACOSX/、tools/qalab-runner.zip 仍是 ??(未跟踪)——本任务未碰它们。

---

## Self-Review
- **Spec 覆盖**:§4 搬运→Task1;§5 配置读上级→Task2;§6 启动脚本→Task3;§8 验证→Task4。全覆盖。
- **搬运范围一致**:Task1 排除 node_modules/accounts/output 与 spec §2 非目标一致;.gitignore 覆盖。
- **不碰既存改动**:Task4 精确 add + Step5 确认 run.cmd/__MACOSX/zip 未动。
- **占位**:Task4-Step3 的 token 需实填(用现有 lili-win token);.bat 路径搬后 `cd /d %~dp0eval` 修正——run-eval.cmd 用的是上级目录 %~dp0eval,eval 内的 .bat(运行测评.bat 等)若含相对路径实施时确认(cd 到 eval 即可)。
