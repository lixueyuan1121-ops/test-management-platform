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
            <TaskPicker v-model="taskId" :tasks="tasks" placeholder="关联任务" @change="reload" />
            <el-select v-model="execKindFilter" placeholder="执行类型" size="small" clearable style="width:120px" @change="reload">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-input v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px" @keyup.enter="reload" @clear="reload" />
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
          <el-option label="⚡ 自动调度(按平台挑在线空闲设备)" value="auto" />
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

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="load"
          @size-change="reload"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/store/app'
import { listTasks, listCases, attachChecklist, enqueueExec, listMyDevices } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import TaskPicker from '@/components/TaskPicker.vue'

const app = useAppStore()
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' }, { value: 'api', label: 'API' }, { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' }, { value: 'manual', label: '人工' },
]

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const execKindFilter = ref(null)   // 执行类型筛选,下推后端
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const myDevices = ref([])
const selected = ref([])
const runner = ref('')
const dispatching = ref(false)

// 分页(后端分页:total 为过滤后总数)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)

// 展示行直接用后端返回的当前页(排序/筛选均已下推后端)
const displayRows = rows

onMounted(async () => {
  // 设备与项目列表互不依赖,并行拉取;项目列表走 store 缓存
  const [devicesRes, projectsRes] = await Promise.allSettled([listMyDevices(), app.fetchProjects()])
  myDevices.value = devicesRes.status === 'fulfilled' ? devicesRes.value : []
  if (myDevices.value.length) runner.value = myDevices.value[0].runner_id
  projects.value = projectsRes.status === 'fulfilled' ? projectsRes.value : []
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null
  if (!pid.value) { tasks.value = []; rows.value = []; total.value = 0; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  await reload()
}

// 筛选条件变化:回到第 1 页再查
async function reload() {
  page.value = 1
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    const { items, total: t } = await listCases({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      review_status: 'adopted',          // 本页固定只看已采纳
      exec_kind: execKindFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    rows.value = items || []
    total.value = t || 0
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
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.intro { margin-bottom: 10px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; padding: 8px 12px; background: #f3f8f6; border: 1px solid #d6e9e2; border-radius: 6px; }
.sel-info { font-weight: 600; color: #00926e; }
.sel-hint { color: #90a4ae; font-size: 12px; }
</style>
