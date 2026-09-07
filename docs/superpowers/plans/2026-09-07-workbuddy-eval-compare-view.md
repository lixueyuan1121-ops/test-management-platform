# WorkBuddy 对话测评 · 跨产品对比视图 Implementation Plan（系列 3/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 判定层零改动复用（确认 WorkBuddy run 能被同一套 rubric 判定），后端统计加"按被测产品(target_engine)分组"，前端 EvalResults 加"产品并排对比 + 产品筛选/分组"，综合评价 prompt 带横评口径——最终产出 WorkBuddy vs 纳米Work 的对比结果。

**Architecture:** 判定链路（`eval_judge.judge_run`）消费的是执行器规整后的统一 trace JSON + expected，不感知 target_engine，故一行不改即可判 WorkBuddy run（Plan 1 已保证 WorkBuddy trace 同结构）。统计层仿现有 `judge-quality` 的 `by_engine` 分组模式，给 `dimension-stats`/`trend`/`_result_summary` 加 engine 维度。前端仿现有 A/B `openAbCompare` 并排弹窗，把配对轴从 `compare_group` 换成 `target_engine`。

**Tech Stack:** FastAPI + SQLAlchemy 现算聚合；Vue3 + ElementPlus + ECharts；统一 `{code,msg,data}` 信封 + api/index.js 薄封装（返回已解包 data）。

**Spec:** `docs/superpowers/specs/2026-09-07-eval-multi-product-workbuddy-design.md`（§7 判定零改动、§8 对比展示）

**依赖:** Plan 1（WorkBuddy 执行器产出同结构 trace）+ Plan 2（多产品 fan-out 使同批含多 engine 的 run）。本 plan 消费它们的产物。

## Global Constraints

- **判定层零改动**:不修改 `eval_judge.judge_run`/`_judge_once`/rubric prompt 常量。WorkBuddy run 走同一判定，前提是 trace 同结构（Plan 1 保证）。本 plan 只加"统计/展示"的分组维度。
- **响应信封 + 薄封装**:后端 `ok(data)`；前端 `api/index.js` 函数返回已解包 data。
- **不建统计表**:全部现算聚合，按 `EvalRun.target_engine`（顶层字段，非解 payload）分组。
- **对比轴**:跨产品对比用 `EvalRun.target_engine`（比 A/B 的 `payload.compare_group` 更好取，SQL 直接 group by）。A/B 与产品是正交两维，前端两个对比入口并存。
- **前端构建**:改完 `npm run build`（普通 build，别 build:clean，见项目记忆 frontend-dist-in-git）；dist 要提交（服务器无 Node）。
- **公平性口径**:报告/对比 UI 标注各产品实际用的模型（trace.model，来自 WorkBuddy footer / 纳米对应字段）。

---

## 文件结构

| 文件 | 责任 | 创建/修改 |
|---|---|---|
| `backend/app/api/eval_judge.py` | `dimension-stats` 加 `group_by=engine` 选项（返回 by_engine 分维通过率） | 修改 |
| `backend/app/api/eval_queue.py` | `trend` 加按 target_engine 拆线 | 修改 |
| `backend/app/services/eval_pipeline.py` | `_result_summary` 加 `engine_line`（各产品通过率/均分）；综合评价拼接带横评 | 修改 |
| `backend/app/services/claude_runner.py` | `build_eval_task_summary_prompt` 的 items 加 target_engine + prompt 增补横评口径 | 修改 |
| `frontend/src/api/index.js` | `listEvalEngines`；`evalDimensionStats`/`evalBatchTrend` 加 engine 参数 | 修改 |
| `frontend/src/views/EvalResults.vue` | 产品并排对比弹窗（仿 openAbCompare）+ 产品筛选/分组列 + dimStats 按产品渲染 | 修改 |
| `backend/scripts/test_engine_stats.py` | 后端统计分组验证 | 创建 |

---

## Task 1: 判定零改动验证（WorkBuddy trace 能被同一 rubric 判定）

**Files:**
- Test: `backend/scripts/test_judge_workbuddy_trace.py`（不改判定代码，只验证复用性）

**Interfaces:**
- Consumes: `eval_judge.judge_run`（现有，不改）、一段 WorkBuddy 结构的样例 trace。

- [ ] **Step 1: 构造 WorkBuddy 样例 trace，喂判定，断言不报错且产出三维**

```python
# backend/scripts/test_judge_workbuddy_trace.py
# 目的：证明判定层对 WorkBuddy 结构的 trace 零改动可用（trace 同结构、target_engine=workbuddy 不影响判定）。
# 构造一条 target_engine='workbuddy' 的 EvalRun + 磁盘 trace（WorkBuddy DomTrace 形状），调 judge_run，
# 断言返回含 verdict + verdict_dims 三维，不因 engine 不同报错。
import json, os
from app.db.session import SessionLocal
from app.models.ai_eval import EvalRun, EvalQuery
from app.services.eval_judge import judge_run
# WorkBuddy 结构 trace（与 ws-trace buildTrace 同顶层字段 + Plan1 的 dom_captured/model）
sample_trace = {
    "session_id": None, "run_id": 1, "thinking": "先检索天气再汇总",
    "tool_calls": [{"tool_call_id": "_dom_sources", "name": "web_search", "original_tool_name": "",
                    "is_mcp": False, "mcp_server": None, "args": None,
                    "result_text": "引用来源 (3)\n中国天气网\n...", "reached_result": True}],
    "artifacts": [], "answer": "北京今天阴转阵雨，最高28℃。",
    "ws_captured": False, "ws_connected": True, "dom_captured": True,
    "reported_duration": "17", "bean_cost": "2.43", "model": "均衡 (MiniMax-M3)",
}
# ... 构造 EvalQuery(expected="给出天气") + EvalRun(target_engine='workbuddy', trace=写盘URL) ...
# 断言：res = judge_run(db, run, provider=None, votes=1); assert res.get('verdict') in ('pass','fail','error')
# 若引擎不可用(无 claude/deepseek 配置) → verdict='error' 也算"判定链路跑通不报错"
print("PASS: 判定层对 WorkBuddy trace 零改动可用（实现时补全构造）")
```

> **实现注意**:judge_run 会真实调 LLM（claude/deepseek）。若测试环境无引擎配置，judge_run 对空引擎返回 `verdict=error`（见 eval_judge.py 无引擎分支）——这仍证明"链路不因 engine=workbuddy 报错"。断言放宽为 `verdict in (pass,fail,error)`。构造 trace 落盘路径参照 `judge_run` 读 trace 的方式（`run.trace` 存 URL/路径）。

- [ ] **Step 2: 跑验证**

Run: `cd backend && .venv/bin/python -m scripts.test_judge_workbuddy_trace`
Expected: PASS —— judge_run 对 workbuddy run 返回合法 verdict，不抛"未知 engine"类异常。

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/test_judge_workbuddy_trace.py
git commit -m "test(eval): 验证判定层对 WorkBuddy trace 零改动复用"
```

---

## Task 2: dimension-stats 加按产品分组

**Files:**
- Modify: `backend/app/api/eval_judge.py`（`eval_dimension_stats` 加 `group_by` / `by_engine`）
- Test: `backend/scripts/test_engine_stats.py`

**Interfaces:**
- Produces: `GET /eval-judge/dimension-stats?...&by_engine=true` → 在原返回基础上加 `by_engine: [{engine, label, dims:[...], overall_rate, judged_total}]`。默认（不传）保持原返回结构不变（向后兼容前端旧调用）。

- [ ] **Step 1: 加 by_engine 分组（仿 judge-quality 的 by_engine 模式）**

`backend/app/api/eval_judge.py` `eval_dimension_stats`（L230）末尾，在原 `agg` 聚合基础上加按 `EvalRun.target_engine` 二次分组。抽出 `_agg_dims(*filters)` 复用主查询逻辑:

```python
    # 原返回（保持不变）
    result = {"dims": dims, "overall_rate": overall_rate, "judged_total": judged_total}

    # by_engine：按 target_engine 分组各算一份分维通过率（仿 judge-quality 的 by_engine）
    if by_engine:
        from app.services.eval_engines import EVAL_ENGINES
        engines = [e for (e,) in db.query(EvalRun.target_engine)
                   .filter(EvalRun.project_id == project_id, EvalRun.verdict.in_(["pass", "fail"]),
                           EvalRun.target_engine.isnot(None),
                           func.date(EvalRun.created_at) >= d_from, func.date(EvalRun.created_at) <= today)
                   .distinct().all()]
        eng_out = []
        for e in engines:
            e_rows = (db.query(EvalQuery.dimension, EvalRun.verdict, func.count(EvalRun.id))
                      .join(EvalQuery, EvalQuery.id == EvalRun.eval_query_id)
                      .filter(EvalRun.project_id == project_id, EvalRun.verdict.in_(["pass", "fail"]),
                              EvalRun.target_engine == e,
                              func.date(EvalRun.created_at) >= d_from, func.date(EvalRun.created_at) <= today)
                      .group_by(EvalQuery.dimension, EvalRun.verdict).all())
            e_agg = {}
            for dim, verdict, cnt in e_rows:
                b = e_agg.setdefault(dim or "未标注", {"total": 0, "passed": 0})
                b["total"] += cnt
                if verdict == "pass": b["passed"] += cnt
            e_dims = sorted(({"dimension": k, "total": v["total"], "passed": v["passed"],
                              "rate": round(v["passed"]/v["total"]*100, 1) if v["total"] else 0}
                             for k, v in e_agg.items()), key=lambda x: -x["total"])
            e_total = sum(v["total"] for v in e_agg.values())
            e_pass = sum(v["passed"] for v in e_agg.values())
            eng_out.append({"engine": e, "label": EVAL_ENGINES.get(e, {}).get("label", e),
                            "dims": e_dims, "judged_total": e_total,
                            "overall_rate": round(e_pass/e_total*100, 1) if e_total else 0})
        result["by_engine"] = eng_out
    return ok(result)
```

端点签名加 `by_engine: bool = False`。

- [ ] **Step 2: 写测试（构造两引擎已判定 run，断言 by_engine 拆分）**

```python
# backend/scripts/test_engine_stats.py
# 构造同项目下 namiwork/workbuddy 各若干条已判定 run（verdict+dimension），调 eval_dimension_stats(by_engine=True)，
# 断言 by_engine 有两组、各组 overall_rate 与该引擎数据吻合。测试末 rollback。
# （构造逻辑按 backend/scripts 现有 eval 脚本样板补全）
print("PASS: dimension-stats by_engine（实现时补全构造+断言）")
```

- [ ] **Step 3: 跑测试 + Commit**

Run: `cd backend && .venv/bin/python -m scripts.test_engine_stats`

```bash
git add backend/app/api/eval_judge.py backend/scripts/test_engine_stats.py
git commit -m "feat(eval): dimension-stats 支持按被测产品(by_engine)分组"
```

---

## Task 3: trend 按产品拆线 + _result_summary 加 engine_line

**Files:**
- Modify: `backend/app/api/eval_queue.py`（`batch_trend`）、`backend/app/services/eval_pipeline.py`（`_result_summary`）

**Interfaces:**
- Produces:
  - `trend` 返回每批次加 `by_engine: {namiwork: {pass_rate, avg_score, judged}, workbuddy: {...}}`（同批多产品时拆分）。
  - `_result_summary` 返回加 `engine_line`（仿 ab_line）——`"纳米Work 通过率 80%（4/5）｜WorkBuddy 通过率 60%（3/5）"`。

- [ ] **Step 1: _result_summary 加 engine_line（仿 ab_line，轴换 target_engine）**

`backend/app/services/eval_pipeline.py` `_result_summary`（L157）在 ab 统计旁加 engine 统计（用 `r.target_engine` 顶层字段，不解 payload）:

```python
    # 跨产品胜率:按 target_engine 统计各产品 pass 率（顶层字段，正交于 A/B）
    from app.services.eval_engines import EVAL_ENGINES
    eng_stat = {}   # engine -> [pass, total]
    for r in rows:
        e = r.target_engine
        if not e: continue
        eng_stat.setdefault(e, [0, 0])
        eng_stat[e][1] += 1
        if r.verdict == "pass": eng_stat[e][0] += 1
    engine_line = None
    if len(eng_stat) >= 2:   # 至少两个产品才有对比意义
        def _rate(x): return f"{round(100*x[0]/x[1])}%" if x[1] else "—"
        engine_line = "｜".join(
            f"{EVAL_ENGINES.get(e,{}).get('label',e)} 通过率 {_rate(v)}（{v[0]}/{v[1]}）"
            for e, v in sorted(eng_stat.items()))
    # 返回 dict 加 "engine_line": engine_line
```

在返回 dict（L191-192）加 `"engine_line": engine_line`。

- [ ] **Step 2: 综合评价拼接带 engine_line**

`eval_pipeline.py` L405 附近（`if s["ab_line"]:` 旁）加:

```python
        if s.get("engine_line"):
            lines.append(f"**产品对比**:{s['engine_line']}")
```

- [ ] **Step 3: trend 加 by_engine 拆线**

`backend/app/api/eval_queue.py` `batch_trend`（L331）在主聚合后，追加按 `(batch_id, target_engine)` 的二次聚合，塞进每批 `by_engine`。查询 group by 加 `EvalRun.target_engine`:

```python
    # 每批各产品拆线（同批多产品对比）
    eng_rows = (db.query(EvalRun.batch_id, EvalRun.target_engine,
                    func.count(EvalRun.id).label("total"),
                    func.sum(case((EvalRun.verdict == "pass", 1), else_=0)).label("passed"),
                    func.sum(case((EvalRun.verdict == "fail", 1), else_=0)).label("failed"),
                    func.avg(EvalRun.score).label("avg_score"))
                .filter(EvalRun.project_id == project_id, EvalRun.batch_id.isnot(None),
                        EvalRun.target_engine.isnot(None))
                .group_by(EvalRun.batch_id, EvalRun.target_engine).all())
    eng_map = {}   # batch_id -> {engine: {...}}
    for er in eng_rows:
        judged = int(er.passed or 0) + int(er.failed or 0)
        eng_map.setdefault(er.batch_id, {})[er.target_engine] = {
            "pass_rate": round(int(er.passed or 0)/judged*100, 1) if judged else None,
            "avg_score": round(float(er.avg_score), 2) if er.avg_score is not None else None,
            "judged": judged}
    # 在 out.append(...) 的每项加 "by_engine": eng_map.get(r.batch_id, {})
```

- [ ] **Step 4: 验证（并入 test_engine_stats 或核对）**

Run: `cd backend && .venv/bin/python -m scripts.test_engine_stats`（扩展断言 _result_summary 的 engine_line、trend 的 by_engine）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/eval_queue.py backend/app/services/eval_pipeline.py
git commit -m "feat(eval): trend 按产品拆线 + _result_summary 加 engine_line 跨产品胜率"
```

---

## Task 4: 综合评价 prompt 带产品维度

**Files:**
- Modify: `backend/app/services/claude_runner.py`（`build_eval_task_summary_prompt`）
- Modify: `backend/app/services/eval_pipeline.py`（拼 items 时带 target_engine）

**Interfaces:**
- Consumes: 各 run 的 `target_engine`。
- Produces: 综合评价 HTML 报告在多产品批次时包含横向对比章节。

- [ ] **Step 1: items 每条带 target_engine + engine label**

找到 `build_eval_task_summary_prompt` 的调用处（拼 items 的地方，`eval_pipeline.py` 或 `eval_task.py` summarize），每条 item 加 `"engine": EVAL_ENGINES[run.target_engine]["label"]`。

- [ ] **Step 2: prompt 增补横评口径**

`backend/app/services/claude_runner.py` `build_eval_task_summary_prompt`（L1315）:①每条 item 行加 `| 产品:{engine}`；②输出要求加一条:

```python
    # item 行加产品
    f"- 维度:{dim} | 产品:{it.get('engine') or '—'} | 执行:{it.get('status') or ''} | 判定:{verdict}"
```

输出要求（L1349 结构建议）加:

```
   <h2>产品横向对比</h2> 若素材含多个「产品」，用 <table> 按产品汇总(产品/用例数/通过率/均分/优劣势)，
   并给出哪个产品在本任务表现更好的明确结论(注明各产品实际用的模型，对比的是产品整体表现)。
```

- [ ] **Step 3: 验证 prompt 构造**

```python
# 快速核对：构造含两 engine 的 items，调 build_eval_task_summary_prompt，断言 prompt 文本含"产品横向对比"和两个产品 label
cd backend && .venv/bin/python -c "
from app.services.claude_runner import build_eval_task_summary_prompt
items=[{'title':'t1','engine':'纳米Work','verdict':'pass','status':'done'},{'title':'t2','engine':'WorkBuddy','verdict':'fail','status':'done'}]
p=build_eval_task_summary_prompt('对比任务','',items)
assert '产品横向对比' in p and '纳米Work' in p and 'WorkBuddy' in p, 'prompt 缺横评口径'
print('PASS: summary prompt 带产品横评')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/claude_runner.py backend/app/services/eval_pipeline.py
git commit -m "feat(eval): 综合评价 prompt 带产品横向对比章节"
```

---

## Task 5: 前端 api/index.js 加接口参数

**Files:**
- Modify: `frontend/src/api/index.js`

**Interfaces:**
- Produces:
  - `listEvalEngines()` → GET /ai/eval-engines
  - `evalDimensionStats(project_id, days, byEngine)` 加 by_engine 参数
  - `createEvalTask`/`updateEvalTask` payload 支持 target_engines（已是透传 payload，无需改函数签名，仅注释）

- [ ] **Step 1: 加 listEvalEngines + 扩展 dimensionStats**

`frontend/src/api/index.js`:

```js
// 被测产品(引擎)列表：任务编辑时勾选测哪些产品
export const listEvalEngines = () => http.get('/ai/eval-engines')
```

改 `evalDimensionStats`（L291）:

```js
export const evalDimensionStats = (project_id, days = 30, byEngine = false) =>
  http.get('/eval-judge/dimension-stats', { params: { project_id, days, ...(byEngine ? { by_engine: true } : {}) } })
```

- [ ] **Step 2: 验证 build 通过**

Run: `cd frontend && npm run build`
Expected: 构建成功（api/index.js 语法正确）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/index.js
git commit -m "feat(eval): 前端 api 加 listEvalEngines + dimensionStats by_engine 参数"
```

---

## Task 6: 前端 EvalResults 产品并排对比 + 筛选

**Files:**
- Modify: `frontend/src/views/EvalResults.vue`

**Interfaces:**
- Consumes: `listEvalEngines`、`evalDimensionStats(...,byEngine)`、run 行的 `target_engine`（`_to_out` 已回显，L110）、trend 的 by_engine。

- [ ] **Step 1: 结果列表加"产品"列 + 产品筛选**

在结果表加一列显示 `row.target_engine`（用 label 映射 `ENGINE_LABEL = { namiwork:'纳米Work', workbuddy:'WorkBuddy' }`），加 `engineFilter` ref + 筛选下拉（仿现有 `verdictFilter`/`batchFilter`），并入 `matchFilter`（L383）。

- [ ] **Step 2: 产品并排对比弹窗（仿 openAbCompare，轴换 target_engine）**

新增 `openEngineCompare(row)`——按 `eval_query_id + batch_id` 配对同题不同产品的 run（仿 `openAbCompare` L400-413，但配对键用 `target_engine` 而非 `compare_group`）:

```js
function openEngineCompare(row) {
  const pair = {}   // engine -> run
  for (const r of rows.value) {
    if (r.batch_id !== row.batch_id || r.eval_query_id !== row.eval_query_id) continue
    if (r.target_engine) pair[r.target_engine] = r
  }
  enginePair.value = pair
  engineCompareVisible.value = true
}
```

弹窗模板仿 A/B 对比：左右（或多列）分栏，每栏顶显产品 label + 实际模型（`r.trace?.model` 或从 payload），下显 answer/verdict/三维/score。"对比"按钮出现条件：该题在本批有 ≥2 个产品的 run。

- [ ] **Step 3: dimStats 按产品渲染（可选增强）**

若 `dimStats.by_engine` 有数据，在维度通过率面板加产品切换/并排（两组条形对比）。最小实现:加一个"按产品看"开关，切换时调 `evalDimensionStats(pid, 30, true)` 渲染 by_engine。

- [ ] **Step 4: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功。

- [ ] **Step 5: 真机/浏览器冒烟（可选）**

若有多产品批次数据，浏览器打开 EvalResults 确认：产品列显示、筛选生效、并排对比弹窗左右产品对照。无数据则跳过，留待 Plan 1+2 端到端联调时验。

- [ ] **Step 6: Commit（含 dist）**

```bash
cd frontend && npm run build   # 普通 build，别 build:clean
cd .. && git add frontend/src/views/EvalResults.vue frontend/dist
git commit -m "feat(eval): EvalResults 产品并排对比 + 产品筛选/分组"
```

---

## Task 7: 前端任务编辑加产品勾选

**Files:**
- Modify: `frontend/src/views/EvalTasks.vue`

**Interfaces:**
- Consumes: `listEvalEngines`、`createEvalTask`/`updateEvalTask`（payload 加 target_engines）。

- [ ] **Step 1: 任务创建/编辑表单加产品多选**

`EvalTasks.vue` 任务编辑弹窗加 `el-checkbox-group`（或 el-select multiple）绑 `form.target_engines`，选项来自 `listEvalEngines()`。默认勾选 namiwork。保存时 payload 带 `target_engines`。

- [ ] **Step 2: 执行任务时传 target_engines**

任务"执行"入口（调 `runEvalTask`）的 payload 带该任务的 `target_engines`（或用任务已存的）。若执行弹窗让临时改产品，加勾选；否则用任务级持久值。**决策:执行时默认用任务级 target_engines，执行弹窗提供覆盖勾选。**

- [ ] **Step 3: 构建验证 + Commit**

```bash
cd frontend && npm run build
cd .. && git add frontend/src/views/EvalTasks.vue frontend/dist
git commit -m "feat(eval): EvalTasks 任务编辑加被测产品勾选"
```

---

## Self-Review 记录

- **Spec 覆盖**:对应 spec §7（判定零改动 = Task 1 验证）、§8.1（后端 by_engine 统计 = Task 2/3）、§8.2（前端产品并排对比 + 筛选/分组 = Task 6，任务勾选 = Task 7，综合评价横评 = Task 4）。
- **占位符扫描**:Task 1/2 测试脚本的"构造逻辑实现时补全"是数据构造延迟（需真实 project/eval_query，按 backend/scripts 样板补），断言已明确；Task 6 Step 3/5 标"可选/最小实现"是增强项非核心，核心（产品列+筛选+并排弹窗）在 Step 1/2 明确。
- **类型一致**:`evalDimensionStats` 加第三参 `byEngine`（默认 false，旧调用兼容）；`target_engine` 前端读的是 `_to_out` L110 回显的顶层字段；`openEngineCompare` 配对键 `target_engine` 与后端字段名一致。
- **依赖检查**:本 plan 消费 Plan 2 的 `target_engines`/多引擎 run 和 Plan 1 的 WorkBuddy trace；Task 1 显式验证判定复用，是三部曲闭环的关键确认点。
- **构建纪律**:Task 6/7 改前端须 `npm run build` 且提交 dist（服务器无 Node），用普通 build 非 build:clean（项目记忆 frontend-dist-in-git-and-build-drift）。
