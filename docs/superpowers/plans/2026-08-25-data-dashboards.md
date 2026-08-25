# 数据看板增强（清单 1-5）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已确认清单为平台新增 4 个高冲击力数据看板（AI 价值漏斗、版本质量档案、测评维度雷达、回归防线日历墙），最后拼装质量作战大屏；全程不破坏现有功能。

**Architecture:** 每个看板 = 后端一个只读聚合端点（现算、不建表，沿用 `{code,msg,data}` 信封 + 手写 `_to_out` 风格）+ 前端一个视图（echarts 5.6 已装可直接用；深色 hero + 浅底卡片延续平台语言）。作战大屏纯前端拼装复用前面端点。每个看板独立成任务、独立提交，互不依赖，坏一个不影响其余。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（后端聚合）、Vue3 + echarts ^5.6（funnel/radar/heatmap/gauge）、既有测试模式（`backend/scripts/test_*.py` TestClient + 内存 SQLite）。

**Spec:** 无独立 spec 文件——需求即已确认的清单对话（本文件 Global Constraints + 各任务描述为准）。

## Global Constraints

- 统一响应信封 `{code,msg,data}`：后端用 `app/schemas/common.py` 的 `ok()`；前端 `api/http.js` 已解包，`api/index.js` 函数返回 data 本身。
- 不建独立统计表：全部对既有表现算聚合（平台既有惯例）。
- 日期比较统一 `func.date(col)`（兼容 SQLite 与生产 MySQL 5.6）；**MySQL 5.6 无 JSON 列**，任何 JSON 都是 Text 字符串手动 loads。
- `last_seen_at`/执行时间戳写入用 `datetime.utcnow()`；在线类判定必须同基准（见 devices.py 时区修复先例）。
- 权限：跨项目聚合端点复用 `stats.py::_visible_project_ids(db, user)`（平台管理员=全部，普通用户=参与项目）；无需平台管理员专属的页面不加 `meta.platformAdmin`。
- 测试：仓库无 pytest，用 `backend/scripts/test_*.py` 自执行脚本模式（TestClient + `sqlite:///:memory:` + StaticPool + 依赖覆盖），跑法 `cd backend && .venv/bin/python -m scripts.test_xxx`。
- 前端图表：echarts ^5.6.0 已在 dependencies；**按需引入**（`echarts/core` + 所需 chart/component + CanvasRenderer，参照 `WorkloadStats.vue` 的引入方式），别学 `PerfReport.vue` 全量 import。
- 视觉：延续设备看板确立的语言——浅色页面底（`#eef1f5→#f6f8fa` 渐变）+ 深色 hero 条（`#1a2836→#212f43` 渐变、`// EYEBROW` 等宽字标签、JetBrains Mono 数字）+ 白底卡片浮起（`border:#e3e8ef` + 阴影）。强调色：绿 `#00b386` / 蓝 `#2f7dd1` / 红 `#e5565f`。**类名严禁与内部区块重名**（前车之鉴：`.run`/`.active` 串味）。
- 关键字段事实（Explore 已核实，写代码时以此为准）：
  - `RemainingIssue` 负责人字段是 **`owner`**（不是 assignee_id）；severity=blocker/major/minor；status=open/resolved；有 `resolved_at`。
  - `TestCase` **没有** selector_status 列；「选择器待补」判据是 `kind_reason LIKE '[选择器待补]%'`（常量 `_SELECTOR_FIX_MARK`，从 `app.services.claude_runner` import）。
  - `/api/stats/ai` 在 `stats.py`，已有 by_provider/trend/adopt_rate 聚合可复用。
  - `EvalQuery.dimension`（String16，单值）+ `EvalRun.verdict`（"pass"/"fail"/"error"，NULL=未判定）+ `EvalRun.verdict_dims`（Text-JSON 三维结论）。
  - `FeedbackRun`：batch_id 关联 exec_run 现算通过率；`trigger` = auto/manual；`created_at` 定位到天。
  - `ReleaseRecord.release_date` 是 Date 列（index）。
- 不破坏现有功能：不改任何既有端点的返回结构；前端只新增路由/菜单/视图文件，改 `MainLayout.vue`/`router/index.js`/`api/index.js` 时只做追加；每任务完成跑既有测试回归（至少 `test_device_overview` + `test_exec_correct`）+ `npm run build` 须通过。
- 每任务收尾：构建 dist（`cd frontend && npm run build`）随任务一起提交（仓库惯例 dist 入库）。提交信息末尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: AI 全链路价值漏斗（后端聚合端点）

**Files:**
- Modify: `backend/app/api/stats.py`（追加 `GET /api/stats/ai-funnel`）
- Test: `backend/scripts/test_ai_funnel.py`（新建）

**Interfaces:**
- Consumes: `_visible_project_ids(db, user)`（stats.py 已有）；`_SELECTOR_FIX_MARK`（`from app.services.claude_runner import _SELECTOR_FIX_MARK`）
- Produces: `GET /api/stats/ai-funnel?days=30` → data 结构：
  ```json
  {
    "from": "2026-07-27", "to": "2026-08-25", "days": 30,
    "funnel": [
      {"stage": "generated",  "label": "AI 生成",   "count": 128},
      {"stage": "adopted",    "label": "已采纳",    "count": 96},
      {"stage": "automatable","label": "可自动化",  "count": 71},
      {"stage": "executed",   "label": "已执行",    "count": 64},
      {"stage": "passed",     "label": "执行通过",  "count": 57}
    ],
    "adopt_rate": 75.0,          // adopted/generated*100，1 位小数，0 除保护
    "bugs_found": 5,             // 该窗口 exec_run fail_kind='business' 计数
    "selector_pending": 8,       // kind_reason LIKE '[选择器待补]%' 且 review_status=adopted
    "saved_hours": 5.3           // executed*5min/60，1 位小数
  }
  ```

各阶段口径（时间窗按 `func.date(created_at)` ∈ [from,to]，项目按 `_visible_project_ids`）：
- generated: `TestCase` 计数
- adopted: 其中 `review_status == ReviewStatus.adopted`
- automatable: adopted 中 `exec_kind != 'manual'`
- executed: `ExecRun` 计数（窗口内 created_at，任意状态终态 passed/failed/blocked——不含 pending/running）
- passed: executed 中 `status == 'passed'`
- bugs_found: 窗口内 `ExecRun.fail_kind == 'business'` 计数
- selector_pending: **不限时间窗**（是当前存量卡点）`TestCase.kind_reason.like('[选择器待补]%')` 且 `review_status == adopted`

- [ ] **Step 1: 写失败测试**

创建 `backend/scripts/test_ai_funnel.py`（模板抄 `scripts/test_device_overview.py` 的内存库+依赖覆盖骨架）：

```python
"""ai-funnel 聚合端点自测。运行: cd backend && python -m scripts.test_ai_funnel"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.core.enums import ReviewStatus
from app.db.session import Base, get_db
from app.models import AiTask, ExecRun, Project, TestCase, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="P1", code="p1")
    _s.add_all([u, p]); _s.flush()
    at = AiTask(project_id=p.id, user_id=u.id, kind="testcase_gen", input_ref="r")
    _s.add(at); _s.flush()

    def tc(review, exec_kind, kind_reason=None):
        c = TestCase(ai_task_id=at.id, project_id=p.id, title="t",
                     exec_kind=exec_kind, review_status=review, kind_reason=kind_reason)
        _s.add(c); return c

    # 5 生成：3 采纳(2 可自动化 gui/api + 1 manual)、1 否决、1 待定
    tc(ReviewStatus.adopted, "gui")
    tc(ReviewStatus.adopted, "api")
    tc(ReviewStatus.adopted, "manual")
    tc(ReviewStatus.rejected, "gui")
    tc(ReviewStatus.pending, "gui")
    # 选择器待补卡点：adopted + 标记（不限窗口，故意造旧时间也算）
    old = tc(ReviewStatus.adopted, "gui", kind_reason="[选择器待补] 补齐选择器 key:xBtn 后即可执行 gui")
    old.created_at = datetime.now() - timedelta(days=90)

    def run(status, fail_kind=None, days_ago=0):
        r = ExecRun(project_id=p.id, runner="m", payload="{}", status=status, fail_kind=fail_kind)
        if days_ago: r.created_at = datetime.now() - timedelta(days=days_ago)
        _s.add(r)

    # 窗口内：2 passed + 1 failed(business) + 1 blocked(selector) + 1 running(不计 executed)
    run("passed"); run("passed"); run("failed", "business"); run("blocked", "selector"); run("running")
    # 窗口外（40 天前）：1 passed 不计
    run("passed", days_ago=40)
    _s.commit()


_seed()
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = lambda: iter([_s])
client = TestClient(app)


def main():
    r = client.get("/api/stats/ai-funnel", params={"days": 30})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    stages = {s["stage"]: s["count"] for s in d["funnel"]}
    assert stages["generated"] == 6, stages    # 5 + 1 张选择器待补(旧时间在 90 天前 → 30 天窗外) → 5
    # ↑ 注意：老卡点 created_at 在窗外，generated 应为 5；断言先写 5，若实现含窗外则此断言暴露口径错
    assert stages["generated"] == 5, stages
    assert stages["adopted"] == 3, stages
    assert stages["automatable"] == 2, stages
    assert stages["executed"] == 4, stages     # passed2+failed1+blocked1（running/窗外不计）
    assert stages["passed"] == 2, stages
    assert d["bugs_found"] == 1, d
    assert d["selector_pending"] == 1, d       # 不限窗口的存量卡点
    assert d["adopt_rate"] == 60.0, d          # 3/5
    assert d["saved_hours"] == round(4 * 5 / 60, 1), d
    # 非法 days 回落默认
    r2 = client.get("/api/stats/ai-funnel", params={"days": -1})
    assert r2.json()["code"] == 0
    print("OK test_ai_funnel")


if __name__ == "__main__":
    main()
```

注意：上面故意先写了一条互相矛盾的 generated 断言（6 然后 5）——**删掉 `== 6` 那行**，最终以 5 为准（窗口口径）。写文件时直接只留 `== 5`。

- [ ] **Step 2: 跑测试确认 RED**

Run: `cd backend && .venv/bin/python -m scripts.test_ai_funnel`
Expected: FAIL（404 → JSONDecodeError 或 code!=0，端点不存在）

- [ ] **Step 3: 实现端点**

在 `backend/app/api/stats.py` 末尾追加（import 区补 `from app.services.claude_runner import _SELECTOR_FIX_MARK`、`from app.models import TestCase, ExecRun`——先查文件头已有哪些，避免重复 import）：

```python
@router.get("/ai-funnel")
def ai_funnel(
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 全链路价值漏斗：生成→采纳→可自动化→已执行→通过，附真bug数/选择器卡点/省时。

    时间窗 [today-days+1, today]；项目范围 = 当前用户可见项目。全部现算不建表。
    selector_pending 是当前存量卡点（不限时间窗）。saved_hours 按每条执行折算 5 分钟人工。
    """
    if days <= 0 or days > 365:
        days = 30
    pids = _visible_project_ids(db, user)
    today = date.today()
    d_from = today - timedelta(days=days - 1)

    def _win(q, col):
        return q.filter(func.date(col) >= d_from, func.date(col) <= today)

    tc_base = db.query(func.count(TestCase.id)).filter(TestCase.project_id.in_(pids))
    generated = _win(tc_base, TestCase.created_at).scalar() or 0
    adopted = _win(tc_base.filter(TestCase.review_status == ReviewStatus.adopted),
                   TestCase.created_at).scalar() or 0
    automatable = _win(tc_base.filter(TestCase.review_status == ReviewStatus.adopted,
                                      TestCase.exec_kind != "manual"),
                       TestCase.created_at).scalar() or 0

    run_base = db.query(func.count(ExecRun.id)).filter(ExecRun.project_id.in_(pids))
    executed = _win(run_base.filter(ExecRun.status.in_(["passed", "failed", "blocked"])),
                    ExecRun.created_at).scalar() or 0
    passed = _win(run_base.filter(ExecRun.status == "passed"), ExecRun.created_at).scalar() or 0
    bugs_found = _win(run_base.filter(ExecRun.fail_kind == "business"),
                      ExecRun.created_at).scalar() or 0

    selector_pending = (
        db.query(func.count(TestCase.id))
        .filter(TestCase.project_id.in_(pids),
                TestCase.review_status == ReviewStatus.adopted,
                TestCase.kind_reason.like(f"{_SELECTOR_FIX_MARK}%"))
        .scalar() or 0
    )

    return ok({
        "from": str(d_from), "to": str(today), "days": days,
        "funnel": [
            {"stage": "generated", "label": "AI 生成", "count": generated},
            {"stage": "adopted", "label": "已采纳", "count": adopted},
            {"stage": "automatable", "label": "可自动化", "count": automatable},
            {"stage": "executed", "label": "已执行", "count": executed},
            {"stage": "passed", "label": "执行通过", "count": passed},
        ],
        "adopt_rate": round(adopted / generated * 100, 1) if generated else 0.0,
        "bugs_found": bugs_found,
        "selector_pending": selector_pending,
        "saved_hours": round(executed * 5 / 60, 1),
    })
```

先读 `stats.py` 头部确认 `date`/`timedelta`/`ReviewStatus`/`TestCase`/`ExecRun` 哪些已 import，缺啥补啥。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `cd backend && .venv/bin/python -m scripts.test_ai_funnel`
Expected: `OK test_ai_funnel`

- [ ] **Step 5: 回归既有测试**

Run: `cd backend && .venv/bin/python -m scripts.test_device_overview && .venv/bin/python -m scripts.test_exec_correct`
Expected: 两个 OK

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/stats.py backend/scripts/test_ai_funnel.py
git commit -m "feat(stats): AI 价值漏斗聚合端点 /api/stats/ai-funnel" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: AI 全链路价值漏斗（前端页面，改造 AIWall.vue 顶部嵌入）

**Files:**
- Modify: `frontend/src/api/index.js`（追加 `aiFunnel`）
- Modify: `frontend/src/views/AIWall.vue`（顶部插入漏斗区块——**只增不改**现有区块）
- Test: 手动冒烟 + `npm run build`

**Interfaces:**
- Consumes: `GET /api/stats/ai-funnel?days=`（Task 1）；AIWall.vue 现有的范围选择器（rangeDays computed）
- Produces: AIWall 页顶部新增「AI 价值漏斗」可视区块

设计（视觉冲击点）：
- 不用 echarts funnel（AIWall 全页手写 SVG 风格统一）——**自绘水平阶梯漏斗**：5 个从左到右递减的色块条（CSS flex + 宽度百分比 + clip-path 斜切），每级色块上大数字（JetBrains Mono 26px）+ 标签 + 相对上一级的转化率小字。色阶从 `#2f7dd1`（生成）渐进到 `#00b386`（通过）。
- 漏斗右侧三张小结卡：**揪出真 bug N**（红 `#e5565f` 大数字）、**选择器待补 N**（琥珀 `#d98a1f`，副标题"补齐即可自动执行"）、**已省人工 X 小时**（绿）。
- 复用 AIWall 现有 range 切换：days 参数跟随页面现有范围选择联动。

- [ ] **Step 1: api/index.js 追加**

```js
// AI 价值漏斗：生成→采纳→可自动化→执行→通过 + 真bug/选择器卡点/省时
export const aiFunnel = (days = 30) => http.get('/stats/ai-funnel', { params: { days } })
```

- [ ] **Step 2: AIWall.vue 顶部插入漏斗区块**

先 Read AIWall.vue 全文摸清现有结构（范围选择变量名、区块布局类名、色变量），然后在 hero 区块之后、主趋势图之前插入 `<div class="funnel-panel">`。模板骨架：

```html
<div class="funnel-panel">
  <div class="fp-title">// VALUE FUNNEL · AI 生成到执行的价值转化</div>
  <div class="fp-body">
    <div class="fp-steps">
      <div v-for="(s, i) in funnel.funnel" :key="s.stage" class="fp-step"
           :style="{ width: stepWidth(i), background: STEP_COLORS[i] }">
        <div class="fp-num">{{ s.count }}</div>
        <div class="fp-lbl">{{ s.label }}</div>
        <div v-if="i > 0" class="fp-rate">{{ convRate(i) }}%</div>
      </div>
    </div>
    <div class="fp-side">
      <div class="fp-card bug"><div class="n">{{ funnel.bugs_found }}</div><div class="l">揪出真 Bug</div></div>
      <div class="fp-card pend"><div class="n">{{ funnel.selector_pending }}</div><div class="l">选择器待补</div></div>
      <div class="fp-card save"><div class="n">{{ funnel.saved_hours }}<span class="u">h</span></div><div class="l">已省人工</div></div>
    </div>
  </div>
</div>
```

script 增量（融入现有 setup）：

```js
import { aiFunnel } from '@/api'
const funnel = ref({ funnel: [], bugs_found: 0, selector_pending: 0, saved_hours: 0 })
const STEP_COLORS = ['#2f7dd1', '#3f8fc9', '#31a3ab', '#19b394', '#00b386']
function stepWidth(i) {
  const max = funnel.value.funnel[0]?.count || 1
  const c = funnel.value.funnel[i]?.count || 0
  return Math.max(18, (c / max) * 100) + '%'   // 最窄 18% 保证数字可读
}
function convRate(i) {
  const prev = funnel.value.funnel[i - 1]?.count || 0
  const cur = funnel.value.funnel[i]?.count || 0
  return prev ? Math.round((cur / prev) * 100) : 0
}
async function loadFunnel() { try { funnel.value = await aiFunnel(rangeDaysValue) } catch {} }
```

（`rangeDaysValue` 用 AIWall 现有范围变量——Read 后对齐实际名字；范围切换的现有 watch/handler 里追加 `loadFunnel()` 调用；onMounted 里追加。）

样式要点（scoped，类名全用 `fp-` 前缀避免串味）：色块条 `clip-path: polygon(0 0, 100% 0, calc(100% - 14px) 100%, 0 100%)` 斜切出漏斗感；`.fp-num` JetBrains Mono 26px 白字；侧卡 `.bug .n{color:#e5565f}` `.pend .n{color:#d98a1f}` `.save .n{color:#00b386}`。

- [ ] **Step 3: 冒烟验证**

启动后端 + 前端 dev，Playwright 登录 → `/ai-wall`，截图确认：漏斗 5 级递减渲染、三张侧卡数字、切换范围时漏斗联动刷新、**页面原有区块（趋势图/维度/引擎表）完好**。

- [ ] **Step 4: 构建**

Run: `cd frontend && npm run build`
Expected: `✓ built`

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/index.js frontend/src/views/AIWall.vue frontend/dist
git commit -m "feat(ai-wall): 顶部嵌入 AI 价值漏斗(生成→采纳→自动化→执行→通过)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 版本质量档案（后端）

**Files:**
- Modify: `backend/app/api/release.py`（追加 `GET /api/releases/quality`）
- Test: `backend/scripts/test_release_quality.py`（新建）

**Interfaces:**
- Consumes: `ReleaseRecord`（release_date: Date, version, project_id, req_count）、`ExecRun`、`RemainingIssue`（owner/severity/status/created_at/resolved_at）
- Produces: `GET /api/releases/quality?project_id=&limit=6` → data:
  ```json
  {
    "items": [{
      "release_id": 3, "version": "v2.1.0", "release_date": "2026-08-20",
      "window_from": "2026-08-11", "window_to": "2026-08-20", "req_count": 12,
      "exec_total": 88, "exec_passed": 80, "pass_rate": 90.9,
      "bugs_found": 3,
      "issues_open": {"blocker": 0, "major": 2, "minor": 5},
      "grade": "green"   // green/yellow/red
    }]
  }
  ```

口径：
- 每个版本的统计窗 = (上一版 release_date, 本版 release_date]；最老一版窗起点 = 该版 release_date - 14 天。按 `func.date(ExecRun.created_at)`。
- `pass_rate` = passed/(passed+failed+blocked)*100，无执行则 null。
- `bugs_found` = 窗口内 `fail_kind='business'`。
- `issues_open` = **当前仍 open** 且 `created_at` 在窗口内的问题按 severity 计数。
- `grade`: red = 有 open blocker 或 pass_rate<70；yellow = 有 open major 或 pass_rate<90；否则 green。无执行数据时 grade="yellow"（信息不足）。
- 鉴权沿用 release.py 现有读端点的方式（Read 后对齐——项目成员可读）。`project_id` 必填；`limit` 默认 6、上限 20。

- [ ] **Step 1: 写失败测试**

`backend/scripts/test_release_quality.py`（骨架同 Task 1 测试；种子要点）：

```python
# 种子：项目 P1 + 两个版本
#   v1.0 release_date=20 天前；v2.0 release_date=5 天前
# exec_run：
#   12 天前(落 v2.0 窗)：passed×3, failed(business)×1  → v2.0: total4 pass3 rate75 bugs1
#   25 天前(落 v1.0 窗，v1.0 窗=release_date-14 起)：passed×1 → v1.0: total1 pass1 rate100
# remaining_issue：
#   10 天前 open major ×1（落 v2.0 窗）→ v2.0 grade=yellow（有 open major 且 rate75<90）
#   30 天前 open blocker ×1（窗外，不计入任何版本）
# 断言：
#   items[0] 是 v2.0（最新在前）：exec_total==4, exec_passed==3, pass_rate==75.0,
#     bugs_found==1, issues_open=={"blocker":0,"major":1,"minor":0}, grade=="yellow"
#   items[1] v1.0：pass_rate==100.0, grade=="green"
#   limit=1 时只回 1 条；缺 project_id 422
```

完整测试代码按 Task 1 模板写全（内存库 + admin 依赖覆盖 + client.get("/api/releases/quality", params={...})）。ReleaseRecord 构造需要 `release_date=date.today()-timedelta(days=20)` 形态（Date 列传 date 对象）。RemainingIssue 构造必填 `project_id/title`，`severity=IssueSeverity.major, status=IssueStatus.open`，`created_at` 手动覆盖。

- [ ] **Step 2: 跑测试 RED**

Run: `cd backend && .venv/bin/python -m scripts.test_release_quality`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现端点**

Read `backend/app/api/release.py` 摸清现有 import/鉴权写法后追加：

```python
@router.get("/quality")
def release_quality(
    project_id: int,
    limit: int = 6,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """版本质量档案：每个版本一张记分卡（窗口=上版发布日到本版发布日）。

    exec 通过率 + 真bug数 + 窗口内仍 open 的遗留问题按严重度 + 红黄绿定级。现算不建表。
    """
    assert_project_role(db, user, project_id, ALL_PROJECT_ROLES)   # 对齐文件内现有读端点的角色集合写法
    limit = min(max(limit, 1), 20)
    rows = (db.query(ReleaseRecord).filter(ReleaseRecord.project_id == project_id)
            .order_by(ReleaseRecord.release_date.desc(), ReleaseRecord.id.desc())
            .limit(limit + 1).all())   # 多取 1 个用于最老版的窗起点
    items = []
    for i, rel in enumerate(rows[:limit]):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        w_to = rel.release_date
        w_from = prev.release_date if prev else (w_to - timedelta(days=14))
        # 执行统计：(w_from, w_to] —— 用 > w_from 且 <= w_to
        q = (db.query(ExecRun.status, func.count(ExecRun.id))
             .filter(ExecRun.project_id == project_id,
                     func.date(ExecRun.created_at) > w_from,
                     func.date(ExecRun.created_at) <= w_to)
             .group_by(ExecRun.status).all())
        cnt = {getattr(s, "value", s): n for s, n in q}
        done = cnt.get("passed", 0) + cnt.get("failed", 0) + cnt.get("blocked", 0)
        pass_rate = round(cnt.get("passed", 0) / done * 100, 1) if done else None
        bugs = (db.query(func.count(ExecRun.id))
                .filter(ExecRun.project_id == project_id, ExecRun.fail_kind == "business",
                        func.date(ExecRun.created_at) > w_from,
                        func.date(ExecRun.created_at) <= w_to).scalar() or 0)
        sev_rows = (db.query(RemainingIssue.severity, func.count(RemainingIssue.id))
                    .filter(RemainingIssue.project_id == project_id,
                            RemainingIssue.status == IssueStatus.open,
                            func.date(RemainingIssue.created_at) > w_from,
                            func.date(RemainingIssue.created_at) <= w_to)
                    .group_by(RemainingIssue.severity).all())
        sev = {getattr(s, "value", s): n for s, n in sev_rows}
        issues_open = {"blocker": sev.get("blocker", 0), "major": sev.get("major", 0),
                       "minor": sev.get("minor", 0)}
        if issues_open["blocker"] or (pass_rate is not None and pass_rate < 70):
            grade = "red"
        elif issues_open["major"] or pass_rate is None or pass_rate < 90:
            grade = "yellow"
        else:
            grade = "green"
        items.append({
            "release_id": rel.id, "version": rel.version,
            "release_date": str(rel.release_date),
            "window_from": str(w_from), "window_to": str(w_to),
            "req_count": rel.req_count,
            "exec_total": done, "exec_passed": cnt.get("passed", 0),
            "pass_rate": pass_rate, "bugs_found": bugs,
            "issues_open": issues_open, "grade": grade,
        })
    return ok({"items": items})
```

import 需补：`ExecRun`、`RemainingIssue`、`IssueStatus`、`timedelta`、`func`、鉴权辅助（以 release.py 现况为准）。**注意路由顺序**：若文件里有 `GET /{rid}` 类动态路由，`/quality` 必须注册在它之前。

- [ ] **Step 4: 跑测试 GREEN**

Run: `cd backend && .venv/bin/python -m scripts.test_release_quality`
Expected: `OK test_release_quality`

- [ ] **Step 5: 回归 + 提交**

```bash
cd backend && .venv/bin/python -m scripts.test_device_overview && .venv/bin/python -m scripts.test_ai_funnel
git add backend/app/api/release.py backend/scripts/test_release_quality.py
git commit -m "feat(release): 版本质量档案聚合端点 /api/releases/quality" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 版本质量档案（前端，嵌入 ReleaseNotes.vue）

**Files:**
- Modify: `frontend/src/api/index.js`（追加 `releaseQuality`）
- Modify: `frontend/src/views/ReleaseNotes.vue`（在 stat-row 与 chart-wrap 之间插入质量档案横排——只增不改）
- Test: 手动冒烟 + `npm run build`

**Interfaces:**
- Consumes: `GET /api/releases/quality?project_id=&limit=6`；ReleaseNotes.vue 现有的项目选择变量
- Produces: 发版记录页新增「版本质量档案」卡片横排

设计（视觉冲击点）：
- 横向滚动卡片列（最近 6 版），每卡：左侧**大红黄绿灯圆点**（16px，green `#00b386`/yellow `#d98a1f`/red `#e5565f`，red 时呼吸动画）+ 版本号大字 + 发布日期；中部通过率**迷你环形**（SVG stroke-dasharray，通过率上色：≥90 绿 / ≥70 琥珀 / <70 红，null 显示 `--`）；底部三小格：真bug 数（红）、open blocker+major 数、需求数。
- 卡片类名全用 `rq-` 前缀。ReleaseNotes 的深色 board 区域内风格：半透明白卡 `rgba(255,255,255,.06)` + `border rgba(79,216,196,.18)`（对齐该页现有 stat-card 语言）。

- [ ] **Step 1: api/index.js 追加**

```js
// 版本质量档案：每版一张记分卡(通过率/真bug/遗留问题/红黄绿)
export const releaseQuality = (project_id, limit = 6) =>
  http.get('/releases/quality', { params: { project_id, limit } })
```

- [ ] **Step 2: ReleaseNotes.vue 插入档案横排**

先 Read 摸清：项目选择变量名、加载时机（onMounted/watch project）、板块布局。在 `.stat-row` 之后插入：

```html
<div v-if="quality.length" class="rq-row">
  <div v-for="q in quality" :key="q.release_id" class="rq-card" :class="`rq-${q.grade}`">
    <div class="rq-hd">
      <span class="rq-light" :class="q.grade"></span>
      <span class="rq-ver">{{ q.version }}</span>
      <span class="rq-date">{{ q.release_date }}</span>
    </div>
    <div class="rq-mid">
      <svg viewBox="0 0 44 44" class="rq-ring">
        <circle cx="22" cy="22" r="18" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="4"/>
        <circle v-if="q.pass_rate != null" cx="22" cy="22" r="18" fill="none"
                :stroke="rateColor(q.pass_rate)" stroke-width="4" stroke-linecap="round"
                :stroke-dasharray="`${q.pass_rate * 1.131} 113.1`" transform="rotate(-90 22 22)"/>
        <text x="22" y="26" text-anchor="middle" fill="#fff" font-size="11"
              font-family="'JetBrains Mono',monospace">{{ q.pass_rate != null ? Math.round(q.pass_rate) : '--' }}</text>
      </svg>
      <div class="rq-rl">执行通过率<br/><span class="rq-sub">{{ q.exec_passed }}/{{ q.exec_total }}</span></div>
    </div>
    <div class="rq-ft">
      <span class="rq-bug">{{ q.bugs_found }} bug</span>
      <span class="rq-iss">{{ q.issues_open.blocker + q.issues_open.major }} 高危遗留</span>
      <span class="rq-req">{{ q.req_count }} 需求</span>
    </div>
  </div>
</div>
```

script：`const quality = ref([])`；`rateColor(r){ return r >= 90 ? '#00e5a0' : r >= 70 ? '#d98a1f' : '#ff5c6c' }`；在现有项目加载/切换 handler 追加 `releaseQuality(pid).then(d => quality.value = d.items).catch(() => {})`。
样式：`.rq-row{display:flex;gap:12px;overflow-x:auto;margin-top:14px}`；`.rq-light.red{animation: rq-breathe 1.4s infinite}` + `@keyframes rq-breathe{50%{opacity:.3}}`；红卡加 `border-color:rgba(255,92,108,.4)`。

- [ ] **Step 3: 冒烟 + 构建 + 提交**

Playwright 打开 `/releases` 截图：档案横排渲染、红黄绿灯正确、现有统计/图表/表格完好。然后：

```bash
cd frontend && npm run build
git add frontend/src/api/index.js frontend/src/views/ReleaseNotes.vue frontend/dist
git commit -m "feat(release): 发版记录页嵌入版本质量档案(红黄绿记分卡)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 对话测评维度雷达（后端）

**Files:**
- Modify: `backend/app/api/eval_judge.py` 或 `ai_eval.py`（Read 后选路由前缀合适者；追加 `GET /api/eval/dimension-stats`——以文件内实际 prefix 为准调整路径，下文接口路径按最终注册结果同步到前端）
- Test: `backend/scripts/test_eval_dimension_stats.py`（新建）

**Interfaces:**
- Consumes: `EvalQuery.dimension`（String16 单值，可空）、`EvalRun.verdict`（"pass"/"fail"/"error"/NULL）、`EvalRun.eval_query_id`
- Produces: `GET <eval prefix>/dimension-stats?project_id=&days=30` → data:
  ```json
  {
    "days": 30,
    "dims": [
      {"dimension": "准确性", "total": 12, "passed": 9, "pass_rate": 75.0},
      {"dimension": "安全性", "total": 8,  "passed": 8, "pass_rate": 100.0}
    ],
    "judged_total": 20, "overall_rate": 85.0
  }
  ```

口径：窗口内（`func.date(EvalRun.created_at)`）`verdict IN ('pass','fail')` 的 run（error/NULL 不计），join `EvalQuery` 取 dimension；dimension 为空归入 `"未标注"`。`pass_rate` 1 位小数。项目必填。dims 按 total 降序。

- [ ] **Step 1: 写失败测试**

种子：1 项目 + 3 个 EvalQuery（dimension 分别 "准确性"/"准确性"/"安全性"）+ 若干 EvalRun：
- 准确性 q1: pass、fail（2 判定）；q2: pass（1 判定）→ 准确性 total3 passed2 rate66.7
- 安全性 q3: pass ×2 → total2 passed2 rate100.0
- q1 再挂 1 条 verdict=NULL（未判定）+ 1 条 "error" → 都不计
- 40 天前一条 pass → 窗外不计
断言 dims 排序（准确性 total3 在前）、各 rate、judged_total==5、overall_rate==80.0。EvalRun 构造必填 `project_id/runner`（默认有）+ `eval_query_id`；`verdict` 直接给字符串。

完整代码按 Task 1 骨架 + 上述种子写全。

- [ ] **Step 2: RED**

Run: `cd backend && .venv/bin/python -m scripts.test_eval_dimension_stats`
Expected: FAIL

- [ ] **Step 3: 实现**

Read 目标文件确认 prefix 与鉴权风格后追加（聚合 SQL 形态）：

```python
rows = (db.query(EvalQuery.dimension, EvalRun.verdict, func.count(EvalRun.id))
        .join(EvalQuery, EvalQuery.id == EvalRun.eval_query_id)
        .filter(EvalRun.project_id == project_id,
                EvalRun.verdict.in_(["pass", "fail"]),
                func.date(EvalRun.created_at) >= d_from)
        .group_by(EvalQuery.dimension, EvalRun.verdict).all())
# Python 侧归拢 {dim: {"total": n, "passed": n}}，dim None → "未标注"，组装 dims 列表按 total 降序
```

- [ ] **Step 4: GREEN + 回归 + 提交**

```bash
cd backend && .venv/bin/python -m scripts.test_eval_dimension_stats && .venv/bin/python -m scripts.test_device_overview
git add backend/app/api/<目标文件>.py backend/scripts/test_eval_dimension_stats.py
git commit -m "feat(eval): 测评维度通过率聚合端点 dimension-stats" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 对话测评维度雷达（前端，嵌入 EvalResults.vue）

**Files:**
- Modify: `frontend/src/api/index.js`（追加 `evalDimensionStats`）
- Modify: `frontend/src/views/EvalResults.vue`（顶部插入雷达卡片区——只增不改）
- Test: 手动冒烟 + `npm run build`

**Interfaces:**
- Consumes: Task 5 端点；EvalResults.vue 现有项目选择变量
- Produces: 测评结果页顶部「能力画像」雷达图

设计（视觉冲击点）：
- echarts **radar**（按需引入：`echarts/core` + `RadarChart` + `TooltipComponent` + `CanvasRenderer`——参照 WorkloadStats.vue 引入写法）。
- 深色小面板（`#1a2836→#212f43` 渐变、对齐 hero 语言）内放雷达：indicator = dims（max=100），系列值 = pass_rate，区域填充 `rgba(0,229,160,.35)` + 线 `#00e5a0`，配中心大字 overall_rate。
- **维度 < 3 时雷达不可用** → 退化为水平条形列表（每维一条 CSS bar，同色系），模板 `v-if="dims.length >= 3"` 切换。空数据显示空态引导文案。

- [ ] **Step 1: api/index.js 追加**

```js
// 对话测评维度通过率(能力画像雷达)
export const evalDimensionStats = (project_id, days = 30) =>
  http.get('<Task5 实际路径>', { params: { project_id, days } })
```

- [ ] **Step 2: EvalResults.vue 插入雷达区**

Read 现有结构后，顶部插入 `<div class="dim-radar-panel">`：echarts 容器 `<div ref="radarEl" class="dr-chart">` + 右侧 overall 大数字。init 形态：

```js
import * as echarts from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([RadarChart, TooltipComponent, CanvasRenderer])
// option 要点：radar.indicator = dims.map(d => ({name: `${d.dimension}\n${d.pass_rate}%`, max: 100}))
// series data = [dims.map(d => d.pass_rate)]，areaStyle rgba(0,229,160,.35)
// 组件卸载 dispose；项目切换时 setOption 重绘
```

- [ ] **Step 3: 冒烟 + 构建 + 提交**

Playwright 打开 `/eval-results` 截图（有测评数据的项目雷达渲染 / 无数据空态 / 原有结果表完好）。

```bash
cd frontend && npm run build
git add frontend/src/api/index.js frontend/src/views/EvalResults.vue frontend/dist
git commit -m "feat(eval): 测评结果页嵌入维度能力画像雷达" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 回归防线日历墙（后端）

**Files:**
- Modify: `backend/app/api/feedback.py`（追加 `GET /api/feedback/defense-calendar`）
- Test: `backend/scripts/test_defense_calendar.py`（新建）

**Interfaces:**
- Consumes: `FeedbackRun`（batch_id/trigger/created_at/project_id）、`ExecRun`（batch_id/status）
- Produces: `GET /api/feedback/defense-calendar?weeks=12` → data:
  ```json
  {
    "from": "2026-06-02", "to": "2026-08-25",
    "days": [{"date": "2026-08-25", "runs": 2, "cases": 34, "failed": 1, "state": "red"}],
    "streak": 23,          // 从今天往回数连续 state!=gray 的天数（今天没跑不断签：从昨天起算）
    "total_guard_days": 47 // 窗口内有跑批的天数
  }
  ```

口径：
- `days` 覆盖窗口内每一天（含没跑的天，state="gray"），按 FeedbackRun.created_at 的 `func.date` 归天；该天所有 run 的 batch_id 集合去 `ExecRun` 聚合 failed/blocked>0 → "red"，否则 "green"（有跑就非灰；跑了但 exec_run 还全 pending/running 算 "green" 口径从简：failed+blocked==0 即 green）。
- `streak`：从今天开始往回连续非 gray 天数；若今天 gray 从昨天起算（今天可能还没到跑批时间，不算断签）。
- feedback 是全局模块（专用项目），无 project_id 参数；鉴权对齐 feedback.py 现有用户端点（登录即可/成员——Read 后对齐）。
- `weeks` 默认 12、上限 26。

- [ ] **Step 1: 写失败测试**

种子（今天记 D）：
- D-1：FeedbackRun(batch_id="b1") + exec_run(batch_id="b1") passed×3 → green
- D-2：FeedbackRun(batch_id="b2") + exec_run passed×1 failed×1 → red
- D-3：无 → gray
- D-4：FeedbackRun(batch_id="b4") + passed×2 → green
断言：days 里 D-1 green / D-2 red / D-3 gray / D-4 green；streak==2（D-1、D-2 连续非灰，D-3 断）；total_guard_days==3。FeedbackRun 构造需 `project_id`（种一个项目）+ `batch_id` + created_at 手动覆盖。ExecRun 需 `project_id/payload/batch_id/status`。

- [ ] **Step 2: RED**

Run: `cd backend && .venv/bin/python -m scripts.test_defense_calendar`
Expected: FAIL

- [ ] **Step 3: 实现**

feedback.py 追加。聚合形态：一次查窗口内 FeedbackRun 按天归拢 batch_id 集合与 run 数；一次查这些 batch 的 exec_run `group by (batch_id, status)`；Python 侧拼每天 state。再从 to 往回扫 streak。**注意**：`/defense-calendar` 静态路径需注册在任何 `/{xxx}` 动态路由之前（feedback.py 若有）。

- [ ] **Step 4: GREEN + 回归 + 提交**

```bash
cd backend && .venv/bin/python -m scripts.test_defense_calendar && .venv/bin/python -m scripts.test_device_overview
git add backend/app/api/feedback.py backend/scripts/test_defense_calendar.py
git commit -m "feat(feedback): 回归防线日历聚合端点 defense-calendar" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 回归防线日历墙（前端，嵌入 FeedbackRegression.vue）

**Files:**
- Modify: `frontend/src/api/index.js`（追加 `defenseCalendar`）
- Modify: `frontend/src/views/FeedbackRegression.vue`（顶部插入日历墙——只增不改）
- Test: 手动冒烟 + `npm run build`

**Interfaces:**
- Consumes: Task 7 端点
- Produces: 回归用例集页顶部「回归防线」日历墙 + 连续值守大数字

设计（视觉冲击点）：
- GitHub 贡献墙式 CSS grid：`grid-template-rows: repeat(7, 12px)`、按列排周（`grid-auto-flow: column`），每格 12×12 圆角 3px；green `#00b386`（当天 cases 越多越深，3 档透明度）/ red `#e5565f` / gray `#e3e8ef`。hover 原生 `title` 提示 `date · N 批 M 例 K 失败`。
- 左侧大数字：**「防线已连续值守 N 天」**（JetBrains Mono 44px 绿字），streak==0 时改为琥珀提示「防线待激活」。
- 全部纯 CSS，类名 `dc-` 前缀，无需 echarts。

- [ ] **Step 1: api 追加**

```js
// 回归防线日历(GitHub 贡献墙式)
export const defenseCalendar = (weeks = 12) => http.get('/feedback/defense-calendar', { params: { weeks } })
```

- [ ] **Step 2: FeedbackRegression.vue 插入**

模板骨架：

```html
<div class="dc-panel">
  <div class="dc-left">
    <div class="dc-streak">{{ cal.streak }}</div>
    <div class="dc-streak-lbl">{{ cal.streak > 0 ? '防线已连续值守(天)' : '防线待激活' }}</div>
    <div class="dc-total">窗口内值守 {{ cal.total_guard_days }} 天</div>
  </div>
  <div class="dc-wall">
    <span v-for="d in cal.days" :key="d.date" class="dc-cell" :class="`dc-${d.state}`"
          :style="d.state === 'green' ? { opacity: greenOpacity(d.cases) } : {}"
          :title="`${d.date} · ${d.runs} 批 ${d.cases} 例` + (d.failed ? ` ${d.failed} 失败` : '')"/>
  </div>
</div>
```

`greenOpacity(c){ return c >= 30 ? 1 : c >= 10 ? .75 : .5 }`。加载：onMounted `defenseCalendar().then(d => cal.value = d).catch(() => {})`。

- [ ] **Step 3: 冒烟 + 构建 + 提交**

```bash
cd frontend && npm run build
git add frontend/src/api/index.js frontend/src/views/FeedbackRegression.vue frontend/dist
git commit -m "feat(feedback): 回归用例集页嵌入防线日历墙(连续值守)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 质量作战大屏（拼装整合页）

**Files:**
- Create: `frontend/src/views/WarRoom.vue`
- Modify: `frontend/src/router/index.js`（追加 `/war-room` 路由）
- Modify: `frontend/src/layouts/MainLayout.vue`（「设备看板」菜单项旁追加「作战大屏」，同为 `v-if="auth.isPlatformAdmin"`）
- Modify: `frontend/src/router/index.js` 路由挂 `meta: { title: '作战大屏', platformAdmin: true }`
- Test: 手动冒烟 + `npm run build`

**Interfaces:**
- Consumes（全部复用，零新后端）: `getDeviceOverview()`、`aiFunnel(30)`、`defenseCalendar(12)`、`overviewStats()`（Dashboard 用的今日 KPI）、`execHistory`/近期执行列表接口（Read api/index.js 找现成的执行历史函数；若仅有 project 维度的就并发拉可见项目 top 若干合并，**不写新端点**）
- Produces: `/war-room` 挂电视全屏页

设计（视觉冲击点——整页深色，这是全站唯一整页深色页，作为"大屏"定位是刻意差异）：
- 顶部：品牌条 `// QUALITY WAR ROOM` + 实时时钟（复用 DeviceBoard 的 clock 写法）+ 今日四大数字（今日执行/通过率/在线设备/防线连续天数）。
- 中部左：AI 漏斗迷你版（复用 Task 2 的 CSS 漏斗，数据同 aiFunnel）；中部右：设备编队缩略（在线灯 + running 计数，数据 getDeviceOverview）。
- 底部：防线日历墙缩略（复用 dc- 结构） + 最近执行滚动流（`<transition-group>` 纵向滚动，pass 绿行/fail 红行，每 30s 轮询）。
- 30s 自动整体刷新（`setInterval` 并发拉全部接口，onUnmounted 清理）；`prefers-reduced-motion` 尊重。
- 组件内样式自包含（类名 `wr-` 前缀），**不 import 其他视图组件**——漏斗/日历墙的 CSS 在本页复制精简版（两页样式独立演化，避免耦合；此处刻意 WET）。

- [ ] **Step 1: 建视图 + 路由 + 菜单**

WarRoom.vue 按上述设计写全（结构参照 DeviceBoard.vue 的深色版语言：`margin:-20px` 铺满 + `#121a25→#0d131b` 深底——大屏页够格用深色）；router 追加：

```js
{ path: 'war-room', name: 'war-room', component: () => import('@/views/WarRoom.vue'), meta: { title: '作战大屏', platformAdmin: true } },
```

MainLayout 设备看板菜单项后追加：

```html
<el-menu-item v-if="auth.isPlatformAdmin" index="/war-room"><el-icon><DataBoard /></el-icon><span>作战大屏</span></el-menu-item>
```

（`DataBoard` 图标需加入 icons import；若 element icons 无此名，Read 可用图标列表换 `Monitor` 之外未占用者，如 `FullScreen`。）

- [ ] **Step 2: 冒烟**

Playwright：管理员登录 → `/war-room` 截图（四大数字/漏斗/设备缩略/日历墙/滚动流全渲染，30s 刷新不报错，非管理员被踢回 dashboard）；再抽查 `/device-board`、`/ai-wall`、`/releases` 确认未破坏。

- [ ] **Step 3: 构建 + 提交**

```bash
cd frontend && npm run build
git add frontend/src/views/WarRoom.vue frontend/src/router/index.js frontend/src/layouts/MainLayout.vue frontend/dist
git commit -m "feat(war-room): 质量作战大屏(全平台脉搏+漏斗+编队+防线+实时流)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: 收尾——全量回归 + 推送

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && for t in test_device_overview test_ai_funnel test_release_quality test_eval_dimension_stats test_defense_calendar test_exec_correct test_exec_blocked; do .venv/bin/python -m scripts.$t || echo "FAIL $t"; done`
Expected: 全 OK 无 FAIL

- [ ] **Step 2: 前端构建 + 关键页冒烟**

`npm run build` 通过；Playwright 依次打开 `/dashboard`、`/ai-wall`、`/releases`、`/eval-results`、`/feedback-regression`、`/device-board`、`/war-room` 各截一图确认渲染正常。

- [ ] **Step 3: 推送**

```bash
git push origin main
```

（全程在 main 上按任务小步提交的话此步直接推；若用了分支则合并后推。）

## Self-Review 结论

- 覆盖：清单 1→Task1/2，2→Task3/4，3→Task5/6，4→Task7/8，5→Task9，收尾 Task10。引擎对战已按用户指示剔除（Task2 无 by_provider 内容）；团队效能热力未纳入。
- 字段核实：issue 负责人=owner、选择器待补=kind_reason LIKE、/stats/ai 在 stats.py——均已按 Explore 结果写入约束。
- 类型一致：`aiFunnel/releaseQuality/evalDimensionStats/defenseCalendar` 前后端名称与参数在任务间一致；Task9 只消费已定义接口。
- 风险位已标注：release.py/feedback.py 静态路由须先于动态路由注册；eval 端点文件与 prefix 执行时 Read 决定，前端路径同步。
