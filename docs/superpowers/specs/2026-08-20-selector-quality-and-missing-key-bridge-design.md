# 选择器质量治理 + 缺 key→页面元素桥接（设计草案）

> 状态：**已评审 / 已批准（2026-08-20）**（方案 A 通过；三项子决策取推荐默认，见 §10）
> 日期：2026-08-20 ｜ 方向：方案 A（增量、复用现有架构）
> 目标：提升 gui/e2e 用例的生成与执行成功率，解决两个具体痛点。
> 下一步：writing-plans 出实现计划（未开始写代码）。

## 1. 背景与问题（用户原话）

1. **缺 key 定位不到位**：靠页面探测添加选择器后，生成该页用例时仍会「找不到相应 key」，但不知道该补页面上**具体哪个位置**的元素。
2. **探测更新的数据污染疑虑**：探测后出现很多「更新」项，很多只是**文案不同**；全量更新会不会污染现有选择器？（一直没想清楚）

前置约束（已与用户确认）：
- 被测页稳定锚点覆盖**一半一半**（部分元素有 testid/稳定 id，部分只能靠文案/role）。
- **常有在线 runner**，可随时对目标页发起 discover 探测 → P1 桥接可走实时探测。

## 2. 现状架构（已 grep 核对真实代码）

### 2.1 选择器注册表（单一事实源）
- `SelectorKey`（`models/selector.py`）：`(project_id, sub_product, key)` 唯一；一个 key 存一组 `candidates`（TEXT 存 JSON，元素形状 `{by, value, name?}`，by ∈ testid/role/label/text/placeholder/css），另有 `frame`（auto/shell/iframe，`url:<host>` 深层）、`page`（纯分组、不参与定位）、`desc`。
- `services/selectors.py`：`resolved_registry`（共享 `sub_product=''` ∪ 子产品，子产品覆盖同名）、`shared_key_dicts`（按 pages 收窄，供 prompt 注入）、`shared_key_page_map`、`shared_key_set`。DB 是唯一口径，生成/API/runner 都经此层。

### 2.2 探测（discover）闭环
- `ProbeRequest`（设备探测）：平台入队 → runner 拉取认领(pending→running) → 真机跑 discover/verify → 回写 `result`(JSON) + 整页截图。
- discover 结果：`{groups:[{frame,url,elements:[{tag,type,text,candidates,best,rect/absRect}]}], pageSize, 截图}`。
- **runner 侧已有稳定度打分**（`gui-core.mjs::genCandidates`）：testid=100 / id=90 / aria-label=80 / name=75 / placeholder=70 / role+aria=68 / 稳定class(BEM)=60、非BEM=45 / text(2–20字)=40 / tag[type]=30；`candidates=前4按分排序`、`best=最高分`。
- 前端 `SelectorAdmin.vue`：`matchStatus` 按「候选 `by+value` 是否与已有 key 重叠」把探测元素分 **新增/更新/已存在**；截图按 `absRect` 画框；「加为 key」新建或**把 best 追加到候选链头**更新。`toCand()` 存库时**丢弃 score**，只留 `{by,value}`。

### 2.3 生成与「待补」闭环
- `claude_runner.build_testcase_prompt`：只喂「该页 key 清单」，要求「找不到合适 key → 判 manual、别瞎编 selector」。
- `parse_testcases`：校验 script；用了注册表没有的 key → 降级 manual + 打 `[选择器待补] 补齐 key:X,Y` 标记（`_SELECTOR_FIX_MARK`，前后端两处同步）。
- `selector_fix_info` 解析标记 →（是否待补、缺哪些 key、原意图 gui/e2e）。`api/ai.py` 透出 `selector_fix`/`selector_fix_keys`；前端 `CaseLibrary/AITestGen` 显示「补选择器可自动化 · 补:X,Y」，支持 `selector_fix=true` 筛选；待补 manual 用例「保存并重生」时后端按原意图恢复 gui/e2e。

### 2.4 导出（自愈链）
- `playwright_exporter._locator_expr`：一个 key 的多候选按**存库顺序**拼 `cand0.or(cand1).or(cand2)…`，**无 `.first()`**。
- runner `resolveKey`：按存库顺序逐候选 `byToLocator(scope,cand).first()`，首个可见即用——**runner 用了 `.first()`，exporter 没用**（真实镜像不一致）。
- 用户本地未提交改动：新增 `_js_comment()`，把嵌入 `//` 注释的用例文本换行剔除（防存储型注入在开发机 `npx playwright test` 时执行）。本设计**建立在该改动之上，不覆盖**。

## 3. 根因分析

### P2「文案更新会不会污染」——收敛结论
- **有稳定锚点的元素（那一半）**：`best`=testid/id，文案变化不改 best → `matchStatus` 归「已存在」→ 不写库 → **本就无污染**。
- **纯文案/弱锚点元素（另一半）**：copy 变化产生新 `text` best，若某弱候选与旧 key 重叠 → 归「更新」→ **best 被插到候选链头**，于是：
  1. **链路膨胀**：每改一次文案堆积一个 text 候选，无上限。
  2. **strict-mode 真报错（要害）**：exporter 的 `.or()` 无 `.first()` + `getByText` 默认**子串匹配**（`getByText('登录')` 命中「立即登录」）→ 链里两个 text 候选同时命中 → Playwright strict 违例抛错。**全量更新文案候选反而降低成功率。**
  3. **优先级倒置**：最脆的新 text 被插到链头，排在稳定候选之前（runner 与 exporter 都受影响：runner `.first()` 会取 DOM 首个 text 命中，可能是错元素）。
- 附带发现：runner 数值分梯与 `selectors.json` 注释里的口径（testid>role>label>text>placeholder>css）**不一致** → 需要一份权威排序。

### P1「缺 key 不知补哪个元素」
- 系统只给「缺 key 名」（还是模型自起的名），未连到「页面具体元素」。
- 而 discover 元素已自带 `candidates`(稳定优先) + `best` + `rect` + 截图 —— **缺的只是「模型想要的 key」↔「截图上这个元素」的匹配桥**。
- 另有一类：模型遵循 prompt 直接判 manual（未产 script）→ 连缺 key 名都没有，`selector_fix` 也不触发 → 完全不可见。

## 4. 设计（方案 A）

分 5 个可独立理解/测试的组件。**除组件 4 的一处 prompt 文案外，无需改表**（`candidates` JSON 形状不变；排序按 `by` 派生，不持久化 score）。

### 组件 1：候选稳定/脆弱口径（比完整分梯更简单、无歧义）
- 关键前提：存库候选顺序**已反映探测期精确分梯**（`genCandidates` 排序后 `candidates.slice(0,4)` 存入）。因此运行期**不做完整重排**（存库已丢 score，css 无法按 value 可靠细分 `#id` vs class，强行分梯反而引入歧义），只做**二分 + 降级**：
  - **脆弱候选**：`text`（`getByText` 子串匹配 → strict 报错根源）、`role`（`genCandidates` 只产 role+name，copy 依赖）。
  - **稳定候选**：其余（testid / id-css / 一般 css / label / placeholder）。
  - 运行期规则：把脆弱候选**降到链尾**，其余保持既有相对顺序。
- 这个「稳定/脆弱」判定是唯一需要跨端一致的口径，与 gui-core 分梯天然相容（text/role 本就是其最低档）。
- **单一事实源取舍（子问题 1，已定：两处镜像）**：后端出一个 `is_fragile(by)`/排序小工具（放 `services/selectors.py` 或新 `selector_ranking.py`），前端 JS 出等价实现，**文档标注互为镜像**（沿用本仓「镜像+注释」老约定，如 exporter 已注明镜像 gui-core）。**不改 candidates JSON 加 score**（避免动存储与 migrate）。两处（py ↔ js）均以 gui-core 分梯为参照。

### 组件 2：exporter 安全对齐 runner（最高 ROI、最低风险）
- `_locator_expr` 生成的 `.or()` 链**整体加 `.first()`**：`(a.or(b).or(c)).first()`，镜像 runner `resolveKey` 的 `.first()` 语义 → 消除 strict 违例。
- 拼链前**把脆弱候选(text/role)降到链尾**（其余保持相对顺序，见组件 1）→ 稳定候选先试。
- 建立在用户本地 `_js_comment()` 改动之上；同步扩展 `scripts/test_playwright_export.py`（新增：多候选排序、`.first()` 存在、text 候选被排到末尾的断言）。

### 组件 3：合并策略（治理污染源，前端 `SelectorAdmin`）
- `matchStatus` 细化（「稳定候选」= 非 text/role，见组件 1）：
  - **稳定锚点已存在**：元素有稳定候选已在某 key → 归「已存在」，即便文案变了也**不提示更新**。
  - **锚点变更**（值得更新）：元素的**稳定候选**与旧 key 不同/新增 → 「更新」。
  - **仅文案漂移**（cosmetic）：元素稳定候选集与命中 key 相同、仅 text/role 候选不同 → 单独标「文案漂移」，**默认不写库**，可手动展开再决定。
- `submitAddAsKey` 更新合并：
  - 不再无脑「新 best 插链头」，改为**插入后把 text/role 候选降到链尾**（其余保持既有相对顺序）。
- `submitAddAsKey` 更新合并：
  - 不再无脑「新 best 插链头」，改为**插入后把 text/role 候选降到链尾**（其余保持既有相对顺序）。
  - 同 `by`（尤其 text）**就地替换**旧候选而非累加；链**去重 + 上限**（如每 key ≤N 候选，超出丢最不稳的）。
- 与组件 1 的稳定/脆弱口径（JS 版）共用。

### 组件 4：生成 prompt——缺 key 时输出元素描述
- `build_testcase_prompt`：把「找不到 key → 判 manual」改为「找不到 key → 仍产 script，用一个**语义化新 key 名**，并在该步/用例里给出**元素人话描述**（是什么、在哪、文案/role）」。
- 效果：`parse_testcases` 走既有 `_unregistered_keys` → `[选择器待补]` 路径，标记从「缺名字」升级为「缺名字 + 要哪个元素」，让 P1 匹配更准；把「静默 manual」这类不可见缺口也拉回可见的待补。
- 仅改 prompt 文案 + 可能在 `_SELECTOR_FIX_MARK` 里附带 desc（若附带则前后端两处同步，遵循既有约定）。

### 组件 5：P1 缺 key→页面元素桥接（实时探测）
数据流：
1. 待补用例行（`CaseLibrary`，已有 `selector_fix_keys`）新增「**定位这些 key**」入口。
2. 点击 → 对该用例 `page` 所属 runner 发起 discover（复用 `startProbe`/`getProbe`/轮询）。
3. 探测返回后，对每个缺 key，用「key 名 + desc（组件 4）+ 该步上下文」对 discover 元素**语义打分排序**（名称/文案/role 相似度）。
4. 前端在截图上**高亮 Top 匹配框**（复用 `absRect` 叠框）。
5. **子问题 2（已定：人点选）**：**只高亮候选、由人点选确认**（不自动选中 Top1 直接落库）——匹配是启发式，人确认一次成本低、可避免误登记污染注册表；确认后按组件 1/3 的稳定优先登记 key。
6. 登记后可一键「保存并重生」把待补 manual 恢复为 gui/e2e（既有能力）。

## 5. 数据模型影响
- **无需改表**：`selector_key.candidates` JSON 形状不变；排序按 `by` 派生。
- discover `result` 已含所需字段（candidates/best/rect/截图），无新增。
- 若组件 4 决定把「元素 desc」写进 `_SELECTOR_FIX_MARK` 文本 → 仅解析格式变化，无表变化。

## 6. 一致性约束（改动须同步处）
- **稳定/脆弱口径两处**：后端 `is_fragile`（exporter 导出用）↔ 前端等价实现（合并用），均以 `gui-core.mjs::genCandidates` 分梯为参照（弱 = text/role）。任一改动，另一处 + 文档同步。
- **`_SELECTOR_FIX_MARK` 格式**：`parse_testcases` 写 ↔ `selector_fix_info` 读（既有两处约定）；若组件 4 附带 desc 再加前端渲染。
- **两份 schema**：本设计不改表，故 `models/` 与 `sql/schema.sql` 无需动；如后续加列须两处 + `migrate.py` 同步（本仓既有模式）。

## 7. 错误处理与边界
- exporter 加 `.first()`：list 类多命中元素将取 DOM 首个——对「单元素语义 key」是预期；文档注明。
- discover 无在线 runner / 超时：P1 桥接回落提示「稍后重试或去选择器管理手动补」，不阻塞用例。
- 语义匹配无高分候选：不硬塞，提示人工在截图上自行框选（复用现有「加为 key」）。
- 合并链上限裁剪：只裁最不稳候选，保底保留 1 条最稳。

## 8. 测试策略（本仓无测试框架，沿用既有风格）
- 后端：仿 `test_page_hook.py` 写 hermetic 脚本，覆盖：`by_rank` 排序、exporter `.first()` + 稳定优先、`parse_testcases` 缺 key desc 透出。
- exporter：扩展用户在途的 `scripts/test_playwright_export.py`。
- 前端：`npm run build` 通过 + 手动端到端（待补用例 → 定位 key → 截图框选 → 登记 → 重生）。

## 9. 明确不做（YAGNI / 排除方案 B、C）
- 不做**自动应用**锚点更新 / 后台定时 verify 自愈（方案 B）——自动写库正是污染最大来源，与用户顾虑冲突。
- 不做 P1 的**自动选中 Top1 直接落库**（保留人确认）。
- 不为候选持久化 score / 加候选元数据列（排序按 `by` 派生足够）。
- 不做纯 prompt 规避而不建桥（方案 C 不解决 P1）。

## 10. 决策记录（已批准 2026-08-20）
1. 稳定/脆弱口径：**两处镜像（后端 py ↔ 前端 js，均参照 gui-core 分梯）+ 文档标注**。✅
2. P1 匹配：**高亮候选、由人点选确认**（不自动选中 Top1）。✅
3. 组件 4：**把「元素 desc」写进 `_SELECTOR_FIX_MARK`**（前后端解析格式随之同步）。✅
4. 整体按**方案 A** 推进。✅
