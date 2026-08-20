# gui/e2e 用例自治生成规则 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 生成的 gui/e2e 用例「自治」(每条从初始态开始、结尾恢复 UI 瞬态),提升连续执行成功率。

**Architecture:** 纯 prompt 层改动。在 `build_testcase_prompt`(批量生成)与 `build_script_prompt`(单条重生)里加入「进入→执行→恢复」三段式规则,并同步对外文档。执行器、校验器、action 白名单、数据库全不动;新规则全部落在现有 action(click 导航/关闭、fill("") 清输入、assert_*)内。

**Tech Stack:** Python(FastAPI 后端);测试用本仓库手写脚本 `backend/scripts/test_*.py` + `python -m scripts.<name>`(仓库**无 pytest/lint**,勿臆造)。

**依据 spec:** `docs/superpowers/specs/2026-08-20-gui-e2e-case-autonomy-design.md`(已评审)。

## Global Constraints

- **只改这三处**:`backend/app/services/claude_runner.py` 的 `build_testcase_prompt`、`build_script_prompt`,以及 `docs/ai-testgen-guide.md`。不改 `runner.mjs`/`step-executor.mjs`/`gui-core.mjs`、不改 `_validate_script`/`_looks_like_e2e`/`_VALID_ACTIONS`、不改数据库。
- **不引入新 action**(`goto`/`judge`/`press_key` 仅记录为后续,本计划不做)。
- **缺 key 话术必须与选择器分支组件 4 一致**:清单无 key 时「起语义化 key 名 + 在 desc 描述元素 → 走选择器待补」,**不用 selector 兜底、不直接判 manual**。
- **不得破坏 `test_build_testcase_prompt_api.py` 的现有断言**:prompt 里必须仍含 `选择器待补`、`描述这个元素`、`connect`、`assert_visible`、`自动化优先`、`优先判 gui/e2e`,以及尾部条目编号 `11.`。
- **两引擎自动获益**:claude/deepseek 复用同一 prompt,改后 deepseek 一并生效,无需单独改 `generators/deepseek_runner.py`。
- **测试运行目录**:所有 `python -m scripts.*` 命令须在 `backend/` 下执行。
- **无关改动**:工作区可能已有 `playwright_exporter.py` 的 U+2028/U+2029 安全修复(独立改动,已验证),**不属于本计划**,勿在本计划的 commit 里混入。

---

### Task 1: `build_testcase_prompt` 三段式自治规则 + gui/e2e 步数与正例

**Files:**
- Create: `backend/scripts/test_case_autonomy.py`
- Modify: `backend/app/services/claude_runner.py`(`build_testcase_prompt`,第 263 行后插入;第 265–271 行替换)

**Interfaces:**
- Consumes: `build_testcase_prompt(requirement, project_id=None, pages=None) -> str`、`_validate_script(script, valid_keys) -> (list, str|None)`、`_looks_like_e2e(script) -> bool`(均已存在于 `claude_runner.py`)。
- Produces: 无新符号;仅改变 `build_testcase_prompt` 输出文本内容。

- [ ] **Step 1: 写失败测试**

Create `backend/scripts/test_case_autonomy.py`:

```python
"""gui/e2e 用例自治生成规则自测(project_id=None 免 DB)。
运行: cd backend && python -m scripts.test_case_autonomy
"""
from app.services.claude_runner import (
    build_testcase_prompt,
    build_script_prompt,
    _validate_script,
    _looks_like_e2e,
)


def test_testcase_prompt_has_autonomy_rules():
    p = build_testcase_prompt("测试任务页新建与校验", project_id=None)
    # 三段式自治关键字
    assert "用例自治" in p, "缺『用例自治』规则"
    assert "进入" in p and "恢复" in p, "缺进入/恢复三段式"
    assert "不假设" in p, "缺『不假设当前页』约束"
    assert "已登录" in p, "缺『默认已登录主界面』起点约定"
    # gui 步数放宽到 3–6 步(接受 en dash 或 hyphen)
    assert ("3–6 步" in p) or ("3-6 步" in p), "gui 步数应放宽到 3–6 步"
    # 回归:组件4缺 key 话术与既有断言未被破坏
    assert "选择器待补" in p and "描述这个元素" in p
    assert "connect" in p and "assert_visible" in p


def test_three_phase_gui_script_not_downgraded():
    # 进入(导航)→ 等待 → 断言 → 恢复(回起点),全用已注册 key
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "进入任务页"},
        {"action": "wait_for", "target": {"key": "taskList"}, "args": {"timeout_ms": 6000}, "desc": "等任务页"},
        {"action": "assert_visible", "target": {"key": "taskList"}, "desc": "断言任务列表可见"},
        {"action": "click", "target": {"key": "navHome"}, "desc": "恢复:回首页"},
    ]
    valid = {"navTasks", "taskList", "navHome"}
    norm, err = _validate_script(script, valid)
    assert err is None, f"三段式 gui 不应被判非法: {err}"
    assert len(norm) == 5


def test_three_phase_e2e_recognized():
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "进入"},
        {"action": "click", "target": {"key": "newTaskBtn"}, "desc": "新建"},
        {"action": "fill", "target": {"key": "taskTitleInput"}, "args": {"text": "自动化任务"}, "desc": "填标题"},
        {"action": "click", "target": {"key": "submitBtn"}, "desc": "提交"},
        {"action": "wait_for", "target": {"key": "taskList"}, "args": {"timeout_ms": 8000}, "desc": "等列表刷新"},
        {"action": "assert_text", "target": {"key": "taskList"}, "args": {"expected": "自动化任务", "contains": True}, "desc": "断言出现"},
        {"action": "click", "target": {"key": "navHome"}, "desc": "恢复:回首页"},
    ]
    assert _looks_like_e2e(script) is True, "多步三段式应识别为 e2e"


def main():
    test_testcase_prompt_has_autonomy_rules()
    test_three_phase_gui_script_not_downgraded()
    test_three_phase_e2e_recognized()
    print("OK test_case_autonomy")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && python -m scripts.test_case_autonomy`
Expected: FAIL —— `AssertionError: 缺『用例自治』规则`(其余两个校验测试此时已能通过,因为校验器未改)。

- [ ] **Step 3: 在第 5 条缺 key 规则后插入「用例自治」小节**

在 `build_testcase_prompt` 中把这段(第 263–264 行):

```
   - target.key 优先取下方清单里的 key。**清单里没有合适 key 时**：不要瞎编 selector、也不要直接判 manual——给该元素起一个语义化新 key 名（如 submitOrderBtn），照常写进 script，并在该步 desc 里**描述这个元素**（可见文案 / 角色 / 页面位置）。用到未注册 key 的用例会被自动标为「选择器待补」，补齐后即可自动执行；只有确无界面元素可操作/断言时才判 manual、script=[]。
6. 按 kind 的 script 编写偏重（**务必区分，别把 e2e 写成 gui**）：
```

替换为:

```
   - target.key 优先取下方清单里的 key。**清单里没有合适 key 时**：不要瞎编 selector、也不要直接判 manual——给该元素起一个语义化新 key 名（如 submitOrderBtn），照常写进 script，并在该步 desc 里**描述这个元素**（可见文案 / 角色 / 页面位置）。用到未注册 key 的用例会被自动标为「选择器待补」，补齐后即可自动执行；只有确无界面元素可操作/断言时才判 manual、script=[]。
   - **用例自治（关键——直接决定连续执行成功率，务必执行）**：多条用例在**同一客户端、同一页面**上连续执行，执行器不会在用例之间重置页面。每条用例必须能**单独、从初始态、一步步执行到底**，不得依赖上一条遗留的页面状态。按「进入→执行→恢复」三段组织：
     · **进入（不假设当前页）**：connect 后先用导航/入口类 key（如 navHome/navTasks，见下方清单）**显式进入本用例目标功能页**，再开始操作；不要假设“当前已在该页”。默认起点为**已登录的应用主界面**（登录流程单列为一条 e2e 用例，其它用例不重复写登录步）。清单无对应导航 key 时，按上一条缺 key 规则起语义化 key 名 + desc 描述该导航元素。
     · **执行**：完成本用例的操作与断言。
     · **恢复（结尾还原，确保下一条能顺利开始）**：用例最后还原本用例引入的 UI 瞬态——①关闭本用例打开的弹窗/面板（点其关闭 key；无 key 同上起语义 key 名 + desc）；②清空本用例填写但未提交的输入框（fill 空串 ""）；③视情况导航回起点页。数据副作用（如新建数据）尽力而为、无便捷删除入口时在 desc 注明即可，不强制。
6. 按 kind 的 script 编写偏重（**务必区分，别把 e2e 写成 gui**）：
```

- [ ] **Step 4: 替换第 6 条 gui/e2e 偏重、步数与正例**

把这段(第 265–271 行):

```
   - **gui**：单点/局部验证，**2–4 步**即可——connect → (最多一两个 click/fill/wait_for) → assert_*。聚焦"某一个元素/文案对不对"，不要串联整条业务流程。
   - **e2e**：**端到端多步流程，通常 ≥5 步**，体现"从入口一路操作到结果"。必须串联多个界面动作（如 登录→导航→输入→提交），并在**关键节点分别断言**（不止最后断一次）。
     · 若流程中触发了 AI 生成/异步加载（发消息、提交后等结果），**必须插入 wait_response 或 wait_for** 再断言，不能立刻断。
     · 一条 e2e 的 script 明显比 gui 长、动作更丰富；若你发现某"e2e"只需 2–3 步就能验完，说明它其实是 gui，请改判 kind=gui。
   - **判定自检**：kind=e2e 但 script 少于 5 步或无跨界面串联 → 要么补足步骤，要么改判 gui。
   正例(gui,单点)：connect → wait_for(navTasks) → assert_visible(navTasks)
   正例(e2e,多步)：connect → click(loginAccountTab) → fill(loginUserName) → fill(loginPassword) → click(loginSubmit) → wait_for(homepageTitle) → assert_visible(homepageTitle) → assert_text(homepageTitle,"早上好",contains)
```

替换为:

```
   - **gui**：单点/局部验证，但**仍需自治**——结构为「进入导航 + 单点操作/断言 + 收尾恢复」，通常 **3–6 步**（比纯断言略长是正常的，含进入与恢复）。断言聚焦单点，不要串联整条业务流程。
   - **e2e**：**端到端多步流程，通常 ≥5 步**，从已登录主界面**导航进入 → 操作 → 关键节点分别断言 → 收尾恢复**。必须串联多个界面动作，并在**关键节点分别断言**（不止最后断一次）。
     · 若流程中触发了 AI 生成/异步加载（发消息、提交后等结果），**必须插入 wait_response 或 wait_for** 再断言，不能立刻断。
     · 一条 e2e 的 script 明显比 gui 长、动作更丰富；若你发现某"e2e"剔除进入/恢复后只需 2–3 步就能验完，说明它其实是 gui，请改判 kind=gui。
   - **判定自检**：kind=e2e 但剔除进入/恢复步后实质交互不足或总步数过短 → 改判 gui。
   正例(gui,单点,含自治)：connect → click(navTasks) → wait_for(任务页锚点) → assert_visible(目标元素) → click(navHome)[恢复回起点]
   正例(e2e,多步,含自治)：connect → click(navTasks) → click(新建按钮) → fill(表单字段) → click(提交) → wait_for(结果锚点) → assert_text(结果文案,contains) → click(navHome)[恢复]
   登录单列(其它用例默认已登录)：connect → fill(loginUserName) → fill(loginPassword) → click(loginAgree) → click(loginSubmit) → wait_for(homepageTitle) → assert_visible(homepageTitle)
```

- [ ] **Step 5: 跑本任务测试,确认通过**

Run: `cd backend && python -m scripts.test_case_autonomy`
Expected: `OK test_case_autonomy`

- [ ] **Step 6: 跑回归测试,确认未破坏既有断言**

Run: `cd backend && python -m scripts.test_build_testcase_prompt_api`
Expected: `OK test_build_testcase_prompt_api`

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/test_case_autonomy.py backend/app/services/claude_runner.py
git commit -m "feat(ai-gen): build_testcase_prompt 三段式用例自治规则

进入即导航到目标页(不假设当前页,默认已登录主界面)+ 结尾恢复 UI 瞬态;
gui 放宽到 3-6 步、修正 e2e 正例(去掉登录起点误导);缺 key 沿用选择器待补话术。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `build_script_prompt`(单条重生)同步自治规则并对齐组件 4 缺 key 话术

**Files:**
- Modify: `backend/scripts/test_case_autonomy.py`(加两个测试函数 + main 调用)
- Modify: `backend/app/services/claude_runner.py`(`build_script_prompt` 的 gui/e2e 分支,第 330、334、335 行)

**Interfaces:**
- Consumes: `build_script_prompt(kind, title, steps, expected, project_id=None) -> str`(已存在,Task 1 测试已 import)。
- Produces: 无新符号;仅改变 `build_script_prompt` 输出文本。

- [ ] **Step 1: 追加失败测试**

在 `backend/scripts/test_case_autonomy.py` 的 `def main():` **之前**插入两个函数:

```python
def test_script_prompt_gui_autonomy():
    p = build_script_prompt("gui", "任务页新建校验", "打开任务页→新建", "列表出现新项", project_id=None)
    assert "用例自治" in p, "单条重生 gui 缺自治规则"
    assert ("3-6 步" in p) or ("3–6 步" in p), "gui 步数应为 3-6 步"
    # 对齐组件4:缺 key 走选择器待补,不再用 selector 兜底
    assert "选择器待补" in p, "应对齐组件4缺 key 话术"
    assert "最接近的语义 key 或 selector" not in p, "旧的『用 selector 兜底』话术应移除"


def test_script_prompt_e2e_autonomy():
    p = build_script_prompt("e2e", "登录后发消息", "登录→发消息→等回复", "有回复", project_id=None)
    assert "用例自治" in p, "单条重生 e2e 缺自治规则"
    assert "≥5 步" in p, "e2e 应保留 ≥5 步要求"
```

并在 `def main():` 体内、`test_three_phase_e2e_recognized()` 之后加两行调用:

```python
    test_script_prompt_gui_autonomy()
    test_script_prompt_e2e_autonomy()
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && python -m scripts.test_case_autonomy`
Expected: FAIL —— `AssertionError: 单条重生 gui 缺自治规则`

- [ ] **Step 3: 改 `build_script_prompt` 三处**

**(3a)** 把第 330 行:

```
   - target:优先 {{"key":"<下方清单里的 key>"}};清单没有的元素才用 {{"selector":"<CSS>"}}
```

替换为:

```
   - target:优先 {{"key":"<下方清单里的 key>"}};清单没有合适 key 时,起语义化新 key 名并在 desc 描述该元素(可见文案/角色/位置),走「选择器待补」,不要臆造 selector
```

**(3b)** 把第 334 行:

```
   - {'e2e:多步端到端(≥5 步)、跨界面串联、异步处插 wait_response' if kind == 'e2e' else 'gui:单点聚焦,2-4 步即可'}
```

替换为(保留原三元表达式,gui 分支改步数,并新增一行自治要点):

```
   - {'e2e:多步端到端(≥5 步)、跨界面串联、异步处插 wait_response' if kind == 'e2e' else 'gui:单点聚焦,含进入与恢复通常 3-6 步'}
   - **用例自治**:connect 后先用导航/入口 key 显式进入目标页(不假设当前页,默认已登录主界面);结尾恢复 UI 瞬态(关本用例开的弹窗、清填写的输入、必要时导航回起点页),确保连续执行不相互污染
```

**(3c)** 把第 335 行:

```
3. 只能用下方 key 清单里的 key,找不到合适的就用最接近的语义 key 或 selector:
```

替换为:

```
3. target.key 优先取下方清单里的 key;清单无合适 key 时起语义化新 key 名 + desc 描述元素(走「选择器待补」),不要臆造 selector:
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && python -m scripts.test_case_autonomy`
Expected: `OK test_case_autonomy`

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/test_case_autonomy.py backend/app/services/claude_runner.py
git commit -m "feat(ai-gen): build_script_prompt 同步自治规则并对齐缺 key 话术

单条重生 gui/e2e 补三段式自治;缺 key 从『用 selector 兜底』改为『语义 key 名+desc→选择器待补』
以对齐 build_testcase_prompt(组件4);gui 步数同步为 3-6 步。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 同步对外文档 `docs/ai-testgen-guide.md`

**Files:**
- Modify: `docs/ai-testgen-guide.md`(第 12 行 gui 步数;「用例类型与自动化」小节末尾加自治说明)

**Interfaces:** 无(纯文档)。

- [ ] **Step 1: 改 gui 行步数**

把第 12 行:

```
| **gui** | 客户端界面上单点操作+断言(2–4 步) | ✅ 走 step-executor |
```

替换为:

```
| **gui** | 客户端界面上单点操作+断言(含进入/恢复通常 3–6 步) | ✅ 走 step-executor |
```

- [ ] **Step 2: 在小节末尾加「用例自治」说明**

把这段(第 23–25 行):

```
- 项目**没配 api 契约**时,本会判 api 的验证点会**自动改判 gui/e2e**(因为这类系统接口多半客户端外调不通)。

> 前提:gui/e2e 能落地,靠项目里有对应的**选择器 key**(在「选择器管理」用探测积累)。key 越全,能自动化的用例越多。
```

替换为(在其后追加一段):

```
- 项目**没配 api 契约**时,本会判 api 的验证点会**自动改判 gui/e2e**(因为这类系统接口多半客户端外调不通)。

> 前提:gui/e2e 能落地,靠项目里有对应的**选择器 key**(在「选择器管理」用探测积累)。key 越全,能自动化的用例越多。

**用例自治(连续执行不打架)**:执行机会在同一客户端连续跑多条 gui/e2e 用例、用例间不重置页面。故生成规则要求每条用例**从初始态开始**(connect 后先用导航 key 显式进入目标页、默认已登录主界面、登录单列为一条 e2e)、并在**结尾恢复 UI 瞬态**(关本用例开的弹窗、清填写的输入、必要时回起点页)。缺导航/关闭 key 时会走「选择器待补」,用「定位缺失 key」桥接补齐后即恢复自动化。
```

- [ ] **Step 3: 验证文档含新内容**

Run: `grep -c "用例自治" docs/ai-testgen-guide.md && grep -c "3–6 步" docs/ai-testgen-guide.md`
Expected: 两行都输出 `1`(各命中一次)。

- [ ] **Step 4: Commit**

```bash
git add docs/ai-testgen-guide.md
git commit -m "docs(ai-gen): 用例自治规则同步到 AI 测试助手使用说明

gui 步数 2-4 → 3-6(含进入/恢复);补『用例自治(连续执行不打架)』小节。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- spec §4.1(第 5 条自治小节)→ Task 1 Step 3 ✅
- spec §4.2(第 6 条步数/正例)→ Task 1 Step 4 ✅
- spec §4.3(`_looks_like_e2e` 不改,仅确认)→ Task 1 `test_three_phase_e2e_recognized` 断言现有校验对三段式仍判 True ✅
- spec §4.4(`build_script_prompt` 同步 + 对齐组件4)→ Task 2 ✅
- spec §4.5(文档同步)→ Task 3 ✅
- spec §7 验证 1–4 → `test_case_autonomy.py`(规则注入 + 校验兼容 + e2e 判定)+ Task 1 Step 6 回归 ✅
- spec §7 验证 5(缺 key 待补路径,需带注册表的 project_id)→ 依赖 DB,非 hermetic,**本计划未纳入自动化**(与仓库 hermetic 脚本风格一致);由 spec §7 保留为手动验证点。

**2. Placeholder scan:** 每个 code/命令步骤均给出完整内容与确切 old/new 文本,无 TBD/TODO/“类似上文”。

**3. Type consistency:** 全程未引入新符号;`build_testcase_prompt`/`build_script_prompt`/`_validate_script`/`_looks_like_e2e` 签名与 `claude_runner.py` 现状一致;测试 import 的私有函数(下划线)沿用 `test_playwright_export.py` 既有惯例。

> 注:所有 old_string 取自当前 `claude_runner.py`(基线 `a8c5846`)与 `ai-testgen-guide.md`。执行前若行号/文本有出入,以文件实际内容为准做等价替换(锚点文字唯一)。
