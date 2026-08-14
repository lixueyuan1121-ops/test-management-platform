<template>
  <div class="adopted-cases">
    <el-card>
      <template #header>
        <div class="header">
          <span>已采纳用例</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="taskId" placeholder="关联任务" size="small" clearable filterable fit-input-width style="width:240px" @change="load">
              <el-option v-for="t in tasks" :key="t.id" :label="t.description || t.title" :value="t.id" :title="t.description || t.title" />
            </el-select>
            <el-select v-model="execKindFilter" placeholder="执行类型" size="small" clearable style="width:120px">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-input v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px" @keyup.enter="load" @clear="load" />
          </div>
        </div>
      </template>

      <el-alert type="success" :closable="false" show-icon class="intro">
        这里只列已采纳的用例,勾选后选自己的设备即可下发执行。人工(manual)用例不可下发。
      </el-alert>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-button type="primary" size="small" :loading="dispatching" @click="dispatchSelected">发送到执行机</el-button>
        <span class="sel-hint">仅『有关联任务 + 非人工』的选中项会下发</span>
      </div>

      <el-table :data="displayRows" v-loading="loading" size="small" border stripe empty-text="暂无已采纳用例"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column label="类型" width="72" align="center">
          <template #default="{ row }">
            <el-tag :type="KIND_TYPE[row.exec_kind || 'gui'] || 'info'" size="small" effect="plain">{{ KIND_LABEL[row.exec_kind || 'gui'] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="80" align="center">
          <template #default="{ row }"><el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="220">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listProjects, listTasks, listCases, attachChecklist, enqueueExec, listMyDevices } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const KIND_ORDER = { gui: 0, e2e: 1, api: 2, cli: 3, manual: 4 }
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' }, { value: 'api', label: 'API' }, { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' }, { value: 'manual', label: '人工' },
]

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const execKindFilter = ref(null)
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const myDevices = ref([])
const selected = ref([])
const runner = ref('')
const dispatching = ref(false)

const displayRows = computed(() => {
  let rs = rows.value
  if (execKindFilter.value) rs = rs.filter((r) => (r.exec_kind || 'gui') === execKindFilter.value)
  return [...rs].sort((a, b) => (KIND_ORDER[a.exec_kind || 'gui'] ?? 9) - (KIND_ORDER[b.exec_kind || 'gui'] ?? 9))
})

onMounted(async () => {
  try {
    myDevices.value = await listMyDevices()
    if (myDevices.value.length) runner.value = myDevices.value[0].runner_id
  } catch { myDevices.value = [] }
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
      review_status: 'adopted',          // 本页固定只看已采纳
      keyword: keyword.value.trim() || undefined,
    })
  } finally { loading.value = false }
}

function canDispatch(row) {
  return !!row.task_id && (row.exec_kind || 'gui') !== 'manual'   // 本页已保证 adopted
}

async function dispatchSelected() {
  if (!selected.value.length) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  const items = selected.value.filter(canDispatch)
  if (!items.length) { ElMessage.warning('选中项里没有可下发的用例(需:有关联任务 + 非人工)'); return }
  const skipped = selected.value.length - items.length
  dispatching.value = true
  try {
    const byTask = new Map()
    for (const r of items) {
      if (!byTask.has(r.task_id)) byTask.set(r.task_id, [])
      byTask.get(r.task_id).push(r.id)
    }
    const itemIds = []
    for (const [tid, caseIds] of byTask) {
      const checklist = await attachChecklist(tid, caseIds)
      for (const it of checklist) if (caseIds.includes(it.test_case_id)) itemIds.push(it.id)
    }
    if (!itemIds.length) { ElMessage.warning('未能生成可下发的清单项'); return }
    const res = await enqueueExec(pid.value, runner.value, itemIds)
    const n = res?.run_ids?.length || itemIds.length
    ElMessage.success(`已下发 ${n} 条到 ${runner.value}${skipped ? `(跳过 ${skipped} 条不可下发)` : ''},执行机跑完会自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.intro { margin-bottom: 10px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; padding: 8px 12px; background: #f3f8f6; border: 1px solid #d6e9e2; border-radius: 6px; }
.sel-info { font-weight: 600; color: #00926e; }
.sel-hint { color: #90a4ae; font-size: 12px; }
</style>
