# 选择器质量治理（P2）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 gui/e2e 导出脚本的 Playwright strict-mode 报错，并治理探测「文案漂移」对选择器注册表的数据污染，从而提升 gui/e2e 用例的执行成功率。

**Architecture:** 引入一份「稳定/脆弱」候选口径（脆弱 = `text`/`role`，因 `getByText` 子串匹配 + role 靠 name 均 copy 依赖）。后端 exporter 与前端合并都据此把脆弱候选降到链尾；exporter 再给 `.or()` 链整体加 `.first()`（镜像 runner `resolveKey` 的 per-candidate `.first()`）。前端合并改「就地替换 + 稳定优先 + 去重 + 上限」，并把「纯文案漂移」归类为「已存在」默认不写库。

**Tech Stack:** 后端 Python（纯函数，hermetic 脚本自测，`cd backend && .venv/bin/python -m scripts.test_*`）；前端 Vue3 + Vite（无 JS 单测框架 → `npm run build` + 手动验证，逻辑抽 `utils` 纯函数保证可审查）。

**Spec:** `docs/superpowers/specs/2026-08-20-selector-quality-and-missing-key-bridge-design.md`（本计划仅实现其中组件 1/2/3；组件 4/5 = P1，另出 Plan 2）

## Global Constraints

- **不改表**：`selector_key.candidates` JSON 形状不变（元素仍 `{by, value, name?}`）；排序按 `by` 派生，不持久化 score。故 `models/` 与 `sql/schema.sql` 不动。
- **两处镜像 + 文档标注**：后端 `selector_ranking.py`（py）↔ 前端 `utils/selector-ranking.js`（js），二者语义必须一致，均以 `tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates` 分梯为参照（脆弱 = 其最低档 text/role）。改一处必改另一处 + 注释。
- **脆弱口径的唯一定义**：`by ∈ {"text", "role"}` 为脆弱；其余（testid/css/label/placeholder）为稳定。全计划统一此定义。
- **无 pytest/eslint/ruff**（CLAUDE.md）：后端自测用 `backend/scripts/test_*.py` hermetic 脚本；前端用 `npm run build` + 手动。
- **建立在用户在途改动之上**：`playwright_exporter.py` 已有未提交的 `_js_comment()`（防注释注入），本计划**不得覆盖/回退**它；`scripts/test_playwright_export.py` 已有未提交的注入自测断言，只**增改**相关断言、保留其余。
- **执行前置**：按 `superpowers:using-git-worktrees` 建隔离 worktree/分支后再实现（当前在 `main`，勿直接在 `main` 提交）。
- 所有面向用户的文案用 zh-CN。

## File Structure

- `backend/app/services/selector_ranking.py`（**新建**）：`is_fragile` / `order_candidates` 纯函数，py 侧稳定/脆弱口径。单一职责，无 DB、无网络。
- `backend/scripts/test_selector_ranking.py`（**新建**）：上者的 hermetic 自测。
- `backend/app/services/playwright_exporter.py`（**改**）：`_locator_expr` 用 `order_candidates` 排序 + 末尾 `.first()`。
- `backend/scripts/test_playwright_export.py`（**改**）：更新期望字符串（`.first()` + 脆弱降尾），新增排序/`.first()` 断言。
- `frontend/src/utils/selector-ranking.js`（**新建**）：`isFragile` / `orderCandidates`，js 侧口径（Task 1 的镜像）。
- `frontend/src/views/SelectorAdmin.vue`（**改**）：`matchStatus` 分类细化（文案漂移→已存在）、`submitAddAsKey` + `addMergedPreview` 合并策略、相关 UI 文案。

---

### Task 1: 后端稳定/脆弱口径工具（`selector_ranking.py`）

**Files:**
- Create: `backend/app/services/selector_ranking.py`
- Test: `backend/scripts/test_selector_ranking.py`

**Interfaces:**
- Produces:
  - `FRAGILE_BYS: set[str]`（= `{"text", "role"}`）
  - `is_fragile(cand: dict) -> bool` —— `cand.get("by")` 是否脆弱
  - `order_candidates(cands: list[dict]) -> list[dict]` —— 稳定候选在前（保持相对顺序）、脆弱候选在后（保持相对顺序）；稳定排序、不改元素、返回新列表

- [ ] **Step 1: 写失败测试**

Create `backend/scripts/test_selector_ranking.py`:

```python
"""selector_ranking 自测（纯函数，免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_selector_ranking

口径：脆弱 by = text/role（getByText 子串匹配 + role 靠 name，均 copy 依赖）；
order_candidates 把脆弱降到链尾、其余保持相对顺序（稳定排序）。
镜像前端 frontend/src/utils/selector-ranking.js，参照 gui-core.mjs::genCandidates 分梯。
"""
from app.services.selector_ranking import is_fragile, order_candidates, FRAGILE_BYS


def main():
    # is_fragile：text/role 脆弱，其余稳定
    assert FRAGILE_BYS == {"text", "role"}, FRAGILE_BYS
    assert is_fragile({"by": "text", "value": "登录"}) is True
    assert is_fragile({"by": "role", "value": "button", "name": "登录"}) is True
    for by in ("testid", "css", "label", "placeholder"):
        assert is_fragile({"by": by, "value": "x"}) is False, by
    # 缺 by（默认按 css 处理）→ 稳定
    assert is_fragile({"value": "x"}) is False

    # order_candidates：脆弱降尾，稳定保持相对顺序
    cands = [
        {"by": "text", "value": "对话"},
        {"by": "testid", "value": "chat-title"},
        {"by": "label", "value": "标题"},
    ]
    ordered = order_candidates(cands)
    assert [c["by"] for c in ordered] == ["testid", "label", "text"], ordered
    # 原列表不被修改
    assert [c["by"] for c in cands] == ["text", "testid", "label"], cands

    # 多个脆弱之间也保持相对顺序
    cands2 = [
        {"by": "role", "value": "button", "name": "发送"},
        {"by": "css", "value": "#send"},
        {"by": "text", "value": "发送"},
    ]
    assert [c["by"] for c in order_candidates(cands2)] == ["css", "role", "text"], order_candidates(cands2)

    # 全稳定 → 原样；空 → 空
    stable = [{"by": "testid", "value": "a"}, {"by": "css", "value": "#b"}]
    assert order_candidates(stable) == stable
    assert order_candidates([]) == []

    print("OK test_selector_ranking")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/python -m scripts.test_selector_ranking`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.selector_ranking'`

- [ ] **Step 3: 写最小实现**

Create `backend/app/services/selector_ranking.py`:

```python
"""选择器候选「稳定/脆弱」口径（后端侧）。

脆弱 by = text / role：`getByText` 默认子串匹配（'登录' 命中 '立即登录'），role 只能靠
name 匹配（copy 依赖）——二者随文案变化易失效、且多条同时命中会触发 Playwright strict 违例。
其余（testid/css/label/placeholder）视为稳定。

order_candidates 把脆弱候选降到链尾、稳定候选保持相对顺序（存库顺序已反映探测期分梯，
运行期不做完整重排，只降级脆弱）。

镜像：frontend/src/utils/selector-ranking.js（改一处必改另一处）；
参照 tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates 的分梯（text/role 为其最低档）。
"""
from __future__ import annotations

FRAGILE_BYS: set[str] = {"text", "role"}


def is_fragile(cand: dict) -> bool:
    """候选是否脆弱（by=text/role）。缺 by 按 css（稳定）处理。"""
    return (cand.get("by") or "css") in FRAGILE_BYS


def order_candidates(cands: list[dict]) -> list[dict]:
    """稳定候选在前、脆弱候选在后，各自保持相对顺序（稳定排序，返回新列表）。"""
    stable = [c for c in cands if not is_fragile(c)]
    fragile = [c for c in cands if is_fragile(c)]
    return stable + fragile
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/python -m scripts.test_selector_ranking`
Expected: PASS —— 打印 `OK test_selector_ranking`

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/selector_ranking.py backend/scripts/test_selector_ranking.py
git commit -m "feat(selectors): 后端候选稳定/脆弱口径 order_candidates(脆弱 text/role 降尾)"
```

---

### Task 2: exporter 用排序 + `.first()` 收敛

**Files:**
- Modify: `backend/app/services/playwright_exporter.py`（`_locator_expr` 约 64–77 行 + 顶部 import）
- Test: `backend/scripts/test_playwright_export.py`（更新期望字符串 + 新增断言）

**Interfaces:**
- Consumes: `order_candidates`（Task 1）
- Produces: `_locator_expr(entry, key, registry, vm_iframe) -> str` —— 现返回「稳定优先排序的 `.or()` 链 + 末尾 `.first()`」

- [ ] **Step 1: 更新/新增失败断言**

在 `backend/scripts/test_playwright_export.py` 中，把以下四条 `_locator_expr` 断言改为带 `.first()`、脆弱降尾的期望：

第 59 行改为：
```python
    assert e == "page.locator('input[type=submit]').first()", e
```
第 63 行改为（css、placeholder 均稳定，顺序不变）：
```python
    assert e == "page.locator('input[name=userName]').or(page.getByPlaceholder('手机号/用户名/邮箱')).first()", e
```
第 67 行改为：
```python
    assert e == "vm.getByRole('button', { name: '发送' }).first()", e
```
第 71 行改为（`text('对话')` 脆弱 → 降到 `label('标题')` 之后）：
```python
    assert e == "vm.getByTestId('chat-title').or(vm.getByLabel('标题')).or(vm.getByText('对话')).first()", e
```
第 97 行改为（loginSubmit 单候选也带 `.first()`）：
```python
    assert "page.locator('input[type=submit]').first().click()" in out
```

并在第 71 行断言之后**新增**一段，锁定「脆弱降尾 + 每条链以 `.first()` 收尾」：
```python
    # 脆弱候选(text/role)必须被降到链尾：即便注册表里 text 排在 testid 之前
    reordered = _locator_expr(
        {"frame": "vm", "candidates": [
            {"by": "text", "value": "对话"},
            {"by": "testid", "value": "chat-title"},
        ]}, "x", REGISTRY, VM_IFRAME)
    assert reordered == "vm.getByTestId('chat-title').or(vm.getByText('对话')).first()", reordered
    # 每个 _locator_expr 结果都以 .first() 收尾（消除 .or() 多命中 strict 违例）
    for k in REGISTRY:
        assert _locator_expr(REGISTRY[k], k, REGISTRY, VM_IFRAME).endswith(".first()"), k
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/python -m scripts.test_playwright_export`
Expected: FAIL —— 首个失败在第 59 行附近（实际值无 `.first()`，与期望不符）

- [ ] **Step 3: 改实现**

在 `playwright_exporter.py` 顶部 import 区加入：
```python
from app.services.selector_ranking import order_candidates
```

把 `_locator_expr` 整体替换为：
```python
def _locator_expr(entry: dict, key: str, registry: dict, vm_iframe: str) -> str:
    """一个已登记 key 的 entry → 多候选 .or() 链（稳定优先、脆弱降尾）+ 末尾 .first()。

    镜像 runner resolveKey：runner 逐候选 byToLocator(scope,cand).first()；导出侧把整条
    .or() 链收敛为 .first()，避免多个候选（尤其 getByText 子串匹配）同时命中触发
    Playwright strict 违例。候选排序见 selector_ranking.order_candidates（脆弱 text/role 降尾）。
    """
    scope = _scope_var(entry.get("frame"))
    cands = order_candidates(entry.get("candidates") or [])
    if not cands:
        # 登记了 key 但无候选：占位（调用侧一般不会走到，candidates 通常非空）。
        return f"{scope}.locator('')"
    exprs = [_cand_expr(scope, c) for c in cands]
    head = exprs[0]
    for e in exprs[1:]:
        head = f"{head}.or({e})"
    return f"{head}.first()"
```

（注：`_resolve_target` 中 `target.selector` 原始 CSS 分支保持不变——那是开发提供的转义出口，不在本 key→候选链治理范围内。）

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/python -m scripts.test_playwright_export`
Expected: PASS —— 打印 `OK test_playwright_export`（含既有注入自测断言全绿）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/playwright_exporter.py backend/scripts/test_playwright_export.py
git commit -m "fix(export): .or() 链稳定优先+末尾 .first(),消除 strict 多命中报错(镜像 runner)"
```

---

### Task 3: 前端合并策略 + 文案漂移不入库

**Files:**
- Create: `frontend/src/utils/selector-ranking.js`
- Modify: `frontend/src/views/SelectorAdmin.vue`（`matchStatus`、`submitAddAsKey`、`addMergedPreview`、相关 UI 文案）

**Interfaces:**
- Consumes: 无（Task 1 的 js 镜像）
- Produces: `isFragile(cand)`、`orderCandidates(cands)`（js），与 Task 1 语义一致

> 说明：本仓前端无 JS 单测框架（CLAUDE.md），故本 Task 以 `npm run build` + 手动验证替代自动测试；核心逻辑抽入纯函数 util 以便审查。

- [ ] **Step 1: 新建 js 镜像 util**

Create `frontend/src/utils/selector-ranking.js`:

```javascript
// 选择器候选「稳定/脆弱」口径（前端侧）。
// 脆弱 by = text/role：getByText 子串匹配、role 靠 name，均 copy 依赖，易失效且多命中会 strict 报错。
// 镜像后端 backend/app/services/selector_ranking.py（改一处必改另一处），
// 参照 tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates 分梯（text/role 为最低档）。
export const FRAGILE_BYS = new Set(['text', 'role'])

// 候选是否脆弱（by=text/role）。缺 by 按 css（稳定）处理。
export function isFragile(cand) {
  return FRAGILE_BYS.has(cand?.by || 'css')
}

// 稳定候选在前、脆弱候选在后，各自保持相对顺序（返回新数组）。
export function orderCandidates(cands) {
  const list = cands || []
  const stable = list.filter((c) => !isFragile(c))
  const fragile = list.filter((c) => isFragile(c))
  return [...stable, ...fragile]
}
```

- [ ] **Step 2: 引入 util 并加候选上限常量**

在 `SelectorAdmin.vue` 的 `<script setup>` import 区加入：
```javascript
import { isFragile, orderCandidates } from '@/utils/selector-ranking'
```
在探测相关常量附近（如 `probe` reactive 定义前）加入：
```javascript
// 单个 key 候选链上限：超出丢最不稳的（脆弱在尾，slice 自然丢尾），防链膨胀/优先级倒置。
const MAX_CANDIDATES = 6
```

- [ ] **Step 3: 细化 `matchStatus`（文案漂移 → 已存在，不更新）**

把 `matchStatus` 函数（约 540–548 行）替换为：
```javascript
// 给一个探测元素算标识:{ type:'exists'|'update'|'new', key?:命中的已有 key }
// 口径:best 已在库→已存在;否则看其它候选与哪个 key 重叠——
//   稳定候选命中且 best 是脆弱(纯文案漂移)→ 已存在(不更新,避免堆积脆弱候选);
//   稳定候选命中且 best 也是稳定(锚点变更)→ 更新;
//   仅脆弱候选命中 → 更新;都不命中 → 新增。
function matchStatus(el) {
  const idx = candIndex.value
  const best = el.best
  if (best && idx.has(candKey(best))) return { type: 'exists', key: idx.get(candKey(best)) }
  let stableHit = null
  let fragileHit = null
  for (const c of (el.candidates || [])) {
    if (!idx.has(candKey(c))) continue
    if (isFragile(c)) { if (!fragileHit) fragileHit = idx.get(candKey(c)) }
    else if (!stableHit) stableHit = idx.get(candKey(c))
  }
  if (stableHit) {
    return best && isFragile(best)
      ? { type: 'exists', key: stableHit }   // 纯文案漂移:稳定锚点已在库,best 只是文案 → 不更新
      : { type: 'update', key: stableHit }   // best 是新的稳定锚点 → 值得更新
  }
  if (fragileHit) return { type: 'update', key: fragileHit }
  return { type: 'new' }
}
```

- [ ] **Step 4: 合并策略——`submitAddAsKey` 就地替换 + 稳定优先 + 上限**

把 `submitAddAsKey` 里 update 分支的合并两行（约 693–696 行）：
```javascript
      const existing = target?.candidates || []
      // best 候选追加到头部（优先尝试）；去掉与新候选完全相同的旧项，避免重复。
      const merged = [add.cand, ...existing.filter((c) => !(c.by === add.cand.by && c.value === add.cand.value))]
      await patchSelector(add.targetId, { candidates: merged })
```
替换为：
```javascript
      const existing = target?.candidates || []
      // 合并：去掉与新候选完全相同的旧项；新候选若脆弱(text/role)则替换同 by 的旧脆弱项(就地替换、不累加)；
      // 再按稳定优先排序(脆弱降尾)、裁剪到上限，避免链膨胀与优先级倒置。
      const dropSameFragile = (c) => isFragile(add.cand) && c.by === add.cand.by
      const kept = existing.filter((c) => !(c.by === add.cand.by && c.value === add.cand.value) && !dropSameFragile(c))
      const merged = orderCandidates([add.cand, ...kept]).slice(0, MAX_CANDIDATES)
      await patchSelector(add.targetId, { candidates: merged })
```

- [ ] **Step 5: 合并预览 `addMergedPreview` 同步同一套逻辑**

把 `addMergedPreview` computed（约 649–655 行）替换为：
```javascript
// 合并后候选顺序预览：与 submitAddAsKey 的 merged 完全一致（就地替换脆弱同 by + 稳定优先 + 上限），标记新增项。
const addMergedPreview = computed(() => {
  if (!add.cand || !addTarget.value) return []
  const existing = addTarget.value.candidates || []
  const isDup = (c) => c.by === add.cand.by && c.value === add.cand.value
  const dropSameFragile = (c) => isFragile(add.cand) && c.by === add.cand.by
  const kept = existing.filter((c) => !isDup(c) && !dropSameFragile(c))
  const dup = existing.some(isDup)
  return orderCandidates([{ ...add.cand, _new: !dup }, ...kept.map((c) => ({ ...c, _new: false }))]).slice(0, MAX_CANDIDATES)
})
```

- [ ] **Step 6: 更新过时 UI 文案（"追加到头部" → "稳定优先合并")**

将模板中两处 form-hint（约 289、296 行）：
```
best 候选将追加到该 key 候选列表的<b>头部</b>（优先尝试）
```
```
合并后顺序（best 追加到头部，优先尝试）
```
分别改为：
```
best 候选将按<b>稳定优先</b>并入该 key（文案类候选自动降到末尾，超出上限丢弃最不稳的）
```
```
合并后顺序（稳定优先，脆弱文案候选降到末尾）
```

- [ ] **Step 7: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功（无报错），产物写入 `dist/`。

- [ ] **Step 8: 手动验证（关键路径）**

前置：后端起、有在线 runner、某项目已有一个 testid 锚点的 key（如 `titleText` = testid `chat-title`）。
1. 在「选择器管理」对该页 discover。
2. 若该元素文案有变但 testid 不变 → 该元素应标为**「已存在」**（不是「更新」），且 hideExists 时被隐藏。→ 验证「文案漂移不入库」。
3. 找一个只有文案候选、且与某 key 有重叠的元素 → 标为「更新」；点「加为 key」更新 → 合并预览里稳定候选在前、text 在末尾，且同 by 的旧 text 被替换而非新增。
4. 反复更新同一 key 多次 → 候选数不超过 6。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/utils/selector-ranking.js frontend/src/views/SelectorAdmin.vue
git commit -m "feat(selectors): 前端合并稳定优先+文案漂移不入库+候选上限(镜像后端口径)"
```

> 注：`frontend/dist/` 是否随本次提交重建，遵循本仓既有做法（历史提交常单独「重建 dist」）——由执行时确认，不在本 Task 强制。

---

## Self-Review

**1. Spec coverage（组件 1/2/3）**
- 组件 1（稳定/脆弱口径，两处镜像）→ Task 1（py）+ Task 3 Step 1（js）。✅
- 组件 2（exporter `.first()` + 稳定优先，建立在 `_js_comment` 之上）→ Task 2。✅
- 组件 3（matchStatus 文案漂移分类 + submitAddAsKey 就地替换/稳定优先/上限）→ Task 3 Steps 3–6。✅
- 组件 4/5（prompt desc + P1 桥接）→ **不在本计划**，Plan 2 覆盖（已在标题/Spec 行注明）。✅
- 一致性约束（不改表、两处镜像文档标注、无 pytest）→ Global Constraints + 各 Task 注释。✅

**2. Placeholder scan**：无 TBD/TODO/“add error handling”类占位；每个代码步都给了完整代码与精确期望字符串。前端无自动测试处已显式说明原因（CLAUDE.md 无 JS 框架）并给出可执行的 build + 手动步骤。✅

**3. Type consistency**：`is_fragile`/`order_candidates`（py，Task 1、Task 2 消费）与 `isFragile`/`orderCandidates`（js，Task 3）语义一致；`FRAGILE_BYS = {text, role}` 全计划统一；`candKey`/`candIndex`/`add.cand`/`patchSelector` 均沿用 SelectorAdmin.vue 现有符号；exporter 期望字符串与 `_cand_expr` 现有映射（getByTestId/getByRole{name}/getByLabel/getByText/getByPlaceholder/locator）逐一对齐。✅

## Execution Handoff（见文末对话）
