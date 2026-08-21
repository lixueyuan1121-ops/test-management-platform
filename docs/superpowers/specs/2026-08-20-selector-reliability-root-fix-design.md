# 用例执行可靠性根治（选择器定位 + 前置状态，全链路分层）设计

> 状态：**已评审 / 已批准（2026-08-20）**（五层方案；分期 P0=L1+L5复位核心 → P1=L4 → P2=L2+L5归类 → P3=L3；共享导航宏 / 软复位不做）
> 日期：2026-08-20 ｜ 方向：增量、复用现有架构、YAGNI
> 目标：**彻底解决用例执行失败中「选择器/环境」类的假失败**——坏选择器进不了库、进了也不致命、失败能被正确归类而非伪装成功能 bug；多条用例串行时不带脏页面状态进入下一条。
> 下一步：writing-plans 出实现计划（本文只写设计，未写代码）。

## 1. 背景与触发（用户原话 + 实例）

两个诉求（同属「彻底解决因选择器/前置状态导致的用例执行失败」）：
1. **选择器坏/失效致执行失败**：「为了每条都能顺利执行加了恢复初始状态，导致用例在最开始执行就因选择器异常不能正常跑。」
2. **串行执行前置状态污染**：「多条任务串行执行时，当前执行的前置条件不满足（比如已在某个选中态，但用例执行需要在输入框直接输入）类似这样的场景。」

触发实例（run_id=186 回写 fail）：一条 gui script 用例
```
step1 connect
step2 wait_for {key: homepageTitle}   ← 进入段：等首页加载
step3 assert_visible {key: skills_select}
step4 screenshot
```
报错：`step2「wait_for」执行出错:未命中 key "homepageTitle";已试(含 iframe): shell:undefined=undefined | vm:undefined=undefined`。

## 2. 现状架构（已 grep 核对真实代码）

### 2.1 选择器注册表（DB 单一事实源）
- `SelectorKey`（`backend/app/models/selector.py:9`）：`(project_id, sub_product, key)` 唯一；`candidates` 用 TEXT 存 JSON 字符串（兼容 MySQL 5.6），元素形状约定 `{by, value, name?}`，`by ∈ testid/role/label/text/placeholder/css`；另有 `frame`（auto/shell/vm/`url:<host>`）、`page`（纯分组）、`desc`。
- `backend/app/services/selectors.py`：`resolved_registry`（共享 ∪ 子产品，子产品覆盖）、`_cands`（`selectors.py:8` 仅保证返回 list，**不校验元素结构**）、`shared_key_set`（`selectors.py:67`，仅返回 **key 名集合**，不看候选）。

### 2.2 后端写入路径与校验
- `backend/app/schemas/selector.py:12`：`SelectorKeyIn.candidates: list[dict[str, Any]] = []`、`SelectorKeyPatch.candidates`（`:19`）——**宽松，允许 `[{}]` 等缺字段的空对象元素**。
- `backend/app/api/selectors.py`：`create_key`（`:66`）/`patch_key`（`:84`）直接 `json.dumps(body.candidates)` 落库；`import_legacy`（`:132`）从内置 `selectors.json` 幂等导入（同名 key 跳过）。

### 2.3 runner 定位引擎（消费注册表）
- `tools/qalab-runner/gui-mcp/gui-core.mjs`：`resolveKey`（`:148`）按 `scopesFor(frame) × entry.candidates` 组 plan，`byToLocator`（`:115`）按 `cand.by` 建 Playwright 定位器；未命中抛错，诊断串由 `:166` `${s.name}:${cand.by}=${cand.value||cand.name}` 拼出——**候选缺字段即 `undefined=undefined`**。`setRegistry`（`:196`）是 **`REGISTRY = registry` 整体替换**（非逐 key 合并）。内置注册表路径 `SELECTORS_PATH`（`:12`，仓库 `selectors.json` 的 57 key）。
- `tools/qalab-runner/runner.mjs`：`fetchRegistry`（`:131`）按 project 拉 `/api/selectors`，**仅当整个 registry 为空**才保留内置兜底（`:137-138`）；非空即 `setRegistry` 整体覆盖（`:399-400`、`:445-446`）。
- 进入方式：`goto`（`gui-core.mjs:277`）只导航**顶层** `work.n.cn/claw`；功能页在**动态 `vm_id` 的 iframe SPA**（`:131`），**无稳定 URL 直达**——进入段只能点导航 key。

### 2.4 执行与回写
- `tools/qalab-runner/step-executor.mjs`：`failAt`（`:52`）统一产出 `verdict:"fail"`；定位/操作抛错在 catch（`:110-113`）→ fail；断言不通过 assert_visible（`:98`）/assert_text（`:105`）→ fail——**两类失败不区分**。
- `backend/app/models/exec_queue.py`：`ExecRun.verdict`（`:52`，String16，现 pass/fail）、`reason`（`:53`）、`status`（`:46`，ExecKind/ExecStatus 队列状态）；回写同步 `checklist_item.exec_status`（`:7-11` 注释）。

### 2.5 生成侧校验链（选择器盲区所在）
- `backend/app/services/claude_runner.py`：`_registered_keys`（`:102`）→ `shared_key_set`（**仅 key 名**）；`_validate_script`（校验 `target.key ∈ valid_keys` + 结构）；`revalidate_for_backfill`（`:410`，确定性回填的校验，也走 `_registered_keys`）；`parse_testcases`（`:641` 段）用 `_unregistered_keys` 判缺 key → 打 `_SELECTOR_FIX_MARK`「选择器待补」。
- 已完成的相邻工作（本设计**建立其上、不覆盖**）：
  - `d015920`：用例自治三段→两段，删「恢复段」（结尾 navHome/homepageTitle 收尾步）。**进入段仍在。**
  - `5548e85`：待补重生走 `revalidate_for_backfill` 确定性回填，根治「缺 key（未注册）」重生漂移。**未覆盖「注册了但候选坏」。**

### 2.6 串行执行无复位（前置状态污染所在）
- `tools/qalab-runner/runner.mjs`：主循环 `tick()`（`:426`）`for (const item of pending)` 逐条执行（`:430`），claim 后调 `runScript`（`:450`）或 runClaude；**用例之间无任何 reload/reconnect/复位**。
- `gui-core.ensureConnected()`（`:89-96`）：browser/page 已连即 `return`——**复用同一个 page，不重置**。`connect()`（`:200`）= ensureConnected + `waitForContentFrame`（`:105`，等 vm iframe 就绪，不依赖选择器），也不清页面状态。
- 结果：多条用例**共用同一页面连接，前端瞬态在用例间延续**（选中/展开/弹窗/输入残留/焦点/所在子页面）；进入段（d015920 后）只「导航到目标页」，**不清理页面内瞬态**。

## 3. 根因分析

### 3.1 选择器：核心盲区「key 名注册 ≠ key 可用」
`schema`（2.2）、`shared_key_set/_registered_keys`（2.1/2.5）、`_validate_script/revalidate_for_backfill`（2.5）**全链路只认 key 名**，无一处校验候选结构是否有效（含 `by` + `value`/`name`）。

### 3.2 本次 case 的完整因果链（在最新代码下依然全程畅通）
1. 某 project 的 DB 里 `homepageTitle` 候选坏成 `[{}]`（缺 by/value）——来源：前端手动编辑候选 JSON（`SelectorAdmin.vue:442-461` 直接 `JSON.parse` 落库）或历史操作，**schema 没拦**（L1 缺失）。注：前端「探测→加为 key」产出的 `{by,value}` 是完好的（`gui-core.mjs:26-44` genCandidates 每候选带 by+value），非坏值来源。
2. AI 生成用例进入段用 `wait_for(homepageTitle)`；生成校验只看 **key 名注册**（✓），不看候选坏 → 用例被当**可执行 gui script** 入库（L4 盲区）。
3. runner 执行前整体 `setRegistry` 覆盖内置兜底（L1③ 缺 per-key 回落）→ 坏候选盖掉内置好候选（内置 `homepageTitle` = `h1.home-revamp-title__text`，本是好的）。
4. step2 进入段 `wait_for` 定位失败，白等 6s 超时 → 整条 fail。
5. 报告显示普通 fail（L2 缺失）→ 看似「功能失败」，实为选择器/环境问题。

### 3.3 前置状态：用例间无复位 → 脏态污染下一条
`d015920` 去恢复段后，自治只靠「进入段自导航」；但进入段导航到页**不清页面内瞬态**（2.6），执行器又**共用同一 page 不复位**。于是用例 A 结束时的状态 S_A（如某项选中、某面板展开、输入框残留）带入用例 B，而 B 假设从干净初始态开始，其首步操作（如「直接在输入框输入」）因焦点被占/遮挡/状态机不符而失败。**d015920 的「下一条进来会自己导航到位」假设，缺了「进来时在干净主界面」这一前提。**

**结论**：选择器四层缺失（L1/L4/L2）+ 前置状态无复位（L5 缺失）共同致用例执行假失败；`d015920`/`5548e85` 未堵其中任何一条。

## 4. 设计目标与非目标

**目标**：坏选择器①进不了库（L1 源头）、②进了/存量坏也不致命（L1③ runner 兜底）、③不会被当可执行用例放行（L4 校验）、④真挂了能被正确归类为「选择器/环境问题」而非功能 bug（L2）、⑤进入段核心 key 重点保障（L3）；⑥**用例间硬复位、不带脏态进入下一条（L5）**。

**非目标（YAGNI，明确不做）**：
- **共享导航宏**：结构性改动（新 action + prompt + 执行器 + 维护 UI）；L1③+L3 让进入段「坏也不致命」后边际收益递减。
- **URL 直达功能页**：被测应用 iframe SPA + 动态 vm_id，不支持。
- **软复位 / 进入段加清理步**：软复位（ESC/blur/局部还原）不彻底且部分仍依赖选择器；进入段加 AI 清理步是 d015920 已否的老路（脆弱、每条重写）。均被 L5 的 `reload` 硬复位取代。
- **运行时自动探测自愈**：自动匹配误判风险高，不做。

## 5. 分层设计

### L1 · 数据零坏值（数据侧 + 维护侧）——根基
**「有效候选」定义（全项目统一口径）**：一个候选元素有效 ⟺ 含非空 `by`（∈ {testid,role,label,text,placeholder,css}）且含非空 `value`；`name` 仅 `by=="role"` 时可选补充。空数组 `[]` 合法（表示「待补候选」的 key 壳，允许存在）。

- **L1① schema 强校验**（`backend/app/schemas/selector.py`）：给 `SelectorKeyIn`/`SelectorKeyPatch` 的 `candidates` 加 pydantic validator，按上述「有效候选」定义逐元素校验；非法即 422（走统一信封）。`create_key`/`patch_key` 自动受益，`[{}]` 被拒。
- **L1② 存量修复脚本**（新 `backend/scripts/fix_broken_selector_candidates.py`）：扫全库 `selector_key`，`_cands` 解析后挑出含无效元素的 key。对每个坏 key：内置 `selectors.json` 有同名 key 且候选有效 → 生成回填操作；否则列入「需人工补」清单。**默认 dry-run（只打印报告），`--apply` 才写库**。
- **L1③ runner 逐 key 回落内置兜底**（`tools/qalab-runner/gui-mcp/gui-core.mjs`）：加载时保留内置副本 `BUILTIN`（现 `SELECTORS_PATH` 读的那份）。改 `resolveKey`/`isKeyVisible` 组 plan 前，**先按「有效候选」过滤 `entry.candidates`**；若过滤后为空且 `BUILTIN[key]` 有有效候选 → 用 `BUILTIN[key].candidates`。等价地在 `setRegistry` 做 merge（内置为底、DB 有效项覆盖、DB 坏项不覆盖）二选一（实现时取更集中一处）。**立即止损进入段**：homepageTitle 等通用/导航 key 恰是内置兜底里最全最稳的。
  - 内置 `selectors.json` 作为兜底源必须干净，纳入 L1① 同口径的一次性核验。

**测试**：`backend/scripts/test_selector_schema_validation.py`（拒绝 `[{}]`/缺 value/非法 by、放行合法与空数组）；L1② 脚本 dry-run 自测；`gui-core` node 自检（DB 坏候选→回落内置命中）。

### L4 · 候选有效性校验升级（生成侧）——补 5548e85 盲区
- `backend/app/services/selectors.py`：新增 `usable_key_set(db, project_id) -> set[str]`——只返回**候选有效**的 key 名。保留 `shared_key_set`（供仅需 key 名处，如 prompt 清单）。
- `backend/app/services/claude_runner.py`：`_registered_keys`（`:102`）改调 `usable_key_set`（把 `_validate_script`/`revalidate_for_backfill`/`parse_testcases` 的「合法 key 集」全切到此口径）。效果：**候选坏的 key 被当「选择器待补」，不再被当可执行 script 放行**；确定性回填也会因「候选未有效」正确继续待补，直到候选补好。
- 与 L1① 呼应：新库「注册即有效」，L4 主防历史存量 + 双保险；「有效候选」口径单点定义（L1）三处复用（schema/服务/runner），根除口径漂移。

**测试**：扩展 `test_selector_fix_backfill.py`/`test_gen_script_backfill.py`：注册但候选坏的 key → 判「待补」；候选补有效后 → 确定性回填通过。

### L2 · 执行失败分类（执行侧）——新增独立状态 blocked
- `tools/qalab-runner/step-executor.mjs`：`failAt` 增 `fail_kind` 参数。**定位/操作失败**（catch `:110`、`wait_for` 失败、click/fill 找不到/超时）→ `fail_kind="selector"`；**断言不通过**（assert_visible `:98`、assert_text `:105`）→ `fail_kind="business"`。回写 PATCH body 增 `fail_kind`。
- `backend/app/models/exec_queue.py`：`ExecRun` 增 `fail_kind: str|None`；**verdict 新增独立值 `blocked`**（选择器/环境阻塞）——`fail_kind=="selector"` → verdict 记 `blocked`，`business` → `fail`。`checklist_item.exec_status` 同步支持 `blocked`。`backend/app/db/migrate.py` 补 `ensure_exec_run_columns`（ALTER TABLE ADD COLUMN `fail_kind`）；若 `exec_status`/`verdict` 为 DB 枚举则同步加 `blocked` 值；`backend/sql/schema.sql` 同步。
- `backend/app/api/exec_queue.py`：回写端点接收并落 `fail_kind`/`blocked`，同步 `checklist_item.exec_status`。
- 前端 `frontend/src/views/ExecResults.vue`：三态并列（通过 / 失败(真bug) / **选择器阻塞**）；**功能失败率 = fail(business) / (pass + fail(business))，blocked 不计入**；`blocked` 行一键跳「补齐 key」（复用现有 `selector_fix` → SelectorAdmin 桥接）。

**测试**：`step-executor.test.mjs` 加断言（定位失败→selector/blocked；断言失败→business/fail）；后端回写端点 hermetic 测。

### L3 · 进入段健壮（生成侧 + 执行侧）——恢复段已解决，聚焦进入段
- **核心 key 清单**：定义进入/首页/登录类 key 为「核心 key」（navHome/navTasks/homepageTitle/loginSubmit…），落地为一份清单常量（后端 + runner 共识，单点定义）。
- **健康巡检**：复用 `gui-core.verifyKeys`（`:271`）+ `ProbeRequest` verify 模式，支持「默认巡检核心 key 集」并对失效核心 key 告警（SelectorAdmin 已有 verify 入口，扩展默认目标 = 核心 key）。
- **执行侧归类**：进入段的 `wait_for(锚点)`/`click(navX)` 失败天然经 L2 归 `fail_kind=selector`→`blocked`，本层不需额外执行逻辑。
- 靠 L1③ 兜底 + L5 复位后，进入段核心 key「坏也不致命」；本层是重点保障 + 可见性。

**测试**：核心 key 清单单测；verify 巡检对坏核心 key 产出告警的 hermetic 测。

### L5 · 用例间前置复位（执行侧）——解决串行状态污染
- **位置**：`runner.mjs tick()` 逐条循环（`:430`），每条 **gui/e2e** 用例执行前（claim 后、runScript/runClaude 前）调 `guiCore.resetHome()`；api/cli 用例跳过（不碰页面）。
- **`resetHome()`（gui-core 新增对外方法）** = `ensureConnected()` → `page.reload({waitUntil:"domcontentloaded"})` → `waitForContentFrame()`（等 vm iframe 就绪，`:105`，**不依赖选择器**）→ 可选再等 `homepageTitle` 可见（首页真就绪信号，由 L1③ 兜底保障其可靠；失败不阻断，尽力而为）。
- **失败处理**：reload 或就绪等待失败 → 重试 1 次 → 仍失败，该用例记 `blocked`（环境问题，接 L2），不误判功能 fail。
- **掉登录防御**（可选）：reload 后若检测到 `loginModal` 可见 = 会话过期 → 该用例 `blocked` 并提示「执行机需重新登录」，而非一路点导航失败。
- **开关**：`RESET_BETWEEN_CASES` 默认开（可关用于调试）。
- **与 d015920 衔接**：reload 保证每条从初始主界面开始，进入段自导航（「默认已登录主界面」）的假设真正成立——两者配套才完整。

**测试**：gui-core/runner 侧 node 自检（`resetHome` = reload+就绪；每条 gui/e2e 前触发、api/cli 跳过；reload 失败→重试→blocked）。

## 6. 分期与依赖

| 期 | 层 | 交付 | 依赖 |
|---|---|---|---|
| **P0** | L1 + L5复位核心 | L1①schema ②存量脚本 ③runner 回落兜底；**L5：resetHome(reload+就绪) + tick 每条 gui/e2e 前调用** | 无（根基） |
| **P1** | L4 | usable_key_set + 校验链切「候选有效」口径 | L1「有效候选」定义 |
| **P2** | L2 + L5归类 | fail_kind + verdict blocked + migrate + 回写 + 前端三态/统计；**L5：reload 失败/掉登录 → blocked** | 无（L5归类依赖本期 L2） |
| **P3** | L3 | 核心 key 清单 + verify 巡检 | L2（归类）、L1③/L5（兜底+复位） |

*L5 拆两半：复位核心（reload）改动小、独立、高价值，随 P0 早止损状态污染；blocked 归类依赖 L2，随 P2。*
每期结束跑后端 hermetic 回归（现 26/26）+ 相关 node 自检 + `npm run build`。

## 7. 关键决策记录（ADR 摘要）
- **进入只能点导航**：被测应用 iframe SPA + 动态 vm_id，无稳定 URL 直达 → 进入段保留导航 key，靠 L1③/L3/L5 加固而非改导航方式。
- **失败用新独立状态 `blocked`**：不计入功能失败率，独立展示 + 直连补齐入口（用户选定）。
- **用例间用 `reload` 硬复位**（非软复位）：不依赖任何业务选择器、一次清掉全部瞬态；前提「reload 保持登录」已确认，另加掉登录检测防御。
- **L5 并入本 spec（非独立成篇）**：与选择器层同属「用例执行可靠性」，且依赖 L1③（就绪锚点兜底）/L2（blocked 归类）。
- **共享导航宏 / 软复位先不做**（YAGNI）。
- **口径单点化**：「有效候选」「核心 key」各只定义一处，多处复用，根除口径漂移。
- **不覆盖已完成工作**：建立在 `d015920`（去恢复段）、`5548e85`（确定性回填）之上。

## 8. 风险与回滚
- **L1① 过严误伤存量**：先跑 L1② dry-run 摸清坏候选规模，再上 schema 校验；校验只在写入路径生效，老坏数据靠 L1③ 兜底 + L1② 修复。
- **L1③ merge 引入定位行为变化**：内置兜底仅在「DB 候选过滤后为空」时触发，DB 有有效候选时行为不变；node 自检锁定。
- **L2 migrate 老库**：沿用 `migrate.py` ADD COLUMN 幂等模式；verdict 新值 `blocked` 对旧行无影响。
- **L5 reload 保持登录**：已确认前提；另加掉登录检测（reload 后见 loginModal → blocked 提示）兜底会话过期。
- **L5 性能**：每条 gui/e2e 前 reload 增加耗时（重载 SPA + 等就绪），可靠性优先、可接受；`RESET_BETWEEN_CASES` 可关用于调试/加速。
- **runner 需执行机 pull + 重启** 才生效（L1③/L2/L5 改 runner 侧）——分期上线注明。

## 9. 测试范式（沿用项目约定）
无 pytest/eslint/ruff。后端：`backend/scripts/test_*.py`（monkeypatch 免 DB，`python -m scripts.test_xxx`）；前端/runner：`npm run build` + node 自检脚本。每层交付含对应 hermetic 测，纳入现有回归集。
