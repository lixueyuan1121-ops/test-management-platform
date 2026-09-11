# 录制生成 e2e 脚本 —— 设计文档（v1 / MVP）

> 状态：已评审（2026-09-10），待转实现计划。
> 目标能力：功能测试模块「Req4 · 手动录制生成脚本」。补齐平台唯一实质缺失的能力——
> 在真实被测客户端里手动操作 → 自动生成可执行 e2e script + 顺带回填选择器。

## 1. 背景与目标

平台现有的选择器「自动获取回填」已完整（设备探测 + 加为 key + 执行期自愈回写 + 扫描分支导入），
但缺少「**录制操作序列 → 脚本**」：目前 script 只能由 AI 从需求/steps 生成，或人工手写/重生。

**本设计目标（MVP）**：
- 在真实被测客户端（Namiwork Electron）里手动点击/输入/切页，runner 经 CDP 实时捕获每一步。
- 录制中可随时 **Alt+点击** 把某元素标为断言。
- 停止后前端展示已录步骤，可复审/微调，一键「保存为用例」。
- 保存时：每步元素的候选先与已注册选择器库比对，命中复用、未命中新建 key 并**回填**（多候选 testid+css+xpath）；
  组装成 `exec_kind=e2e` 的 test_case，过校验后即可执行 / 标回归。

**成功标准**：QA 手动走一遍业务流 → 得到一条**当场可执行**的 e2e 用例，且用到的选择器都进了库（无「选择器待补」）。

## 2. 非目标（YAGNI，留给 v2）

- 录完自动回放校验一遍（v1 靠"保存后手动跑一次回归"验证）。
- 拖拽、纯 hover 浮层、深层嵌套 iframe 录制等边角交互。
- 分支/循环/参数化/数据驱动。
- 跨用例的公共前置片段抽取。

## 3. 复用的现有底座（不重造）

| 复用 | 来源 |
|---|---|
| CDP 连接 + testid 注入 + `addInitScript` | `gui-mcp/gui-core.mjs`（`ensureConnected` / `injectTestIdMode`） |
| 元素候选生成 `genCandidates`（testid>id>label>name>placeholder>class>text） | `gui-mcp/gui-core.mjs` 的 `DISCOVER_SCRIPT` |
| runner 轮询循环 + 设备 token 鉴权（`require_runner_ctx`） | `runner.mjs`（`handleProbes` 同款）、`core/deps.py` |
| 选择器回填（多候选、去重、testid>xpath>css 排序、四段式 desc） | `services/heal_selectors.apply_heal_items` |
| 候选 by/value 有效性、排序 | `services/selector_ranking`（三处镜像） |
| script DSL 校验 | `services/claude_runner._validate_script` |
| script 执行 | `step-executor.mjs`（保存后走回归即用） |

## 4. 架构总览

```
前端「录制」面板 ──start/poll/stop/save──▶ 后端 /api/record*  ◀──poll/append── runner(handleRecordings)
   选设备·实时步骤·标断言·保存                  record_session 表           gui-core.startRecording:
        │                                                                    注入 record-capture.mjs
        └─保存为用例─▶ assemble_recording():每步候选→key(命中/新建+回填) + 建 test_case(e2e)
```

录制是**有状态的交互会话**（开始→操作数分钟→停止），故新增 `record_session` 表承载会话与事件缓冲。

## 5. 数据模型：`record_session`

```
record_session
  id            BIGINT PK
  project_id    INT   (FK project)
  sub_product   VARCHAR(32) default ''
  runner        VARCHAR(64)          -- 目标执行机 runner_id
  status        VARCHAR(16)          -- pending / recording / stopped / done / failed
  events        TEXT (JSON)          -- 已捕获步骤数组（runner 增量 append）
  error         TEXT NULL
  created_by    INT
  created_at    DATETIME
  updated_at    DATETIME
```

- `events` 为 JSON 字符串（兼容 MySQL 无 JSON；同 probe_request 的 result），可能较大 → 列用 LONGTEXT（MySQL）。
- 状态机：`pending`（平台建）→ runner claim → `recording`（注入捕获）→ 前端 stop → `stopped` → 保存为用例后 `done`；异常 `failed`。
- 迁移走 `db/migrate.py::ensure_record_session_table`（`checkfirst` create），并同步 `sql/schema.sql`。

### 事件（events 里每一项）

```json
{ "seq": 3, "action": "click|fill|assert",
  "tag": "button", "type": "", "text": "在线预览", "value": "",
  "candidates": [{"by":"testid","value":"office-online-preview"}, {"by":"css","value":".xxx"}],
  "frame": "vm",                // shell/vm/url:<host>
  "assert": { "kind": "visible|text", "expected": "在线预览" }  // 仅 action=assert
}
```

## 6. 捕获机制（runner 侧）

### 6.1 `record-capture.mjs`（新，纯前端注入脚本 + 映射纯函数）
- **注入脚本**（`addInitScript` 字符串，穿 open shadow / 各 frame）：
  - 捕获阶段挂 `click` / `input` / `change`。
  - 每次事件：内联 `genCandidates(target)`（与 DISCOVER_SCRIPT 同款），把
    `{action, tag, type, text, value, candidates, assert?}` push 进页面全局 `window.__qalabRec`（数组）。
  - **断言标记**：事件带 `altKey` → `action=assert`；元素有稳定短文本（2–20 字）则 `assert.kind=text, expected=文本`，否则 `assert.kind=visible`。普通点击=`click`；`input/change` 取最终 `value`=`fill`。
  - 去抖：同一元素连续 input 只留最后一次（seq 覆盖）；点击后紧跟的合成事件去重。
- **映射纯函数** `rawEventToStep(raw, seq)`：把页面回传的原始事件规整成 §5 的步骤对象（**可单测**）。

### 6.2 `gui-core.mjs` 新增
- `startRecording()`：`ensureConnected` → `injectTestIdMode`（保证元素带 testid）→ `addInitScript(CAPTURE)` → 对现有各 frame 立即注入一次（错过 init 的当前页）→ 置 `recording=true`。
- `drainRecordEvents()`：对 `page.frames()` 逐帧 `evaluate` 取出并清空 `window.__qalabRec`，带上 frame 标签（shell/vm/url:host）返回。
- `stopRecording()`：移除监听（页面内置 flag 关闭）/ 置 `recording=false`。（addInitScript 无法撤销，捕获脚本据 `window.__qalabRecOn` 开关空转即可。）

### 6.3 `runner.mjs` 新增 `handleRecordings()`（与 `handleProbes` 并列）
- 每轮 `GET /api/record/pending?runner=` 拉本机 `recording` 会话；
- 首次见到 → `startRecording()`；随后每轮 `drainRecordEvents()` → 有新事件则 `POST /record/{id}/events`（增量 append，seq 续接）；
- 会话变 `stopped` → `stopRecording()`。

## 7. 后端端点（`api/record.py`）

| 端点 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/record` | 用户 | 建录制会话(project_id, runner) → `pending` |
| `GET /api/record/pending?runner=` | runner token | runner 拉本机待录/录制中会话（claim 置 recording） |
| `POST /api/record/{id}/events` | runner token（归属校验） | 增量 append 捕获事件到 `events` |
| `GET /api/record/{id}` | 用户 | 前端轮询：status + 已录步骤（实时展示） |
| `POST /api/record/{id}/stop` | 用户 | 置 `stopped`（runner 下轮撤捕获） |
| `POST /api/record/{id}/save-as-case` | 用户 | 组装 script + 回填选择器 + 建 test_case，会话置 `done` |
| `DELETE /api/record/{id}` | 用户 | 丢弃会话 |

- 沿用统一信封 `ok()/fail()`；runner 侧鉴权走 `require_runner_ctx`（归属校验防串扰，同 probe）。

## 8. 保存为用例：`assemble_recording()`（`services/recording.py`，纯逻辑可单测）

输入：`record_session.events`（可能经前端复审后传回的编辑版）+ `project_id` + `title` + 目标作用域。
逐步：
1. **选择器决策**：该步 `candidates` 与**项目级共享**注册表比对（复用 `candKey` = by+value 重叠口径）：
   - 命中已有 key → `target.key = 该 key`。
   - 未命中 → 生成新 key 名（优先 testid 值转 camelCase，退元素文本/tag+序号），desc 走四段式（页面/场景/控件类型），
     并收集为待回填项。
2. **组装步骤**：`connect` 起手；每步 → `{action, target:{key}, args, desc}`：
   - `click` → `click`；`fill` → `fill args.text=value`；`assert` → `assert_visible` 或 `assert_text args.expected`。
3. **回填**：把所有"新建 key"经 `apply_heal_items`（多候选 testid+css+xpath、去重、排序、四段式 desc）写入库。
4. **建用例**：`test_case(exec_kind=e2e, script=组装结果, page=涉及页面, review_status=pending)`；
   过 `_validate_script`（此时新 key 已回填 → 应全部可解析）。返回 `_to_case_out`。

> 与执行期自愈的回填**同一函数**（`apply_heal_items`），保证录制与自愈落库口径一致。

## 9. 前端「录制」面板

独立小页 `views/Recorder.vue`（与 SelectorAdmin 隔离清晰；菜单/路由挂平台管理员或项目成员可见），复用设备下拉：
- 选在线设备 → **开始录制**（`POST /record`）→ 轮询 `GET /record/{id}` 实时渲染步骤列表（第 n 步 · 动作 · `data-testid=…` · 断言标记）。
- 顶部提示：「在客户端里正常操作；**Alt+点击**任意元素=标断言」。
- **停止** → 步骤进入**复审态**：可删步、改断言 kind、改标题/页面/前置条件。
- **保存为用例** → `save-as-case` → 跳转用例库高亮该新 e2e 用例。
- data-testid 展示复用 `candLabel`。

## 10. 拆分与测试

| 单元 | 测试 |
|---|---|
| `rawEventToStep`（runner 纯函数） | node --test：click/fill/alt-assert/去抖 映射 |
| `drainRecordEvents`（gui-core） | mock `page.frames()/evaluate` 返回缓冲 → 断言排空+frame 标 |
| `assemble_recording`（后端纯逻辑） | 候选命中已有 key / 未命中新建+回填 / 断言步 / 组装校验通过 |
| `/api/record*` 四端点 | TestClient：建→append→get→stop→save-as-case→用例落库+选择器回填 |
| 迁移 | 模拟老库补表幂等 |

## 11. 改动清单

- **后端**：`models/record_session.py`、`api/record.py`、`services/recording.py`、`schemas/record.py`、`db/migrate.py`（+ `main.py` 注册）、`sql/schema.sql`、`api/router.py`。
- **runner（deployed `qalab-runner-bendi` + 仓库 `tools/qalab-runner` 两份同步）**：新增 `record-capture.mjs`（+test）、`gui-core.mjs` 加 start/stop/drainRecording、`runner.mjs` 加 `handleRecordings`。
- **前端**：`views/Recorder.vue`（或 SelectorAdmin 内嵌）、`api/index.js`、路由/菜单。

## 12. 风险与缓解

- **exposeBinding 时序坑** → 改用「页面全局缓冲 + runner 轮询 evaluate 排空」，跨 frame 稳、无时序依赖（与探测同款）。
- **addInitScript 撤不掉** → 捕获脚本据 `window.__qalabRecOn` 开关空转，stop 时置 false。
- **同一操作重复事件（click 触发合成 input 等）** → 去抖（同元素同 seq 覆盖 + 时间窗去重）。
- **元素无 testid（开关没开）** → 依赖 `injectTestIdMode` 已在连接时注入；仍无 testid 的自定义控件退 css/xpath（多候选），与现有口径一致。
- **runner 两份分叉** → 严格 deployed+repo 同步（见既有备忘）。

## 13. 已定决策（原开放问题）

- 录制面板：**独立小页 `views/Recorder.vue`**（不内嵌 SelectorAdmin，隔离清晰）。
- `events` 列：MySQL 用 **LONGTEXT**（沿用 probe result 的 LONGTEXT 迁移先例），SQLite 走 TEXT。
- 保存为用例默认 `exec_kind=e2e`、`review_status=pending`、作用域「项目级共享」（生成/校验只读共享域，见既有约定）。
