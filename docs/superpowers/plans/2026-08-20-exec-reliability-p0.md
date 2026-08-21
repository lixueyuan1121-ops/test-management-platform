# 用例执行可靠性 P0（L1 数据零坏值 + L5 复位核心）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (推荐，见文末说明) 或 superpowers:subagent-driven-development 逐任务实现。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 让坏选择器进不了库、进了也不致命，并在每条 gui/e2e 用例前 reload 硬复位——消除本次 case（`homepageTitle` 坏候选致进入段挂）与串行前置状态污染两类假失败的根基。

**Architecture:** 「有效候选」判定单点定义在 `selector_ranking.py`（py），schema 校验（L1①）与存量修复脚本（L1②）复用；runner 侧 `gui-core.mjs` 独立镜像同口径，逐 key 回落内置兜底（L1③）+ 每条 gui/e2e 前 `resetHome()` reload（L5）。

**Tech Stack:** FastAPI + Pydantic 2.10.4 + SQLAlchemy 2.0（后端）；Node.js + playwright-core（runner，`node:test`）。

**Spec:** `docs/superpowers/specs/2026-08-20-selector-reliability-root-fix-design.md`

## Global Constraints

- 无 pytest/eslint/ruff。后端测试：`cd backend && .venv/bin/python -m scripts.test_xxx`；runner 测试：`node tools/qalab-runner/<file>.test.mjs`。
- **「有效候选」定义**（verbatim）：含非空 `by ∈ {testid, role, label, text, placeholder, css}` 且含非空 `value`；`name` 仅 `role` 可选。空数组 `[]` 合法（待补壳）。
- 该口径三处镜像，改一处必同步：`backend/app/services/selector_ranking.py`、`frontend/src/utils/selector-ranking.js`、`tools/qalab-runner/gui-mcp/gui-core.mjs`（本 P0 只动 py 与 mjs 两处）。
- Pydantic **2.10.4** → 用 `field_validator`（v2）。校验失败抛 `ValueError` → FastAPI 归 422 → 统一信封 `{code,msg,data}`（`app/core/errors.py` 已处理）。
- 提交信息：中文 conventional + 结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。分支：`feat/exec-reliability-root-fix`（spec 已在此分支）。
- runner 侧改动（Task 3/4）需执行机 `git pull` + 重启 runner 才生效。

---

### Task 1: 有效候选判定单点 + schema 强校验（L1①）

**Files:**
- Modify: `backend/app/services/selector_ranking.py`（末尾追加 `VALID_BYS`/`is_valid_candidate`/`valid_candidates`）
- Modify: `backend/app/schemas/selector.py`（加 `field_validator`，全文替换）
- Test: `backend/scripts/test_selector_valid_candidate.py`（新建）

**Interfaces:**
- Produces: `is_valid_candidate(cand: dict) -> bool`、`valid_candidates(cands: list) -> list`、`VALID_BYS: set[str]`（Task 2、后续 P1 复用）。

- [ ] **Step 1: 写失败测试** — 新建 `backend/scripts/test_selector_valid_candidate.py`：

```python
"""「有效候选」口径 + schema 强校验自测（免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_selector_valid_candidate
"""
import pydantic

from app.services.selector_ranking import is_valid_candidate, valid_candidates, VALID_BYS
from app.schemas.selector import SelectorKeyIn, SelectorKeyPatch


def main():
    assert VALID_BYS == {"testid", "role", "label", "text", "placeholder", "css"}, VALID_BYS
    # is_valid_candidate
    assert is_valid_candidate({"by": "css", "value": "h1.x"}) is True
    assert is_valid_candidate({"by": "role", "value": "button", "name": "登录"}) is True
    assert is_valid_candidate({}) is False                              # 本次 case 的坏值 [{}]
    assert is_valid_candidate({"by": "css"}) is False                   # 缺 value
    assert is_valid_candidate({"value": "x"}) is False                  # 缺 by
    assert is_valid_candidate({"by": "bogus", "value": "x"}) is False   # 非法 by
    # valid_candidates 过滤保序 / 非 list → []
    assert valid_candidates([{"by": "css", "value": "a"}, {}, {"by": "text", "value": "b"}]) == \
        [{"by": "css", "value": "a"}, {"by": "text", "value": "b"}]
    assert valid_candidates("nope") == []
    # schema：空数组放行、合法放行
    SelectorKeyIn(project_id=1, key="k", candidates=[])
    SelectorKeyIn(project_id=1, key="k", candidates=[{"by": "css", "value": "h1"}])
    # schema：坏候选一律 422
    for bad in ([{}], [{"by": "css"}], [{"value": "x"}], [{"by": "bogus", "value": "x"}]):
        try:
            SelectorKeyIn(project_id=1, key="k", candidates=bad)
            assert False, f"应拒绝坏候选 {bad}"
        except pydantic.ValidationError:
            pass
    # patch：None（不改候选）放行，坏候选拒绝
    SelectorKeyPatch(candidates=None)
    try:
        SelectorKeyPatch(candidates=[{}])
        assert False, "patch 应拒绝坏候选"
    except pydantic.ValidationError:
        pass
    print("OK test_selector_valid_candidate")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m scripts.test_selector_valid_candidate`
Expected: FAIL — `ImportError: cannot import name 'is_valid_candidate'`。

- [ ] **Step 3a: 实现口径** — 在 `backend/app/services/selector_ranking.py` 末尾追加：

```python


VALID_BYS: set[str] = {"testid", "role", "label", "text", "placeholder", "css"}


def is_valid_candidate(cand: dict) -> bool:
    """候选结构是否有效（可被 runner 定位）：含合法 by + 非空 value。

    与 is_fragile 正交：is_fragile 谈"稳不稳"，本函数谈"结构完不完整"。
    坏例 {}、{"by":"css"}(缺 value)、{"value":"x"}(缺 by)、非法 by 均无效。
    镜像：frontend/src/utils/selector-ranking.js、gui-core.mjs::validCands（三处口径契约）。
    """
    return isinstance(cand, dict) and cand.get("by") in VALID_BYS and bool(cand.get("value"))


def valid_candidates(cands: list[dict]) -> list[dict]:
    """过滤出有效候选（保序）；非 list → []。"""
    return [c for c in cands if is_valid_candidate(c)] if isinstance(cands, list) else []
```

- [ ] **Step 3b: 实现 schema 校验** — 全文替换 `backend/app/schemas/selector.py`：

```python
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.selector_ranking import is_valid_candidate


def _validate_candidates(v: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for i, c in enumerate(v or []):
        if not is_valid_candidate(c):
            raise ValueError(
                f"候选[{i}]非法:须含 by(testid/role/label/text/placeholder/css)且 value 非空"
            )
    return v


class SelectorKeyIn(BaseModel):
    project_id: int
    sub_product: str = ""
    key: str = Field(min_length=1, max_length=64)
    frame: str = "auto"
    page: str = ""
    desc: str = ""
    candidates: list[dict[str, Any]] = []

    @field_validator("candidates")
    @classmethod
    def _v_candidates(cls, v):
        return _validate_candidates(v)


class SelectorKeyPatch(BaseModel):
    frame: str | None = None
    page: str | None = None
    desc: str | None = None
    candidates: list[dict[str, Any]] | None = None

    @field_validator("candidates")
    @classmethod
    def _v_candidates(cls, v):
        return v if v is None else _validate_candidates(v)


class SelectorScopeIn(BaseModel):
    project_id: int
    sub_product: str = ""
    vm_iframe: str = ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m scripts.test_selector_valid_candidate`
Expected: `OK test_selector_valid_candidate`。
再跑全量回归：`cd backend && for f in scripts/test_*.py; do .venv/bin/python -m scripts.$(basename $f .py) || break; done` — 应全绿（现 26 项）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/selector_ranking.py backend/app/schemas/selector.py backend/scripts/test_selector_valid_candidate.py
git commit -m "$(printf 'feat(selector): 有效候选口径 + schema 强校验(L1①)\n\n拒绝缺 by/value 的坏候选(如 [{}])入库,根治本次 homepageTitle 坏候选源。\nis_valid_candidate 单点定义于 selector_ranking.py,schema/存量脚本复用。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 2: 存量坏候选修复脚本（L1②）

**Files:**
- Create: `backend/scripts/fix_broken_selector_candidates.py`
- Test: `backend/scripts/test_fix_broken_selector_candidates.py`

**Interfaces:**
- Consumes: `valid_candidates`（Task 1）。
- Produces: `classify_key(db_cands, builtin_cands) -> str`（"ok"/"backfill"/"manual"）。

- [ ] **Step 1: 写失败测试** — 新建 `backend/scripts/test_fix_broken_selector_candidates.py`：

```python
"""存量修复脚本分类逻辑自测（纯函数，免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_fix_broken_selector_candidates
"""
from scripts.fix_broken_selector_candidates import classify_key


def main():
    good = [{"by": "css", "value": "h1"}]
    assert classify_key(good, []) == "ok"                    # DB 候选有效 → 跳过
    assert classify_key([{}], good) == "backfill"            # DB 坏 + 内置有 → 回填
    assert classify_key([], good) == "backfill"              # DB 空 + 内置有 → 回填
    assert classify_key([{}], []) == "manual"                # DB 坏 + 内置无 → 人工
    assert classify_key([{"by": "css"}], [{}]) == "manual"   # 两边都坏 → 人工
    print("OK test_fix_broken_selector_candidates")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/python -m scripts.test_fix_broken_selector_candidates`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fix_broken_selector_candidates'`。

- [ ] **Step 3: 实现脚本** — 新建 `backend/scripts/fix_broken_selector_candidates.py`：

```python
"""扫描全库 selector_key，找出候选结构坏（缺 by/value）的 key。
默认 dry-run 只打印报告；--apply 用内置 selectors.json 同名 key 回填可回填者。
运行: cd backend && .venv/bin/python scripts/fix_broken_selector_candidates.py [--apply]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal          # noqa: E402
from app.models import SelectorKey               # noqa: E402
from app.services.selector_ranking import valid_candidates  # noqa: E402

_BUILTIN = Path(__file__).resolve().parents[2] / "tools/qalab-runner/gui-mcp/selectors.json"


def classify_key(db_cands, builtin_cands) -> str:
    """该 key 的处置：ok(候选有效,跳过) / backfill(坏但内置可回填) / manual(坏且内置无)。"""
    if valid_candidates(db_cands):
        return "ok"
    if valid_candidates(builtin_cands):
        return "backfill"
    return "manual"


def _load_builtin() -> dict:
    try:
        return json.loads(_BUILTIN.read_text("utf-8")).get("registry", {})
    except (OSError, ValueError):
        return {}


def _cands(raw) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def main(apply: bool = False) -> int:
    builtin = _load_builtin()
    db = SessionLocal()
    ok = backfilled = manual = 0
    try:
        for r in db.query(SelectorKey).all():
            bc = (builtin.get(r.key) or {}).get("candidates", [])
            verdict = classify_key(_cands(r.candidates), bc)
            if verdict == "ok":
                ok += 1
            elif verdict == "backfill":
                good = valid_candidates(bc)
                print(f"[backfill] id={r.id} proj={r.project_id} key={r.key} <- 内置 {len(good)} 候选")
                if apply:
                    r.candidates = json.dumps(good, ensure_ascii=False)
                backfilled += 1
            else:
                print(f"[manual]   id={r.id} proj={r.project_id} key={r.key} 候选坏且内置无同名,需人工补")
                manual += 1
        if apply:
            db.commit()
        print(f"\n汇总: ok={ok} backfill={backfilled} manual={manual} apply={apply}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/python -m scripts.test_fix_broken_selector_candidates`
Expected: `OK test_fix_broken_selector_candidates`。
Dry-run 冒烟（连本地 SQLite，只读不改）：`cd backend && .venv/bin/python scripts/fix_broken_selector_candidates.py` — 应打印汇总不报错。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/fix_broken_selector_candidates.py backend/scripts/test_fix_broken_selector_candidates.py
git commit -m "$(printf 'feat(selector): 存量坏候选修复脚本(L1②,dry-run 默认)\n\n扫全库 selector_key,坏候选能对上内置同名 key 的回填、否则列人工清单。\n默认 dry-run,--apply 才写库。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 3: runner 逐 key 回落内置兜底（L1③）

**Files:**
- Modify: `tools/qalab-runner/gui-mcp/gui-core.mjs`（顶部加 `VALID_BYS`/`validCands`/`pickCandidates` export + `BUILTIN` 副本；`resolveKey`/`isKeyVisible` 组 plan 处改用 `pickCandidates`）
- Test: `tools/qalab-runner/gui-mcp/gui-core.test.mjs`（新建）

**Interfaces:**
- Produces: `validCands(cands) -> array`、`pickCandidates(dbCands, builtinCands) -> array`（Task 4 及后续复用）。

- [ ] **Step 1: 写失败测试** — 新建 `tools/qalab-runner/gui-mcp/gui-core.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { validCands, pickCandidates } from "./gui-core.mjs";

test("validCands: 只留 by+value 齐全且 by 合法的候选", () => {
  assert.deepEqual(
    validCands([{ by: "css", value: "h1" }, {}, { by: "css" }, { value: "x" }, { by: "bogus", value: "y" }]),
    [{ by: "css", value: "h1" }],
  );
  assert.deepEqual(validCands(null), []);
});

test("pickCandidates: DB 坏/空 → 回落内置同名 key；DB 有效 → 用 DB", () => {
  const builtin = [{ by: "css", value: "h1.home" }];
  assert.deepEqual(pickCandidates([{}], builtin), builtin, "DB 坏 → 回落内置");
  assert.deepEqual(pickCandidates([], builtin), builtin, "DB 空 → 回落内置");
  assert.deepEqual(
    pickCandidates([{ by: "css", value: "db" }], builtin),
    [{ by: "css", value: "db" }],
    "DB 有效 → 用 DB,不回落",
  );
  assert.deepEqual(pickCandidates([{}], []), [], "两边都坏 → 空");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node tools/qalab-runner/gui-mcp/gui-core.test.mjs`
Expected: FAIL — `SyntaxError: ... does not provide an export named 'validCands'`。
（前提：`cd tools/qalab-runner && npm install` 已装 playwright-core，与 step-executor.test.mjs 同环境。）

- [ ] **Step 3a: 加口径与内置副本** — 在 `gui-core.mjs` 的 `SELECTORS_PATH`（`:12`）定义之后、`DISCOVER_SCRIPT` 之前插入：

```javascript
// 「有效候选」口径(mjs 侧):含合法 by + 非空 value。镜像 app/services/selector_ranking.py::is_valid_candidate
// 与 frontend/src/utils/selector-ranking.js(三处口径契约,改一处必改另两处)。
const VALID_BYS = new Set(["testid", "role", "label", "text", "placeholder", "css"]);
export function validCands(cands) {
  return (Array.isArray(cands) ? cands : []).filter((c) => c && VALID_BYS.has(c.by) && c.value);
}
// 逐 key 回落:DB 候选过滤后有效则用之;全坏/空且内置有效 → 用内置同名 key 候选。
export function pickCandidates(dbCands, builtinCands) {
  const db = validCands(dbCands);
  return db.length ? db : validCands(builtinCands);
}
```

- [ ] **Step 3b: 加载内置副本** — 在 `createGuiCore` 里 `REGISTRY, VM_IFRAME` 赋值块（`:79-84`）之后加：

```javascript
  // 内置兜底副本(始终从仓库 selectors.json 读一份):DB 某 key 候选全坏/缺时逐 key 回落。
  let BUILTIN = {};
  try { BUILTIN = JSON.parse(readFileSync(opts.selectorsPath || SELECTORS_PATH, "utf-8")).registry || {}; }
  catch { BUILTIN = {}; }
```

- [ ] **Step 3c: resolveKey 组 plan 改用 pickCandidates** — 将 `resolveKey`（`:152`）里：

```javascript
    for (const s of scopesFor(entry.frame)) for (const cand of entry.candidates) plan.push({ s, cand });
```
改为：
```javascript
    const cands = pickCandidates(entry.candidates, (BUILTIN[key] || {}).candidates);
    for (const s of scopesFor(entry.frame)) for (const cand of cands) plan.push({ s, cand });
```

- [ ] **Step 3d: isKeyVisible 同步** — 将 `isKeyVisible`（`:180-181`）里：

```javascript
    for (const s of scopesFor(entry.frame)) {
      for (const cand of entry.candidates) {
```
改为：
```javascript
    const cands = pickCandidates(entry.candidates, (BUILTIN[key] || {}).candidates);
    for (const s of scopesFor(entry.frame)) {
      for (const cand of cands) {
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node tools/qalab-runner/gui-mcp/gui-core.test.mjs` → 全 PASS。
再跑 `node tools/qalab-runner/step-executor.test.mjs` 确认未回归。

- [ ] **Step 5: 提交**

```bash
git add tools/qalab-runner/gui-mcp/gui-core.mjs tools/qalab-runner/gui-mcp/gui-core.test.mjs
git commit -m "$(printf 'feat(runner): 逐 key 回落内置兜底(L1③)\n\nDB 某 key 候选全坏/缺时回落内置 selectors.json 同名 key,坏候选不再\n盖掉内置好候选。validCands/pickCandidates 镜像后端有效候选口径。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 4: 用例间 reload 复位核心（L5）

**Files:**
- Modify: `tools/qalab-runner/gui-mcp/gui-core.mjs`（对外方法加 `resetHome`）
- Create: `tools/qalab-runner/reset-home.mjs`（`resetHomeWithRetry`，与主循环解耦便于单测）
- Modify: `tools/qalab-runner/runner.mjs`（`RESET_BETWEEN_CASES` + import + `tick` gui/e2e 分支复位）
- Test: `tools/qalab-runner/reset-home.test.mjs`（新建）

**Interfaces:**
- Consumes: `gui.resetHome()`（本任务在 gui-core 新增）。
- Produces: `resetHomeWithRetry(gui, log, attempts=2) -> Promise<boolean>`。

- [ ] **Step 1: 写失败测试** — 新建 `tools/qalab-runner/reset-home.test.mjs`：

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { resetHomeWithRetry } from "./reset-home.mjs";

test("首次成功 → true,只调 1 次", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 1);
});

test("首次失败、二次成功 → true", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; if (n === 1) throw new Error("reload 超时"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), true);
  assert.equal(n, 2);
});

test("两次都失败 → false", async () => {
  let n = 0;
  const gui = { async resetHome() { n++; throw new Error("客户端未响应"); } };
  assert.equal(await resetHomeWithRetry(gui, () => {}), false);
  assert.equal(n, 2);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node tools/qalab-runner/reset-home.test.mjs`
Expected: FAIL — 找不到 `./reset-home.mjs`。

- [ ] **Step 3a: 实现重试封装** — 新建 `tools/qalab-runner/reset-home.mjs`：

```javascript
// 用例前硬复位的重试封装(与 runner 主循环解耦,便于单测)。
// gui.resetHome() 失败重试至多 attempts 次;全失败返回 false(调用方判 fail,不空跑脏态用例)。
export async function resetHomeWithRetry(gui, log = () => {}, attempts = 2) {
  for (let i = 0; i < attempts; i++) {
    try { await gui.resetHome(); return true; }
    catch (e) { log(`  复位失败(第${i + 1}次):${e.message || e}`); }
  }
  return false;
}
```

- [ ] **Step 3b: gui-core 加 resetHome** — 在 `gui-core.mjs` 对外方法 `goto`（`:277-281`）之后插入：

```javascript
    // 用例间硬复位:reload 顶层回初始加载态(清全部前端瞬态),等 vm iframe 就绪(不依赖业务选择器);
    // 可选再等首页锚点就绪(尽力,失败不阻断)。串行执行时每条 gui/e2e 前调,消除前置状态污染。
    async resetHome({ readyKey = "homepageTitle", readyTimeout = 8000 } = {}) {
      await ensureConnected();
      await page.reload({ waitUntil: "domcontentloaded", timeout: DEFAULT_TIMEOUT });
      await waitForContentFrame();
      if (readyKey && REGISTRY[readyKey]) {
        try { await resolveKey(readyKey, { timeout: readyTimeout, requireVisible: true }); }
        catch { /* 首页锚点尽力而为,不阻断复位 */ }
      }
      return { reset: true, url: page.url() };
    },
```

- [ ] **Step 3c: runner 接入** — `runner.mjs` 顶部 import 区加：

```javascript
import { resetHomeWithRetry } from "./reset-home.mjs";
```
env 常量区（`RUNNER_ID` 等附近）加：
```javascript
const RESET_BETWEEN_CASES = (process.env.RESET_BETWEEN_CASES ?? "1") !== "0";  // 用例间 reload 复位(默认开)
```
把 `tick()` 的 gui/e2e 分支（`:442-455`）整体替换为：
```javascript
      } else if (item.kind === "gui" || item.kind === "e2e") {
        await ensureNamiclaw();                          // GUI/E2E:先确保客户端带 CDP 在跑
        // 执行前从平台拉该项目的合并注册表(DB 单源)换入 gui-core;失败/无则不换,沿用内置文件(回落)。
        const reg = await fetchRegistry(item.payload?.project_id, "");
        if (reg && reg.registry) guiCore.setRegistry(reg.registry, reg.vmIframe);
        // 用例前硬复位(reload):清上一条遗留的选中/弹窗/输入残留等瞬态,保证从初始主界面开始。
        if (RESET_BETWEEN_CASES && !(await resetHomeWithRetry(guiCore, log))) {
          result = { verdict: "fail", reason: "用例前复位(reload)失败,跳过执行以免脏态污染", duration_ms: 1 };
        } else {
          const script = item.payload?.script;
          // 有结构化 script → StepExecutor 确定性执行(不经 LLM);无/需降级 → 回退 claude 兜底。
          if (Array.isArray(script) && script.length) {
            const r = await runScript(guiCore, script, (m) => log(m), judgeWithClaude);
            if (r.needClaude) { log(`  script 需降级:${r.reason}`); result = await runClaude(item.payload, item.kind); }
            else result = r;
          } else {
            result = await runClaude(item.payload, item.kind);
          }
        }
      } else if (item.kind === "api") {
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node tools/qalab-runner/reset-home.test.mjs` → 3 PASS。
`node tools/qalab-runner/step-executor.test.mjs` + `node tools/qalab-runner/gui-mcp/gui-core.test.mjs` 确认未回归。
（`resetHome` 的 reload→就绪序列 + tick 集成走真机验证，见文末执行说明。）

- [ ] **Step 5: 提交**

```bash
git add tools/qalab-runner/reset-home.mjs tools/qalab-runner/reset-home.test.mjs tools/qalab-runner/gui-mcp/gui-core.mjs tools/qalab-runner/runner.mjs
git commit -m "$(printf 'feat(runner): 用例间 reload 硬复位(L5 复位核心)\n\n每条 gui/e2e 前 page.reload 回初始主界面,清上一条遗留瞬态,消除串行\n前置状态污染。复位失败重试 1 次仍失败则判 fail、不空跑脏态用例。\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Self-Review

**Spec coverage（P0 = L1①②③ + L5 复位核心）：**
- L1① schema 强校验 → Task 1 ✓
- L1② 存量修复脚本 → Task 2 ✓
- L1③ runner 逐 key 回落内置兜底 → Task 3 ✓
- L5 复位核心（resetHome + tick 每条前调用）→ Task 4 ✓
- L5「blocked 归类」「掉登录检测」→ **不在 P0**（依赖 L2，属 P2，spec §6 已注明）。P0 复位失败暂记 `fail`（reason 标明复位失败），P2 改 `blocked`。

**Placeholder scan：** 无 TBD/TODO；每步含真实代码与确切运行命令；resetHome 序列的真机验证是有意的（Playwright page 操作单测价值低），非占位。

**Type consistency：** `is_valid_candidate`/`valid_candidates`/`VALID_BYS`（py，Task 1→2 复用）；`validCands`/`pickCandidates`（mjs，Task 3→4 复用）；`resetHome`（gui-core，Task 4 定义与调用）/`resetHomeWithRetry`（Task 4 定义与 tick 调用）命名一致。

**后续期（不在本 plan，待 P0 落地后各出 plan）：** P1=L4 候选有效性校验升级（`usable_key_set` + `_registered_keys` 切口径）；P2=L2 失败分类 blocked（+ L5 归类/掉登录）；P3=L3 核心 key 保障 + 巡检。
