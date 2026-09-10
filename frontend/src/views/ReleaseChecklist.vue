<template>
  <div class="release-checklist">
    <WorkspacePage title="上线 checklist">
      <template #actions>
            <el-select v-model="pid" :disabled="dispatching || removing" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button size="small" :loading="loading" :disabled="dispatching || removing" @click="onProjectChange">刷新</el-button>
      </template>

      <template #selection>
      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" :disabled="dispatching || removing" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option label="自动调度(按平台挑在线空闲设备)" value="auto" />
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-select v-model="releaseId" :disabled="dispatching || removing" size="small" style="width:200px" clearable
                   placeholder="关联发版(可选)" no-data-text="本项目暂无发版记录">
          <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
        </el-select>
        <el-button type="primary" size="small" :loading="dispatching" :disabled="loading || removing || !runner" @click="runSelected">执行</el-button>
        <el-button type="danger" size="small" :loading="removing" :disabled="loading || dispatching" @click="removeSelected">移出清单</el-button>
        <span class="sel-hint">执行仅跳过 manual;选了发版则结果计入该版本质量卡(实体级)</span>
      </div>
      </template>
      <el-result v-if="loadError" icon="error" title="上线清单加载失败"><template #extra><el-button @click="onProjectChange">重试</el-button></template></el-result>
      <el-table v-else :data="rows" v-loading="loading" size="small" border stripe
                empty-text="清单为空(去「回归用例库」勾选用例「加入上线checklist」)"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" :selectable="() => !dispatching && !removing" />
        <el-table-column label="类型" width="72" align="center">
          <template #default="{ row }">
            <el-tag :type="KIND_TYPE[row.exec_kind || 'gui'] || 'info'" size="small" effect="plain">{{ row.exec_kind || 'gui' }}</el-tag>
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
            <span v-else class="none">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="240" show-overflow-tooltip />
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button link type="danger" size="small" :disabled="dispatching || removing || loading" @click="removeOne(row)">移出</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="rows.length" class="foot">共 {{ rows.length }} 条待上线验证用例</div>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/store/app'
import { listReleaseChecklist, removeReleaseChecklist, enqueueCases, listMyDevices, listReleases } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const KIND_TYPE = { gui: 'success', api: 'warning', cli: 'info', e2e: 'primary', manual: 'info' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'info', P3: 'info' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const rows = ref([])
const selected = ref([])
const myDevices = ref([])
const runner = ref(null)
const releases = ref([])
const releaseId = ref(null)   // 关联发版(可选):选中则本批执行计入该版本质量卡
const loading = ref(false)
const dispatching = ref(false)
const removing = ref(false)
const loadError = ref(false)
let requestVersion = 0; let disposed = false

async function onProjectChange() {
  releaseId.value = null
  releases.value = []
  if (pid.value) setLastProjectId(pid.value)
  await reload()
}

async function reload() {
  const version = ++requestVersion, project = pid.value
  rows.value = []; selected.value = []; loadError.value = false; loading.value = false
  if (!pid.value) return
  loading.value = true
  try {
    const data = await listReleaseChecklist(project)
    if (version !== requestVersion || disposed) return
    const rel = await listReleases({ project_id: project })
    if (version !== requestVersion || disposed) return
    rows.value = data; releases.value = rel.items || []
  } catch { if (version === requestVersion && !disposed) loadError.value = true }
  finally { if (version === requestVersion && !disposed) loading.value = false }
}

async function runSelected() {
  if (!selected.value.length || dispatching.value || removing.value || loading.value) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  const items = selected.value.filter((r) => (r.exec_kind || 'gui') !== 'manual')
  if (!items.length) { ElMessage.warning('选中项里没有可执行的用例(manual 不可自动化)'); return }
  const skipped = selected.value.length - items.length
  const project = pid.value, target = runner.value, release = releaseId.value
  dispatching.value = true
  try {
    await ElMessageBox.confirm(`下发 ${items.length} 条用例到 ${target === 'auto' ? '自动调度的设备' : target}${skipped ? `，跳过 ${skipped} 条 manual` : ''}${release ? `，关联版本 ${releases.value.find(r => r.id === release)?.version || release}` : ''}？`, '确认执行', { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' })
    if (disposed) return
    const res = await enqueueCases(project, target, items.map((r) => r.test_case_id), release)
    if (disposed) return
    const n = res?.run_ids?.length || items.length
    const relTip = releaseId.value ? '，结果计入所选版本质量卡' : ''
    ElMessage.success(`已下发 ${n} 条到 ${runner.value}${skipped ? `(跳过 ${skipped} 条 manual)` : ''}${relTip}，执行机跑完自动回写结果`)
  } catch { /* 拦截器已提示 */ }
  finally { dispatching.value = false }
}

async function removeSelected() {
  await removeItems(selected.value.map(r => r.test_case_id))
}

async function removeItems(ids) {
  if (!ids.length || removing.value || dispatching.value || loading.value) return
  const project = pid.value
  removing.value = true
  try {
    await ElMessageBox.confirm(`将 ${ids.length} 条用例移出上线清单？回归用例仍会保留。`, '确认移出', { type: 'warning', confirmButtonText: '确认移出', cancelButtonText: '取消' })
    if (disposed) return
    const res = await removeReleaseChecklist(project, ids)
    if (disposed) return
    ElMessage.success(`已移出 ${res.removed} 条(不影响回归用例)`)
    await reload()
  } catch { /* 拦截器已提示 */ }
  finally { removing.value = false }
}

async function removeOne(row) {
  await removeItems([row.test_case_id])
}

onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
  } catch { /* ignore */ }
  try { myDevices.value = await listMyDevices() } catch { /* ignore */ }
})
onBeforeUnmount(() => { disposed = true; ++requestVersion })
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; }
.intro { margin-bottom: 12px; }
.dispatch-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 0; border-top: 1px solid var(--el-border-color); }
.dispatch-bar .el-select { max-width: 100%; flex-shrink: 0; }
.dispatch-bar .el-button { margin-left: 0; }
.sel-info { font-weight: 600; color: #e6a23c; }
.sel-hint { font-size: 12px; color: #909399; }
.page-tag { margin: 0 2px; }
.none { color: #c0c4cc; }
.foot { margin-top: 10px; font-size: 12px; color: #909399; text-align: right; }
</style>
