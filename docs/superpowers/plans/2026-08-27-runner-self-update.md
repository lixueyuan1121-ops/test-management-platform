# Runner 自升级功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** runner 启动时自动向后端检查新版本，有更新则下载 zip 覆盖本地 .mjs 文件后重启，消灭人肉同步文件。

**Architecture:** 后端从仓库目录实时打包 `tools/qalab-runner/` 为 zip 提供下载（版本号 = 打包内容的哈希）；runner 端 `--update` 子命令对比版本、下载解压覆盖，退出码 75 表示"已更新需重启"；run.sh/run.cmd 启动前先跑 `--update`，检测 75 则循环重启。

**Tech Stack:** 后端 FastAPI + Python 标准库 zipfile/hashlib；runner 纯 Node 18+（fetch + 内置 zlib 不够解 zip，用无依赖的手写 zip 解析或 spawn 系统 unzip/tar —— 采用系统命令：Mac/Linux `unzip`，Windows PowerShell `Expand-Archive`）。

**Spec:** 会话内确认的设计（无独立 spec 文件）：
- 后端两个接口，runner token 鉴权（`require_runner_ctx`）
- `GET /api/runner/version` → `{version: "<sha8>"}`（打包清单内容哈希，git pull 后自动变化）
- `GET /api/runner/bundle` → zip 流（实时打包，排除 .env/node_modules/evidence/测试文件等）
- runner `--update` 子命令：比版本 → 下载 → 解压覆盖 → exit 75；版本一致 exit 0
- run.sh/run.cmd：先 `node runner.mjs --update`，exit 75 则再跑一次 --update 后的新代码也无需特殊处理（更新已完成），直接继续启动常驻进程
- 只需分发 run.sh / run.cmd 给成员，.mjs 全走升级通道

## Global Constraints

- runner 端零第三方依赖（纯 Node 18+ 内置模块 + 系统命令）
- 后端不新增 pip 依赖（zipfile/hashlib 标准库）
- 响应信封 `{code, msg, data}`（zip 流除外——二进制直接 StreamingResponse）
- 排除清单必须包含：`.env`、`node_modules/`、`evidence/`、`eval/`、`cases/`、`*.test.mjs`、`selectors.json.bak`、`platform/`（已并入后端的历史代码）、`__pycache__`、`.DS_Store`
- **绝不覆盖** runner 本机的 `.env`（用户配置）与 `node_modules`
- 版本号 = 所有打包文件的 (路径, mtime, size) 聚合 sha256 前 8 位——git pull 后文件变化 → 版本自动变化

---

### Task 1: 后端 runner_update 路由（version + bundle 两接口）

**Files:**
- Create: `backend/app/api/runner_update.py`
- Modify: `backend/app/api/router.py`（注册路由）

**Interfaces:**
- Produces: `GET /api/runner/version` → `ok({"version": str})`；`GET /api/runner/bundle` → `StreamingResponse(zip)`，header `X-Bundle-Version`
- Consumes: `app.core.deps.require_runner_ctx`（已存在的 runner 鉴权）

- [ ] **Step 1: 写路由实现**

```python
"""runner 自升级分发：版本查询 + zip 包下载。

- GET /api/runner/version  当前可分发的 runner 版本号；runner token。
- GET /api/runner/bundle   实时打包 tools/qalab-runner/ 为 zip 流；runner token。

版本号 = 打包清单内所有文件 (相对路径, mtime_ns, size) 的 sha256 前 8 位:
不需要人工维护版本号,服务器 git pull 后文件一变,版本随之变化。
打包排除运行时本地产物与机器私有配置(.env/node_modules/evidence 等),
runner 端解压覆盖时同样绝不触碰本机 .env 与 node_modules。
"""
import hashlib
import io
import os
import zipfile

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.core.deps import require_runner_ctx
from app.schemas.common import ok

router = APIRouter(prefix="/api/runner", tags=["runner-update"])

# 仓库根 = backend/app/api/runner_update.py 往上四级
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
_RUNNER_DIR = os.path.join(_REPO_ROOT, "tools", "qalab-runner")

# 排除规则：目录名（任意层级命中即剪枝）与文件名模式
_EXCLUDE_DIRS = {"node_modules", "evidence", "eval", "cases", "platform", "__pycache__", ".git"}
_EXCLUDE_FILES = {".env", ".DS_Store", "selectors.json.bak"}
_EXCLUDE_SUFFIXES = (".test.mjs", ".zip", ".log")


def _iter_bundle_files():
    """走一遍 runner 目录，yield (绝对路径, zip 内相对路径)，按相对路径排序保证哈希稳定。"""
    out = []
    for root, dirs, files in os.walk(_RUNNER_DIR):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            if fn in _EXCLUDE_FILES or fn.endswith(_EXCLUDE_SUFFIXES):
                continue
            ap = os.path.join(root, fn)
            rp = os.path.relpath(ap, _RUNNER_DIR)
            out.append((ap, rp))
    out.sort(key=lambda t: t[1])
    return out


def _bundle_version() -> str:
    """打包清单指纹：所有文件 (相对路径, mtime_ns, size) 聚合 sha256 前 8 位。"""
    h = hashlib.sha256()
    for ap, rp in _iter_bundle_files():
        st = os.stat(ap)
        h.update(f"{rp}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()[:8]


@router.get("/version")
def runner_version(_=Depends(require_runner_ctx)):
    return ok({"version": _bundle_version()})


@router.get("/bundle")
def runner_bundle(_=Depends(require_runner_ctx)):
    """实时打包 zip 到内存并流式返回。包体量级 ~几百 KB，内存打包足够。"""
    ver = _bundle_version()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ap, rp in _iter_bundle_files():
            zf.write(ap, rp)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=qalab-runner.zip",
            "X-Bundle-Version": ver,
        },
    )
```

- [ ] **Step 2: 注册路由**

`backend/app/api/router.py`：import 行加 `runner_update`，末尾加 `api_router.include_router(runner_update.router)`。

- [ ] **Step 3: 手动验证两接口**

```bash
cd backend && python -c "
from app.api.runner_update import _bundle_version, _iter_bundle_files
files = _iter_bundle_files()
print('files:', len(files))
for _, rp in files[:10]: print(' ', rp)
assert all('.env' not in rp and 'node_modules' not in rp for _, rp in files)
print('version:', _bundle_version())
"
```
Expected: 文件列表不含排除项；version 输出 8 位 hex。

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/runner_update.py backend/app/api/router.py
git commit -m "feat(backend): runner 自升级分发接口 /api/runner/version + /bundle"
```

---

### Task 2: runner 端 self-update.mjs（检查、下载、解压覆盖）

**Files:**
- Create: `tools/qalab-runner/self-update.mjs`
- Test: `tools/qalab-runner/self-update.test.mjs`

**Interfaces:**
- Produces: `selfUpdate({ baseUrl, token, dir, log }) -> Promise<"updated"|"current"|"failed">`；纯函数 `shouldUpdate(local, remote) -> bool`
- Consumes: 后端 `GET /api/runner/version`（信封）与 `GET /api/runner/bundle`（zip 流 + X-Bundle-Version header）

- [ ] **Step 1: 写失败测试（版本比较 + 本地版本读写）**

```js
// tools/qalab-runner/self-update.test.mjs —— node --test
import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldUpdate, readLocalVersion, writeLocalVersion } from "./self-update.mjs";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("shouldUpdate: 本地无版本(首次) → 需要更新", () => {
  assert.equal(shouldUpdate("", "abc12345"), true);
  assert.equal(shouldUpdate(null, "abc12345"), true);
});

test("shouldUpdate: 版本一致 → 不更新", () => {
  assert.equal(shouldUpdate("abc12345", "abc12345"), false);
});

test("shouldUpdate: 版本不同 → 更新", () => {
  assert.equal(shouldUpdate("abc12345", "def67890"), true);
});

test("shouldUpdate: 远端版本空/异常 → 不更新(保守)", () => {
  assert.equal(shouldUpdate("abc12345", ""), false);
  assert.equal(shouldUpdate("abc12345", null), false);
});

test("readLocalVersion/writeLocalVersion round-trip", () => {
  const dir = mkdtempSync(join(tmpdir(), "selfup-"));
  try {
    assert.equal(readLocalVersion(dir), "");          // 无文件 → 空串
    writeLocalVersion(dir, "abc12345");
    assert.equal(readLocalVersion(dir), "abc12345");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd tools/qalab-runner && node --test self-update.test.mjs`
Expected: FAIL（self-update.mjs 不存在）

- [ ] **Step 3: 实现 self-update.mjs**

```js
// self-update —— runner 自升级：对比平台版本，下载 zip 解压覆盖本目录，exit 75 由外层脚本重启。
//
// 设计：
// - 版本对比：平台 GET /api/runner/version（文件清单指纹）vs 本地 .runner-version 文件。
//   不在代码里硬编码版本常量——覆盖解压后新代码即新版本，指纹落盘即可。
// - 解压：零依赖方针下不手写 zip 解析——用系统命令(Mac/Linux unzip -o / Windows Expand-Archive)。
// - 安全：只覆盖包内文件；本机 .env / node_modules / evidence 不在包内，天然不被触碰。
// - 失败安全：任何一步失败 → 返回 "failed"，不中断后续正常启动（外层脚本忽略非 75 退出码）。
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, createWriteStream, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const VERSION_FILE = ".runner-version";

// 纯函数：要不要更新。远端拿不到版本(空/null)时保守不动。
export function shouldUpdate(local, remote) {
  if (!remote) return false;
  return (local || "") !== remote;
}

export function readLocalVersion(dir) {
  try { return readFileSync(join(dir, VERSION_FILE), "utf-8").trim(); }
  catch { return ""; }
}

export function writeLocalVersion(dir, ver) {
  writeFileSync(join(dir, VERSION_FILE), ver + "\n");
}

function execP(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { windowsHide: true }, (err, stdout, stderr) =>
      err ? reject(new Error(stderr || err.message)) : resolve(stdout));
  });
}

// 解压 zip 到 dir(覆盖同名文件)。Mac/Linux 用 unzip -o;Windows 用 PowerShell Expand-Archive -Force。
async function extract(zipPath, dir) {
  if (process.platform === "win32") {
    await execP("powershell.exe", ["-NoProfile", "-Command",
      `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${dir}' -Force`]);
  } else {
    await execP("unzip", ["-o", zipPath, "-d", dir]);
  }
}

// 主流程。返回 "updated" | "current" | "failed"(失败不抛,由调用方决定是否继续启动)。
export async function selfUpdate({ baseUrl, token, dir, log = console.log }) {
  const H = { Authorization: `Bearer ${token}` };
  let remote;
  try {
    const res = await fetch(`${baseUrl}/api/runner/version`, { headers: H, signal: AbortSignal.timeout(10000) });
    const env = await res.json();
    if (!res.ok || env.code !== 0) throw new Error(`HTTP ${res.status} code=${env.code}`);
    remote = env.data?.version || "";
  } catch (e) {
    log(`[update] 版本检查失败(跳过更新): ${e.message}`);
    return "failed";
  }
  const local = readLocalVersion(dir);
  if (!shouldUpdate(local, remote)) {
    log(`[update] 已是最新版本 ${remote}`);
    return "current";
  }
  log(`[update] 发现新版本 ${remote}(本地 ${local || "无"}),下载中…`);
  const tmp = mkdtempSync(join(tmpdir(), "qalab-update-"));
  const zipPath = join(tmp, "bundle.zip");
  try {
    const res = await fetch(`${baseUrl}/api/runner/bundle`, { headers: H, signal: AbortSignal.timeout(60000) });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    await pipeline(Readable.fromWeb(res.body), createWriteStream(zipPath));
    await extract(zipPath, dir);
    writeLocalVersion(dir, remote);
    log(`[update] 更新完成 → ${remote},即将重启`);
    return "updated";
  } catch (e) {
    log(`[update] 更新失败(继续用当前版本): ${e.message}`);
    return "failed";
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd tools/qalab-runner && node --test self-update.test.mjs`
Expected: PASS（5 个测试全绿）

- [ ] **Step 5: Commit**

```bash
git add tools/qalab-runner/self-update.mjs tools/qalab-runner/self-update.test.mjs
git commit -m "feat(runner): self-update 模块（版本对比/下载/解压覆盖）"
```

---

### Task 3: runner.mjs 接入 --update 子命令

**Files:**
- Modify: `tools/qalab-runner/runner.mjs`（入口分支 + import）

**Interfaces:**
- Consumes: Task 2 的 `selfUpdate({baseUrl, token, dir, log})`
- Produces: `node runner.mjs --update` → 更新成功 exit 75；已最新/失败 exit 0（失败不阻塞启动）

- [ ] **Step 1: 加 import 与入口分支**

import 区加：
```js
import { selfUpdate } from "./self-update.mjs";
```

入口处（`if (_argv[0] === "upload")` 分支之前）加 `--update` 分支——注意 `--update` 带 `--` 前缀会被现有 `_argv` 过滤掉，所以判断用 `process.argv`：

```js
// 入口:--update 自升级(run.sh/run.cmd 启动前调;updated → exit 75 通知外层重启);
// upload 子命令直传本地 session;否则常驻三队列轮询。
const _argv = process.argv.slice(2).filter((a) => !a.startsWith("--"));
if (process.argv.includes("--update")) {
  const dir = dirname(fileURLToPath(import.meta.url));
  selfUpdate({ baseUrl: BASE_URL, token: RUNNER_TOKEN, dir, log })
    .then((r) => process.exit(r === "updated" ? 75 : 0))
    .catch((e) => { log("[update] 异常(跳过):", e.message); process.exit(0); });
} else if (_argv[0] === "upload") {
```

- [ ] **Step 2: 冒烟验证（不依赖真实后端）**

```bash
cd tools/qalab-runner && BASE_URL=http://127.0.0.1:1 RUNNER_TOKEN=x node runner.mjs --update; echo "exit=$?"
```
Expected: 打印 `[update] 版本检查失败(跳过更新): ...`，`exit=0`（失败安全，不阻塞）。

- [ ] **Step 3: Commit**

```bash
git add tools/qalab-runner/runner.mjs
git commit -m "feat(runner): 入口接入 --update 自升级子命令"
```

---

### Task 4: run.sh / run.cmd 启动前自动检查更新

**Files:**
- Modify: `tools/qalab-runner/run.sh`
- Modify: `tools/qalab-runner/run.cmd`

**Interfaces:**
- Consumes: Task 3 的 `node runner.mjs --update`（exit 75 = 已更新）
- Produces: 成员只需持有这两个脚本，启动即自动升级

- [ ] **Step 1: 改写 run.sh**

```bash
#!/usr/bin/env bash
# qalab 本地执行 runner 启动脚本(Mac/Linux)。对应 Windows 的 run.cmd。
# 首次使用前:cp .env.example .env 并填好;在 gui-mcp 目录跑 npm install。
# 启动时自动向平台检查 runner 新版本(node runner.mjs --update):
#   exit 75 = 已下载覆盖新版本(再检查一次直至最新,防连续两次发版);其余退出码 = 已最新/检查失败,直接启动。
set -euo pipefail
cd "$(dirname "$0")"

echo "[run] checking runner update"
for i in 1 2 3; do
  code=0
  node runner.mjs --update || code=$?
  [ "$code" -ne 75 ] && break
  echo "[run] runner updated, re-checking"
done

echo "[run] starting qalab runner"
# runner.mjs 会自动读取同目录 .env
exec node runner.mjs "$@"
```

- [ ] **Step 2: 改写 run.cmd**

```bat
@echo off
REM qalab 本地执行 runner 启动脚本。双击或命令行运行。
REM 首次使用前:先在 gui-mcp 目录跑一次 npm install(见 README)。
REM 启动时自动检查更新:runner.mjs --update 退出码 75 = 已更新,重新检查直至最新。

setlocal
set "BASE_URL=https://qalab.claw.qihoo.net"
set "RUNNER_ID=win-01"
REM TODO: 填入平台发给本 runner 的长期 token
set "RUNNER_TOKEN=REPLACE_WITH_RUNNER_TOKEN"
set "NAMICLAW_EXE=D:\Program Files\namiclaw\Application\namiclaw.exe"
set "CDP_PORT=9222"
set "POLL_MS=5000"

cd /d "%~dp0"

echo [run] checking runner update
set /a _tries=0
:update_loop
node runner.mjs --update
if %errorlevel%==75 (
  set /a _tries+=1
  if %_tries% lss 3 (
    echo [run] runner updated, re-checking
    goto update_loop
  )
)

echo [run] starting qalab runner (base=%BASE_URL% runner=%RUNNER_ID%)
node runner.mjs %*
```

- [ ] **Step 3: 冒烟验证 run.sh（无后端时直接启动不受阻）**

```bash
cd tools/qalab-runner && BASE_URL=http://127.0.0.1:1 RUNNER_TOKEN=x timeout 8 ./run.sh --dry || true
```
Expected: 先打 `[update] 版本检查失败(跳过更新)`，随后正常进入 `runner 启动 ... dry=true`。

- [ ] **Step 4: Commit**

```bash
git add tools/qalab-runner/run.sh tools/qalab-runner/run.cmd
git commit -m "feat(runner): run.sh/run.cmd 启动前自动检查并应用更新"
```

---

### Task 5: 端到端验证 + 文档更新

**Files:**
- Modify: `tools/qalab-runner/DEPLOY.md`（部署说明加自升级一节）

**Interfaces:**
- Consumes: Task 1-4 全部产物

- [ ] **Step 1: 本地起后端，全链路验证**

```bash
cd backend && RUNNER_TOKEN=test-update-token nohup uvicorn app.main:app --port 8017 > /tmp/be-selfup.log 2>&1 &
sleep 3
# 版本接口
curl -s -H "Authorization: Bearer test-update-token" http://127.0.0.1:8017/api/runner/version
# bundle 接口(zip 魔数 PK)
curl -s -H "Authorization: Bearer test-update-token" http://127.0.0.1:8017/api/runner/bundle -o /tmp/bundle.zip && unzip -l /tmp/bundle.zip | head -20
# runner 端全流程:拷一份 runner 到临时目录模拟旧版本设备,跑 --update
rm -rf /tmp/runner-copy && cp -r tools/qalab-runner /tmp/runner-copy && rm -rf /tmp/runner-copy/gui-mcp/node_modules /tmp/runner-copy/.env
cd /tmp/runner-copy && BASE_URL=http://127.0.0.1:8017 RUNNER_TOKEN=test-update-token node runner.mjs --update; echo "exit=$?"
# 二次运行应 current
BASE_URL=http://127.0.0.1:8017 RUNNER_TOKEN=test-update-token node runner.mjs --update; echo "exit=$?"
```
Expected: version 返回 8 位 hex；zip 清单不含 .env/node_modules/*.test.mjs；首跑 exit=75 且 .runner-version 落盘；二跑 `已是最新版本` exit=0。

- [ ] **Step 2: 杀掉验证后端**

```bash
pkill -f "uvicorn app.main:app --port 8017"
```

- [ ] **Step 3: DEPLOY.md 加自升级说明**

在「五、启动」一节后追加：

```markdown
## 五点五、自升级(无需再人肉拷文件)

run.sh / run.cmd 启动时会自动向平台检查 runner 新版本:
- 有新版本 → 自动下载覆盖本目录代码文件(`.env`、`gui-mcp/node_modules`、`evidence/` 不受影响)后用新版本启动;
- 已最新 / 平台连不上 → 直接正常启动(更新失败绝不阻塞执行)。

因此**手动同步只需要这一次**:拿到 run.sh(Mac)/run.cmd(Windows)与初始目录后,后续升级全自动。
若 gui-mcp/package.json 依赖有变化(罕见),仍需在 gui-mcp 下重新 npm install——启动日志会因 import 失败明确报错。
```

- [ ] **Step 4: Commit + push**

```bash
git add tools/qalab-runner/DEPLOY.md
git commit -m "docs(runner): DEPLOY.md 补自升级说明"
git push origin main
```

---

## Self-Review 结论

- **Spec coverage**：两接口(T1)、自升级模块(T2)、入口(T3)、启动脚本(T4)、验证+文档(T5)——会话确认的设计全覆盖。
- **版本号方案说明**：舍弃了早先讨论的"runner.mjs 里 VERSION 常量 + sed 替换"，改为 `.runner-version` 指纹落盘文件——覆盖式更新下常量无法自动变化，指纹文件才是单一事实源；首次无文件视为"需更新"，会多下载一次并落盘，行为正确。
- **Placeholder scan**：无 TBD/TODO；所有代码块完整可执行。
- **Type consistency**：`selfUpdate` 返回 `"updated"|"current"|"failed"` 三态，T3 只对 `"updated"` exit 75，其余 exit 0——与 T4 脚本对 75 的判断一致。exit 75 取自 BSD `EX_TEMPFAIL`，避开常用码。
- **安全确认**：bundle 排除 `.env`；解压覆盖只写 zip 内文件，本机 `.env`/`node_modules` 天然不动。zip 来源是自家后端 + token 鉴权，路径穿越风险可控（zipfile 打包侧路径全为相对路径）。
