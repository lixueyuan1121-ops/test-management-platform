<template>
  <div class="release-checklist">
    <el-card>
      <template #header>
        <div class="header">
          <span>上线 checklist</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
          </div>
        </div>
      </template>

      <el-alert type="warning" :closable="false" show-icon class="intro">
        漏斗末端:用例库 → 回归用例库 → <b>上线checklist</b>。这里是上线前要跑一遍的最终回归集
        (在「回归用例库」勾选用例点「加入上线checklist」纳入)。勾选后选设备可<b>直接执行</b>;
        <b>移除</b>只从本清单剔除,不影响回归用例库和总用例。manual 用例不可执行。
      </el-alert>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-select v-model="releaseId" size="small" style="width:200px" clearable
                   placeholder="关联发版(可选)" no-data-text="本项目暂无发版记录">
          <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
        </el-select>
        <el-button type="success" size="small" :loading="dispatching" @click="runSelected">执行</el-button>
        <el-button type="danger" size="small" :loading="removing" @click="removeSelected">移出清单</el-button>
        <span class="sel-hint">执行仅跳过 manual;选了发版则结果计入该版本质量卡(实体级)</span>
      </div>

      <el-table :data="rows" v-loading="loading" size="small" border stripe
                empty-text="清单为空(去「回归用例库」勾选用例「加入上线checklist」)"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" />
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
            <el-button link type="danger" size="small" @click="removeOne(row)">移出</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="rows.length" class="foot">共 {{ rows.length }} 条待上线验证用例</div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
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

async function onProjectChange() {
  if (!pid.value) { rows.value = []; return }
  setLastProjectId(pid.value)
  releaseId.value = null
  try { releases.value = await listReleases({ project_id: pid.value }) } catch { releases.value = [] }
  await reload()
}

async function reload() {
  if (!pid.value) return
  loading.value = true
  try { rows.value = await listReleaseChecklist(pid.value) } catch { /* 拦截器已提示 */ }
  finally { loading.value = false }
}

async function runSelected() {
  if (!selected.value.length) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  const items = selected.value.filter((r) => (r.exec_kind || 'gui') !== 'manual')
  if (!items.length) { ElMessage.warning('选中项里没有可执行的用例(manual 不可自动化)'); return }
  const skipped = selected.value.length - items.length
  dispatching.value = true
  try {
    const res = await enqueueCases(pid.value, runner.value, items.map((r) => r.test_case_id), releaseId.value)
    const n = res?.run_ids?.length || items.length
    const relTip = releaseId.value ? '，结果计入所选版本质量卡' : ''
    ElMessage.success(`已下发 ${n} 条到 ${runner.value}${skipped ? `(跳过 ${skipped} 条 manual)` : ''}${relTip}，执行机跑完自动回写结果`)
  } catch { /* 拦截器已提示 */ }
  finally { dispatching.value = false }
}

async function removeSelected() {
  if (!selected.value.length) return
  removing.value = true
  try {
    const res = await removeReleaseChecklist(pid.value, selected.value.map((r) => r.test_case_id))
    ElMessage.success(`已移出 ${res.removed} 条(不影响回归用例)`)
    await reload()
  } catch { /* 拦截器已提示 */ }
  finally { removing.value = false }
}

async function removeOne(row) {
  try {
    await removeReleaseChecklist(pid.value, [row.test_case_id])
    ElMessage.success('已移出清单')
    await reload()
  } catch { /* 拦截器已提示 */ }
}

onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
  } catch { /* ignore */ }
  try { myDevices.value = await listMyDevices() } catch { /* ignore */ }
})
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; }
.intro { margin-bottom: 12px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: #fdf6ec; border-radius: 6px; margin-bottom: 10px; }
.sel-info { font-weight: 600; color: #e6a23c; }
.sel-hint { font-size: 12px; color: #909399; }
.page-tag { margin: 0 2px; }
.none { color: #c0c4cc; }
.foot { margin-top: 10px; font-size: 12px; color: #909399; text-align: right; }
</style>
