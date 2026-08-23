# 对话测评用例库 实现计划(子项B)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或内联执行。步骤用 checkbox 跟踪。

**Goal:** eval_query 生成历史可查、可再次下发验证——加 GET 列表端点 + 独立新页"对话测评用例库"。

**Architecture:** 后端 ai_eval.py 加只读列表端点(复用现有 _to_query_out);前端新页 EvalLibrary.vue(列表 + 勾选 + 复用设备下拉/enqueue 再下发)。

**Tech Stack:** FastAPI + SQLAlchemy;Vue3 + ElementPlus。

## Global Constraints
- 统一信封 {code,msg,data} 用 ok();复用现有 `_to_query_out`(ai_eval.py:32)。
- 鉴权 assert_project_role(admin/member/guest 可读)。
- 前端 api 薄封装返回解包 data;复用现有 listMyDevices/listEvalDevices/enqueueEvalQueries。
- 无 schema/DB 变更(EvalQuery 表子项1 已建)。
- 工作区无关既存改动(run.cmd/__MACOSX/qalab-runner.zip)不 add;精确 git add。
- 无测试框架:后端一次性 Python 脚本(tmp_ 前缀,跑完删)、前端 npm run build。
- 提交结尾 Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>。

---

## Task 1: 后端 GET eval_query 列表端点

**Files:** Modify `backend/app/api/ai_eval.py`;Test `backend/tmp_verify_eval_list.py`。

**Interfaces (Produces):** `GET /api/ai/eval-queries?project_id=X&limit=N` → `ok([_to_query_out...])`,created_at desc。

- [ ] **Step 1: import Query + ok**

`ai_eval.py` 顶部 import 调整:
- `from fastapi import APIRouter, Depends, HTTPException, Query, status`(加 Query)
- 加 `from app.schemas.common import ok`

- [ ] **Step 2: 加列表端点**

在 `gen_eval_queries` 函数之后(文件末尾 return StreamingResponse 那个函数后)加:
```python
@router.get("/eval-queries")
def list_eval_queries(
    project_id: int = Query(...),
    limit: int = Query(200, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出某项目历史生成的对话测评 query(供用例库查看 + 再次下发)。"""
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalQuery).filter(EvalQuery.project_id == project_id)
            .order_by(EvalQuery.id.desc()).limit(limit).all())
    return ok([_to_query_out(q) for q in rows])
```

- [ ] **Step 3: 验证脚本**

创建 `backend/tmp_verify_eval_list.py`:
```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DATABASE_URL"] = "sqlite:///./tmp_evallist.db"
from app.db.session import Base, engine, SessionLocal
from app.models import EvalQuery
from app.api.ai_eval import _to_query_out

Base.metadata.create_all(bind=engine, tables=[EvalQuery.__table__])
db = SessionLocal()
db.query(EvalQuery).delete(); db.commit()
for i in range(3):
    db.add(EvalQuery(project_id=1, title=f"q{i}", prompt=f"p{i}", dimension="thinking", conversation_group=f"g{i}", turn_index=0))
db.commit()
rows = (db.query(EvalQuery).filter(EvalQuery.project_id == 1).order_by(EvalQuery.id.desc()).limit(200).all())
assert len(rows) == 3, f"应3条,得{len(rows)}"
assert rows[0].title == "q2", f"desc排序应q2在前,得{rows[0].title}"
out = _to_query_out(rows[0])
for k in ("id","title","prompt","dimension","expected","conversation_group","turn_index","review_status","created_at"):
    assert k in out, f"_to_query_out 缺 {k}"
print("OK: 列表查询 desc 排序 + _to_query_out 字段齐")
db.close()
try:
    engine.dispose(); os.remove("./tmp_evallist.db")
except OSError: pass
print("ALL PASS")
```

- [ ] **Step 4: 跑验证**

Run: `cd backend && python tmp_verify_eval_list.py`
Expected: `ALL PASS`。

- [ ] **Step 5: 删脚本 + 提交**

```bash
rm backend/tmp_verify_eval_list.py
git add backend/app/api/ai_eval.py
git commit -m "feat(eval): GET /api/ai/eval-queries 列表端点(用例库)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 前端"对话测评用例库"页

**Files:** Create `frontend/src/views/EvalLibrary.vue`;Modify `frontend/src/api/index.js`、`frontend/src/router/index.js`、`frontend/src/layouts/MainLayout.vue`;Test `npm run build`。

**Interfaces:** Consumes `GET /api/ai/eval-queries`(Task1)、listMyDevices/listEvalDevices/enqueueEvalQueries(已有)。

- [ ] **Step 1: api 封装**

`frontend/src/api/index.js` 在 listEvalDevices 附近加:
```javascript
// 对话测评用例库:某项目历史生成的 eval_query 列表(再次触发验证用)。
export const listEvalQueries = (projectId) => http.get('/ai/eval-queries', { params: { project_id: projectId } })
```

- [ ] **Step 2: EvalLibrary.vue**

创建 `frontend/src/views/EvalLibrary.vue`(参照 AIEvalGen 下发区 + EvalResults 表格风格):
```vue
<template>
  <div class="eval-library">
    <el-card>
      <template #header>
        <div class="head">
          <div class="title-wrap">
            <el-icon class="title-icon"><Collection /></el-icon>
            <div>
              <div class="title">对话测评用例库</div>
              <div class="subtitle">历史生成的对话测评 query,可勾选再次下发到执行机验证</div>
            </div>
          </div>
          <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </div>
      </template>

      <div class="dispatch-bar" v-if="selected.length">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="chosenRunner" size="small" style="width:180px" placeholder="选择执行机" @change="loadClientDevices">
          <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-select v-model="chosenDevice" size="small" style="width:200px" clearable
          :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'">
          <el-option v-for="dev in clientDevices" :key="dev.vm_id"
            :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`" :value="dev.vm_id" />
        </el-select>
        <el-button type="success" size="small" :loading="dispatching" :disabled="!chosenRunner" @click="dispatch">
          下发选中到执行机
        </el-button>
      </div>

      <el-table :data="sorted" size="small" border stripe @selection-change="s => selected = s" v-loading="loading">
        <el-table-column type="selection" width="42" />
        <el-table-column label="维度" width="120" align="center">
          <template #default="{ row }"><el-tag :type="DIM_TYPE[row.dimension] || 'info'" effect="plain" size="small">{{ dimLabel(row.dimension) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="标题" min-width="180"><template #default="{ row }">{{ row.title }}</template></el-table-column>
        <el-table-column label="提问 prompt" min-width="240"><template #default="{ row }"><span class="multiline">{{ row.prompt || '—' }}</span></template></el-table-column>
        <el-table-column label="预期 expected" min-width="200"><template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template></el-table-column>
        <el-table-column label="对话组" min-width="110"><template #default="{ row }"><span class="mono">{{ row.conversation_group || '—' }}</span></template></el-table-column>
        <el-table-column label="轮次" width="64" align="center"><template #default="{ row }"><span class="mono">{{ row.turn_index ?? 0 }}</span></template></el-table-column>
        <el-table-column label="生成时间" width="160"><template #default="{ row }"><span class="mono">{{ (row.created_at || '').replace('T',' ').slice(0,19) }}</span></template></el-table-column>
      </el-table>
      <el-empty v-if="!loading && !queries.length" description="该项目暂无生成的对话测评 query,去『对话测评生成』生成" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Collection } from '@element-plus/icons-vue'
import { listEvalQueries, listMyDevices, listEvalDevices, enqueueEvalQueries } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const DIMENSIONS = [
  { k: 'thinking', label: '思考推理' }, { k: 'tool_use', label: '工具·MCP调用' },
  { k: 'artifact', label: '产物生成' }, { k: 'multi_turn', label: '多轮追问' }, { k: 'instruction', label: '指令遵循' },
]
const DIM_LABEL = Object.fromEntries(DIMENSIONS.map(d => [d.k, d.label]))
const dimLabel = (k) => DIM_LABEL[k] || k || '—'
const DIM_TYPE = { thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const queries = ref([])
const loading = ref(false)
const selected = ref([])
const devices = ref([])
const chosenRunner = ref('')
const clientDevices = ref([])
const chosenDevice = ref('')
const dispatching = ref(false)

const sorted = computed(() => [...queries.value].sort((a, b) =>
  String(a.conversation_group || '').localeCompare(String(b.conversation_group || '')) || (a.turn_index ?? 0) - (b.turn_index ?? 0)))

onMounted(async () => {
  const [projRes, devRes] = await Promise.allSettled([app.fetchProjects(), listMyDevices()])
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
  if (devices.value.length) { chosenRunner.value = devices.value[0].runner_id; await loadClientDevices() }
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
})

async function onProjectChange() {
  queries.value = []; selected.value = []
  if (!pid.value) return
  setLastProjectId(pid.value)
  loading.value = true
  try { queries.value = await listEvalQueries(pid.value) || [] } catch { queries.value = [] }
  finally { loading.value = false }
}

async function loadClientDevices() {
  chosenDevice.value = ''; clientDevices.value = []
  if (!chosenRunner.value) return
  try { clientDevices.value = await listEvalDevices(chosenRunner.value) || [] } catch { clientDevices.value = [] }
}

async function dispatch() {
  if (!selected.value.length || !chosenRunner.value) return
  dispatching.value = true
  try {
    const res = await enqueueEvalQueries({
      project_id: pid.value, runner: chosenRunner.value, target_engine: 'namiwork',
      target_device: chosenDevice.value || null, eval_query_ids: selected.value.map(q => q.id),
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条到 ${chosenRunner.value}(批次 ${res.batch_id})`)
  } catch { /* 拦截器已提示 */ }
  finally { dispatching.value = false }
}
</script>

<style scoped>
.eval-library { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: #00b386; }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.sel-info { font-weight: 600; color: #00926e; font-size: 13px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #5a6b7b; }
</style>
```

- [ ] **Step 3: 路由**

`frontend/src/router/index.js` 在 `ai-eval-gen` 那行下面加:
```javascript
      { path: 'eval-library', name: 'eval-library', component: () => import('@/views/EvalLibrary.vue'), meta: { title: '对话测评用例库' } },
```

- [ ] **Step 4: 导航**

`frontend/src/layouts/MainLayout.vue` 测试设计子菜单,"对话测评生成"(/ai-eval-gen)那行下面加:
```html
          <el-menu-item index="/eval-library"><el-icon><Collection /></el-icon><span>对话测评用例库</span></el-menu-item>
```
(Collection 图标已在 MainLayout import 中,无需补 import——验证时确认;若未 import 则加。)

- [ ] **Step 5: build**

Run: `cd frontend && npm run build`
Expected: 构建成功。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/views/EvalLibrary.vue frontend/src/api/index.js frontend/src/router/index.js frontend/src/layouts/MainLayout.vue frontend/dist
git commit -m "feat(eval): 对话测评用例库页(历史查看+勾选再下发)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review
- **Spec 覆盖**:§4 端点→Task1;§5.1 api→Task2-Step1;§5.2 页→Task2-Step2;§5.3 路由/导航→Task2-Step3/4。全覆盖。
- **类型一致**:listEvalQueries 返回 _to_query_out 列表(含 dimension/conversation_group/turn_index/created_at),前端表格字段对应;dispatch 复用 enqueueEvalQueries 入参(project_id/runner/target_engine/target_device/eval_query_ids)与后端 EvalEnqueueIn 一致。
- **占位**:Task2-Step4 需验证 Collection 图标是否已 import(MainLayout 现有 import 含 Collection——用例库已用;实施时确认)。
