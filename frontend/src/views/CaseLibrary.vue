<template>
  <div class="case-library">
    <el-card>
      <template #header>
        <div class="header">
          <span>用例库</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="reviewStatus" placeholder="采纳状态" size="small" clearable style="width:120px" @change="load">
              <el-option label="已采纳" value="adopted" />
              <el-option label="已否决" value="rejected" />
              <el-option label="待定" value="pending" />
            </el-select>
            <el-select v-model="taskId" placeholder="关联任务" size="small" clearable filterable fit-input-width style="width:240px" @change="load">
              <el-option v-for="t in tasks" :key="t.id" :label="t.description || t.title" :value="t.id" :title="t.description || t.title" />
            </el-select>
            <el-select v-model="category" placeholder="维度" size="small" clearable style="width:110px" @change="load">
              <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
            <el-select v-model="execKindFilter" placeholder="执行类型" size="small" clearable style="width:120px">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-input
              v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px"
              @keyup.enter="load" @clear="load"
            />
          </div>
        </div>
      </template>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:120px" placeholder="执行机">
          <el-option v-for="rn in RUNNERS" :key="rn" :label="rn" :value="rn" />
        </el-select>
        <el-button type="primary" size="small" :loading="dispatching" @click="dispatchSelected">发送到执行机</el-button>
        <span class="sel-hint">仅『已采纳且有关联任务』的用例可下发;人工(manual)用例不可下发</span>
      </div>

      <el-table :data="displayRows" v-loading="loading" size="small" border stripe empty-text="没有符合条件的用例"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" :selectable="canDispatch" />
        <el-table-column label="维度" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="CAT_TYPE[row.category] || 'info'" effect="plain" size="small">{{ row.category || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行类型" width="110" align="center">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.kind_reason" :content="'AI 判定：' + (row.kind_reason || '')" placement="top">
              <el-select :model-value="row.exec_kind || 'gui'" size="small" style="width:90px"
                         @change="(v) => onExecKindChange(row, v)">
                <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
              </el-select>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="180">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="采纳状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="RV_TYPE[row.review_status] || 'info'" size="small">{{ RV_LABEL[row.review_status] || '待定' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
        <el-table-column label="生成时间" width="140">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listProjects, listTasks, listCases, setCaseExecKind, attachChecklist, enqueueExec } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 维度 / 优先级 → el-tag 配色（与 AITestGen 口径一致）
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const CATEGORIES = ['功能', '边界', '异常', '兼容', '性能']
// 采纳三态 → 配色/文案（采纳=success / 否决=danger / 待定=info）
const RV_TYPE = { adopted: 'success', rejected: 'danger', pending: 'info' }
const RV_LABEL = { adopted: '已采纳', rejected: '已否决', pending: '待定' }
// 自动化执行类型：gui(客户端 UI) / api(接口) / cli(命令行) / e2e(多步端到端) / manual(人工，不下发)。
// 下发到 runner 时决定 Claude Code 怎么跑；manual 不派发到执行机。
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' },
  { value: 'api', label: 'API' },
  { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' },
  { value: 'manual', label: '人工' },
]

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const reviewStatus = ref(null)
const category = ref(null)
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const execKindFilter = ref(null)   // 执行类型筛选(null=全部)

// 执行类型 → 标签配色 + 排序权重(同类聚拢,方便按类勾选)
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const KIND_ORDER = { gui: 0, e2e: 1, api: 2, cli: 3, manual: 4 }

// 展示行:按执行类型筛选 + 按类型排序聚拢(同类相邻,方便"选某类全勾")
const displayRows = computed(() => {
  let rs = rows.value
  if (execKindFilter.value) rs = rs.filter((r) => (r.exec_kind || 'gui') === execKindFilter.value)
  return [...rs].sort((a, b) => (KIND_ORDER[a.exec_kind || 'gui'] ?? 9) - (KIND_ORDER[b.exec_kind || 'gui'] ?? 9))
})

// ---- 下发到执行机(用例库入口)----
// 可下发的执行机 id(须与各目标机 runner 的 RUNNER_ID 一致);与 Tasks.vue 保持一致。
const RUNNERS = ['mac-01', 'win-01']
const selected = ref([])
const runner = ref(RUNNERS[0])
const dispatching = ref(false)

// 某行能否下发:必须已采纳(attachChecklist 要求)+ 有关联任务 + 非 manual。
function canDispatch(row) {
  return row.review_status === 'adopted' && !!row.task_id && (row.exec_kind || 'gui') !== 'manual'
}

// 用例库下发:用例不一定挂清单项 → 先按任务分组 attachChecklist 建/取清单项,再 enqueue。
async function dispatchSelected() {
  const items = selected.value
  if (!items.length) return
  const manual = items.find((r) => (r.exec_kind || 'gui') === 'manual')
  if (manual) { ElMessage.warning(`含人工(manual)用例「${manual.title || ''}」,不能下发`); return }
  const noTask = items.find((r) => !r.task_id)
  if (noTask) { ElMessage.warning(`用例「${noTask.title || ''}」无关联任务,无法下发(需先在任务里关联)`); return }
  dispatching.value = true
  try {
    // 按 task_id 分组:同一任务的用例一起 attachChecklist,拿回 checklist_item.id
    const byTask = new Map()
    for (const r of items) {
      if (!byTask.has(r.task_id)) byTask.set(r.task_id, [])
      byTask.get(r.task_id).push(r.id)
    }
    const itemIds = []
    for (const [tid, caseIds] of byTask) {
      const checklist = await attachChecklist(tid, caseIds)   // 幂等:已存在则复用,返回这些用例对应的清单项
      for (const it of checklist) {
        if (caseIds.includes(it.test_case_id)) itemIds.push(it.id)
      }
    }
    if (!itemIds.length) { ElMessage.warning('未能生成可下发的清单项'); return }
    const res = await enqueueExec(pid.value, runner.value, itemIds)
    const n = res?.run_ids?.length || itemIds.length
    ElMessage.success(`已下发 ${n} 条到 ${runner.value},执行机跑完会自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}

onMounted(async () => {
  projects.value = await listProjects()
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null
  if (!pid.value) { tasks.value = []; rows.value = []; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listCases({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      review_status: reviewStatus.value || undefined,
      category: category.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
  } finally { loading.value = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

async function onExecKindChange(row, val) {
  const prev = row.exec_kind || 'gui'
  if (val === prev) return
  row.exec_kind = val   // 乐观更新
  try {
    await setCaseExecKind(row.id, val)
    ElMessage.success(`已设为 ${val.toUpperCase()} 执行`)
  } catch {
    row.exec_kind = prev   // 失败回滚（http 拦截器已弹错）
  }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; padding: 8px 12px; background: #f3f8f6; border: 1px solid #d6e9e2; border-radius: 6px; }
.sel-info { font-weight: 600; color: #00926e; }
.sel-hint { color: #90a4ae; font-size: 12px; }
</style>
