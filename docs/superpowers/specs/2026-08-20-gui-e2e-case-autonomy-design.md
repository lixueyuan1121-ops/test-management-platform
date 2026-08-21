# gui/e2e 用例自治(从初始态开始 + 结尾恢复)—— 设计稿

- 初稿:2026-08-20 ｜ **重新盘修订:2026-08-20(基线 `a8c5846`,选择器分支合并后)**
- 状态:待评审(重新盘修订版)
- 关联:`backend/app/services/claude_runner.py`(`build_testcase_prompt` / `build_script_prompt` 生成规则,本设计唯一改动点)、`tools/qalab-runner/runner.mjs`(连续执行上下文,只读参照)、`tools/qalab-runner/step-executor.mjs` + `gui-mcp/gui-core.mjs`(执行能力边界,只读参照)、`docs/ai-testgen-guide.md`(对外规则说明,需同步)
- 依赖已合并的选择器分支:`docs/superpowers/specs/2026-08-20-selector-quality-and-missing-key-bridge-design.md`(组件 4「缺 key→描述元素→选择器待补」、组件 5「缺 key→探测桥接补齐」)、`backend/app/services/selector_ranking.py`、`frontend/src/utils/selector-match.js`
- 范围:**纯 prompt 层改动**。不动执行器、不动 `_validate_script`/`_looks_like_e2e` 校验、不扩 action 白名单。

## 0. 重新盘修订说明(基于 a8c5846)

初稿暂停期间,选择器分支合并了「选择器质量治理 + 缺 key 桥接」。对本设计有三点实质影响,已并入下文:

1. **组件 4 已改 `build_testcase_prompt` 第 5 条缺 key 处理**:从"缺 key→判 manual"改为"缺 key→**起语义化新 key 名 + 在 desc 描述元素(文案/角色/位置)→ 走「选择器待补」**"。本设计的"进入导航 / 关弹窗"在缺对应 key 时**顺着这套机制**,不再是初稿的"用已有 key 尽力凑"。
2. **组件 5 提供缺 key→页面元素桥接**:待补用例可「定位缺失 key」→ 实时 discover → 截图高亮 Top 匹配(`selector-match.js` 按 key 名 + 用例上下文打分)→ 人点选确认 → 补 key → 一键重生恢复 gui/e2e。因此"自治步引入未注册 key 而降级待补"不再是死路,有低成本补齐闭环兜底。
3. **`build_script_prompt` 是组件 4 的漏网之鱼**:单条重生的 gui/e2e 分支仍写"找不到合适的就用最接近的语义 key 或 selector",与组件 4 矛盾。本设计的 §4.4 一并把它对齐(顺手修复不一致)。

## 1. 背景与根因

AI 测试助手生成的 gui/e2e 用例,单条看没问题,但**连续执行时后面的用例常"找不到元素"而 fail**。根因在执行上下文:

- `runner.mjs` 主循环对同一执行机上的多条 gui/e2e 用例,**复用同一个 namiclaw 客户端与同一个页面**:`ensureNamiclaw()` 只在 CDP 未就绪时冷启动,客户端已开着就直接复用。
- 每条用例 script 首步 `connect` = `ensureConnected()` + `waitForContentFrame()`,**只连 CDP、等业务 iframe,不导航、不刷新、不回初始页**。
- 用例之间,执行器**没有任何自动重置**。

结果:上一条用例结束时页面停在哪(子页面 / 弹窗开着 / 输入框有残留),下一条就从那个**中间态**开始;若下一条 script 隐含假设"从初始态开始",定位就会失败。

对照:api 用例 script 早有 `cleanup:true` 清理步概念;gui/e2e **完全没有清理/恢复概念**——这正是本设计要补的。

## 2. 目标 / 非目标

**目标**
- 生成规则强制 gui/e2e 用例**自治**:每条能单独、从初始态、一步步执行到底,不依赖上一条遗留状态。
- 每条用例**结尾恢复 UI 瞬态**,确保下一条能从干净状态开始。
- 顺带修正 e2e/gui 生成规则里与"自治"冲突或误导的表述(见 §4),并对齐 §0.3 的不一致。

**非目标**
- 不引入新 action(`goto`/`judge`/`press_key` 仅记录为后续,见 §6)。
- 不改执行器、不改校验器(`_validate_script`/`_looks_like_e2e`)、不改 action 白名单、不改数据库。
- 不改选择器分支已落地的组件 1–5(本设计**建立其上、复用其闭环**,不覆盖)。
- 不追求数据副作用的完整清理(gui/e2e 无可靠删除机制,尽力而为)。
- 不做真实生成端到端评测(无 claude CLI 环境),只做规则文本与校验兼容的单元级验证。

## 3. 核心决策(已确认)

1. **起点策略 = 进入即导航**:`connect` 后用**导航/入口类 key 显式进入本用例目标功能页**,不假设当前已在该页。
   - 优先用清单里已注册的导航 key(如 navHome/navTasks/navProjects…);
   - **清单无对应导航 key 时**,按第 5 条既定缺 key 规则:起语义化 key 名(如 `navHomeBtn`)+ 在 desc 描述元素("左侧导航栏「首页」项"),走选择器待补,后续经组件 5 桥接补齐。**不臆造 selector、不因此直接判 manual**。
2. **登录态 = 默认已登录主界面**:约定起点为已登录状态;**登录本身单列为一条 e2e 用例**,其它用例不重复登录(契合执行机复用会话)。
3. **恢复范围 = UI 瞬态恢复**,优先级(前两者为要点,第三为收尾):
   1. **关闭本用例打开的弹窗/面板**(点其关闭 key;无对应 key 时同决策 1,起语义 key 名如 `closeDialogBtn` + desc 描述"弹窗右上角关闭按钮",走待补桥接)——最关键,防残留模态框遮挡下一条的开头导航;
   2. **清空本用例填写但未提交的输入**(`fill` 空串 `""`;不引入新 key)——防残留文案影响下一条;
   3. **导航回一个稳定页**(复用进入段的导航 key,本用例进入页或主界面皆可,不强制统一回首页)——因下一条会自行"进入即导航",故此步为收尾而非硬性。
   - 数据副作用(如新建数据)**尽力而为**:有便捷删除入口就删,没有就在 `desc` 注明,不强制、不因此增删大量步骤。
4. **范围 = 仅改生成 prompt**:`build_testcase_prompt` + `build_script_prompt`,外加文档同步。不动执行器与校验。
5. **与「选择器待补 + 桥接」闭环协同(重新盘新增,含权衡)**:自治步(导航/恢复)可能用到未注册 key,`_validate_script` 会把整条降级为**"待补 manual"——即便其核心验证点 key 已注册**。**接受此权衡**,理由:
   - 导航/关闭属**高频复用 key**,补一次全项目受益;
   - 组件 5 桥接使补齐低成本(定位→探测→点选→重生),不再是死路;
   - 把隐性缺口显性化(待补标签 + 缺 key 名 + desc),正是选择器分支的设计哲学;
   - 自治步统一用**语义化 key 名 + 具体 desc**,恰好喂饱 `selector-match.js` 的匹配信号(key 名 + 用例上下文 vs 元素文案/候选),提升桥接命中率。

> 三段互补(双保险):**恢复段**把页面还原到干净起点;**进入段**保证即便页面不干净也能把它拉到目标页。二者叠加才能抗住"连续执行相互污染"。

## 4. 具体改动(prompt 措辞级)

### 4.1 `build_testcase_prompt` 第 5 条 —— 在缺 key 规则(现第 263 行)之后追加「用例自治」小节

保持第 5 条现有 action/target/args/断言/缺 key 规则**原文不动**(含 test 依赖的"选择器待补""描述这个元素"关键字),在其末尾追加:

> - **用例自治(关键——直接决定连续执行成功率,务必执行)**:多条用例在**同一客户端、同一页面**上连续执行,执行器不会在用例之间重置页面。因此每条用例必须能**单独、从初始态、一步步执行到底**,不得依赖上一条遗留的页面状态。按「进入 → 执行 → 恢复」三段组织 script:
>   · **进入(不假设当前页)**:`connect` 之后,**先用导航/入口类 key 显式导航进入本用例目标功能所在页**(优先用清单里的导航 key 如 navHome/navTasks;清单没有就按上一条缺 key 规则起语义 key 名 + desc 描述该导航元素),再开始操作;不要假设"当前已在该页"。默认起点为**已登录的应用主界面**(登录流程请单列为一条 e2e 用例,其它用例不重复写登录步)。
>   · **执行**:完成本用例的操作与断言。
>   · **恢复(结尾还原,确保下一条能顺利开始)**:用例最后把本用例引入的 UI 瞬态还原——① 关闭本用例打开的弹窗/面板(点其关闭 key;无 key 同上起语义 key 名 + desc);② 清空本用例填写但未提交的输入框(`fill` 空串 `""`);③ 视情况导航回一个稳定页。数据副作用(如新建数据)尽力而为、无便捷删除入口时在 `desc` 注明即可,不强制。

### 4.2 `build_testcase_prompt` 第 6 条 —— 协调 gui/e2e 偏重、步数,并换掉登录起点正例

把"gui 单点 2–4 步"改为(与自治规则协调):

> - **gui**:聚焦"某一个元素/文案对不对"的单点验证,但**仍需自治**——结构为「进入导航 + 单点操作/断言 + 收尾恢复」,通常 **3–6 步**(比纯断言略长是正常的,因为含进入与恢复);断言聚焦单点,不串联整条业务流程。
> - **e2e**:端到端多步流程,**≥5 步**,从已登录主界面**导航进入 → 操作 → 关键节点分别断言 → 收尾恢复**;异步处(发消息/提交后)必须插 `wait_response`/`wait_for` 再断言。
> - **判定自检**:e2e 若剔除"进入/恢复"步后实质交互不足(<2 个 click/fill/wait_response)或总步数过短 → 其实是 gui,改判 kind=gui。

**替换正例**(现第 270–271 行,去掉"以登录为通用起点"的误导,体现进入+恢复):

- 正例(gui,单点,含自治):`connect → click(navTasks) → wait_for(任务页锚点) → assert_visible(目标元素) → click(navHome)[恢复回起点]`
- 正例(e2e,多步,含自治):`connect → click(navTasks) → click(新建按钮) → fill(表单字段) → click(提交) → wait_for(结果锚点) → assert_text(结果文案,contains) → [恢复:关弹窗 / click(navHome)]`
- 登录单列说明(呼应"其它用例默认已登录"):`connect → fill(loginUserName) → fill(loginPassword) → click(loginAgree) → click(loginSubmit) → wait_for(homepageTitle) → assert_visible(homepageTitle)`

> 正例中的 key 名(navTasks/navHome/homepageTitle 等)仅示意;实际只能用注入清单里的 key,缺则按缺 key 规则起语义 key 名 + desc。

### 4.3 `_looks_like_e2e` 影响复核(不改代码,仅确认)

`_looks_like_e2e`(≥5 步且 ≥2 交互)不变。进入/恢复步增加 click/fill 数量,只会让"真 e2e"更稳判为 e2e,不影响 gui(该校验仅对 kind=e2e 生效)。gui 步数上限校验层本就不强制,放宽到 3–6 步只是 prompt 软引导。

### 4.4 `build_script_prompt`(单条重生)同步 —— 自治 + 对齐组件 4 缺 key 话术

单条重生的 gui/e2e 分支(现第 335 行"只能用下方 key 清单里的 key,找不到合适的就用最接近的语义 key 或 selector"):

1. **对齐组件 4**:把"找不到合适的就用最接近的语义 key 或 selector"改为与 `build_testcase_prompt` 第 5 条一致——"清单无合适 key 时,起语义化新 key 名 + 在 desc 描述元素,走选择器待补;不臆造 selector"。
2. **补自治精要**:`connect` 后先用导航/入口 key 显式进入目标页(不假设当前页,默认已登录主界面);结尾恢复 UI 瞬态(关本用例开的弹窗、清填写的输入、必要时回起点页)。
3. gui 的"2-4 步"同步为"3-6 步(含进入与恢复)"。

### 4.5 文档同步 `docs/ai-testgen-guide.md`

- 「用例类型与自动化」表格:`gui` 行"2–4 步"改为"含进入/恢复通常 3–6 步"。
- 表下补一小节「用例自治(连续执行不打架)」:一句话说明每条用例从初始态开始、结尾恢复,是提升连续执行成功率的关键约定;缺导航/关闭 key 会走「选择器待补」,用「定位缺失 key」桥接补齐后即恢复自动化。

## 5. 兼容性与不改动项

- **不改**:`runner.mjs` / `step-executor.mjs` / `gui-core.mjs`;`_validate_script` / `_looks_like_e2e` / `_VALID_ACTIONS`;`parse_testcases` 主流程;数据库与 schema;选择器分支组件 1–5。
- **向后兼容**:新规则只让生成的 script 更完整(多几步 click/fill),全部落在现有 action 白名单内;老 script 仍可执行、可解析。
- **`deepseek` 引擎自动获益**:两引擎复用同一 `build_testcase_prompt`/`parse_testcases`,改 prompt 后 deepseek 一并生效。

## 6. 记录为后续(本次不做)

- **暴露 `goto`**:执行器已支持,可作"回初始态"另一手段;但对 iframe 嵌套 SPA(namiclaw)导航顶层 URL 可能破坏登录态,需谨慎评估。
- **暴露 `judge`**:执行器已支持主观判定步,开放后可减少 manual 降级;需同步 prompt + 校验 + `_VALID_ACTIONS`。
- **新增 `press_key`(Esc/Enter)**:执行器目前无按键能力;补上可更可靠关弹窗恢复;需改执行器 + 校验 + prompt。

## 7. 验证

无 claude CLI 环境,不做真实生成端到端。仿 `backend/scripts/test_*.py` 写临时脚本(`tmp_` 前缀,验后删除):

1. **规则注入**:`build_testcase_prompt("需求", project_id=None)` 与 `build_script_prompt("gui"/"e2e", ...)` 输出**包含**新规则关键字(如"用例自治""恢复""进入")。
2. **不破坏既有断言**:`build_testcase_prompt` 仍满足 `test_build_testcase_prompt_api.py` 的现有断言——**"选择器待补""描述这个元素""connect""assert_visible""自动化优先""优先判 gui/e2e""11." 等关键字与条目编号不得丢失**(自治小节是第 5 条内追加,不改 6–11 顶层编号)。跑一遍 `python -m scripts.test_build_testcase_prompt_api` 应仍 OK。
3. **校验兼容**:构造一条"进入导航 + 操作 + 断言 + 恢复"的三段式样例 script,喂 `_validate_script(script, valid_keys=set())` 与 `parse_testcases(json_of_case)`,断言**通过、不被降级 manual**、`kind` 保持 gui/e2e。
4. **e2e 判定**:三段式 e2e 样例经 `_looks_like_e2e` 返回 `True`(不被误降 gui)。
5. **缺 key 待补路径**:构造一条含未注册导航 key(如 `navHomeBtn`)的三段式 gui script,喂 `parse_testcases`(project_id 指向有注册表的项目),断言被打上 `[选择器待补]` 标记且列出该 key —— 验证自治规则与组件 4/5 闭环衔接。
