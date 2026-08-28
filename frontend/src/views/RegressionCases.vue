<template>
  <div class="regression-cases">
    <el-card>
      <template #header>
        <div class="header">
          <span>回归用例库</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select
              v-if="pageOptions.length" v-model="pageFilter" placeholder="页面" size="small"
              clearable filterable style="width:150px" @change="reload"
            >
              <el-option v-for="p in pageOptions" :key="p" :label="p" :value="p" />
            </el-select>
            <TaskPicker v-model="taskId" :tasks="tasks" placeholder="关联任务" width="200px" @change="reload" />
            <el-select v-model="execKindFilter" multiple collapse-tags placeholder="执行类型(可多选)" size="small" clearable style="min-width:150px;max-width:240px" @change="reload">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-select v-model="platformFilter" placeholder="平台" size="small" clearable style="width:110px" @change="reload">
              <el-option v-for="p in PLATFORMS" :key="p.value" :label="p.label" :value="p.value" />
            </el-select>
            <el-input v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px" @keyup.enter="reload" @clear="reload" />
          </div>
        </div>
      </template>

      <el-alert type="success" :closable="false" show-icon class="intro">
        这里是回归用例库(在「用例库」勾选用例点「标记回归」纳入)。按<b>页面</b>筛选后勾选,选自己的设备即可<b>直接执行</b>——不依赖关联任务、无需先采纳;人工(manual)用例不可执行。
        <br>「导出脚本」把 GUI/E2E 用例导成 Playwright <code>.spec.mjs</code> 给开发本地自测;开发怎么跑、要装什么,见仓库根 <b>回归用例导出脚本-开发运行说明.md</b>。
      </el-alert>

      <div class="page-bar">
        <span class="sel-hint">共 {{ total }} 条回归用例{{ pageFilter ? `（页面：${pageFilter}）` : '' }}</span>
        <el-button
          size="small" :disabled="!displayRows.length"
          @click="toggleSelectAll"
        >{{ allSelected ? '取消全选' : '全选本页' }}</el-button>
      </div>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option label="⚡ 自动调度(按平台挑在线空闲设备)" value="auto" />
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-button type="success" size="small" :loading="dispatching" @click="runRegression">执行回归</el-button>
        <el-button type="primary" size="small" :loading="addingChecklist" @click="addToChecklist">加入上线checklist</el-button>
        <el-button size="small" :loading="exporting" @click="exportSelected">导出选中脚本</el-button>
        <span class="sel-hint">随选随跑,仅跳过 manual(不可自动化)用例</span>
      </div>

      <el-table ref="tableRef" :data="displayRows" v-loading="loading" size="small" border stripe empty-text="暂无回归用例（去「用例库」标记）"
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
        <el-table-column label="页面" width="120" align="center">
          <template #default="{ row }">
            <template v-if="row.page">
              <el-tag v-for="p in row.page.split(',').filter(Boolean)" :key="p" size="small" effect="plain" class="page-tag">{{ p }}</el-tag>
            </template>
            <span v-else class="page-none">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="220">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canExport(row)" link type="primary" size="small" @click="exportOne(row)"
            >导出脚本</el-button>
            <el-tooltip v-else content="仅 GUI/E2E 且已生成 script 的用例可导出" placement="top">
              <span class="exp-dim">导出脚本</span>
            </el-tooltip>
          </template>
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
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/store/app'
import { listCases, listTasks, listMyDevices, listSelectors, enqueueCases, exportPlaywrightOne, exportPlaywrightBulk, addReleaseChecklist, _blobErrorMsg } from '@/api'
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
const PLATFORMS = [
  { value: 'web', label: 'PC/Web' },
  { value: 'android', label: 'Android' },
  { value: 'ios', label: 'iOS' },
]

const projects = ref([])
const pid = ref(null)
const pageFilter = ref(null)       // 页面筛选(回归页核心维度)
const pageOptions = ref([])        // 页面候选:项目选择器已有 page
const taskId = ref(null)           // 关联任务筛选(按需求维度挑回归用例)
const tasks = ref([])              // 任务候选(TaskPicker 用,随项目切换拉取)
const execKindFilter = ref([])    // 执行类型筛选(多选,可同时选 gui+e2e)
const platformFilter = ref(null)   // 平台筛选(null=全部):web/android/ios
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const myDevices = ref([])
const selected = ref([])
const runner = ref('')
const dispatching = ref(false)
const exporting = ref(false)
const addingChecklist = ref(false)
const tableRef = ref(null)

// 分页(后端分页:total 为过滤后总数)
const page = ref(1)
const pageSize = ref(50)
const total = ref(0)

const displayRows = rows
const allSelected = computed(() => displayRows.value.length > 0 && selected.value.length === displayRows.value.length)

function toggleSelectAll() {
  tableRef.value?.toggleAllSelection()
}

onMounted(async () => {
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
  pageFilter.value = null
  pageOptions.value = []
  taskId.value = null
  tasks.value = []
  platformFilter.value = null
  if (!pid.value) { rows.value = []; total.value = 0; return }
  setLastProjectId(pid.value)
  // 页面候选:从项目选择器(共享域)派生 distinct page;任务候选供关联任务筛选。均失败不影响列表。
  try {
    const data = await listSelectors(pid.value)
    pageOptions.value = [...new Set((data.shared || []).map((k) => k.page).filter(Boolean))].sort()
  } catch { pageOptions.value = [] }
  try { tasks.value = await listTasks({ project_id: pid.value }) } catch { tasks.value = [] }
  await reload()
}

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
      is_regression: true,               // 本页固定只看回归用例
      page: pageFilter.value || undefined,
      task_id: taskId.value || undefined,
      exec_kind: execKindFilter.value.length ? execKindFilter.value.join(',') : undefined,
      platform: platformFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    rows.value = items || []
    total.value = t || 0
  } finally { loading.value = false }
}

// 执行回归:直接按用例 id 下发(不依赖任务/采纳,不挂清单);只发非 manual。
async function runRegression() {
  if (!selected.value.length) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  const items = selected.value.filter((r) => (r.exec_kind || 'gui') !== 'manual')
  if (!items.length) { ElMessage.warning('选中项里没有可执行的用例(manual 不可自动化)'); return }
  const skipped = selected.value.length - items.length
  dispatching.value = true
  try {
    const res = await enqueueCases(pid.value, runner.value, items.map((r) => r.id))
    const n = res?.run_ids?.length || items.length
    ElMessage.success(`已下发 ${n} 条回归到 ${runner.value}${skipped ? `(跳过 ${skipped} 条 manual)` : ''},执行机跑完自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}

// 把勾选的回归用例加入本项目「上线checklist」（漏斗末端；幂等，已在清单的跳过）。
async function addToChecklist() {
  if (!selected.value.length) return
  addingChecklist.value = true
  try {
    const res = await addReleaseChecklist(pid.value, selected.value.map((r) => r.id))
    ElMessage.success(`已加入上线checklist ${res.added} 条${res.added < selected.value.length ? '(其余已在清单)' : ''}`)
  } catch { /* http 拦截器已提示 */ }
  finally { addingChecklist.value = false }
}

// 能否导出 Playwright 脚本：仅 gui/e2e（后端仍会二次校验有无 script）。
function canExport(row) {
  return ['gui', 'e2e'].includes(row.exec_kind || 'gui')
}

// 单条导出：下载一个 .spec.mjs。
async function exportOne(row) {
  try {
    await exportPlaywrightOne(row.id)
    ElMessage.success(`已导出「${row.title}」`)
  } catch (e) {
    ElMessage.error(await _blobErrorMsg(e))
  }
}

// 批量导出：选中的 gui/e2e 用例打包 zip 下载。
async function exportSelected() {
  const items = selected.value.filter(canExport)
  if (!items.length) { ElMessage.warning('选中项里没有可导出的用例(需 GUI/E2E)'); return }
  exporting.value = true
  try {
    const skipped = await exportPlaywrightBulk(items.map((r) => r.id))
    const n = items.length - skipped.length
    ElMessage.success(`已导出 ${n} 条脚本${skipped.length ? `(跳过 ${skipped.length} 条：无 script 或不支持)` : ''}`)
  } catch (e) {
    ElMessage.error(await _blobErrorMsg(e))
  } finally { exporting.value = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.intro { margin-bottom: 10px; }
.page-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; padding: 8px 12px; background: #f3f8f6; border: 1px solid #d6e9e2; border-radius: 6px; }
.sel-info { font-weight: 600; color: #00926e; }
.exp-dim { color: #c0c4cc; font-size: 12px; cursor: not-allowed; }
.sel-hint { color: #90a4ae; font-size: 12px; }
.page-tag { margin: 1px 2px; }
.page-none { color: #c0c4cc; }
</style>
