<template>
  <div class="rts-page">
    <WorkspacePage title="回归智选">
      <template #actions>
            <el-select v-model="projectId" aria-label="项目" placeholder="项目" :disabled="analyzing || dispatching" size="small" style="width:180px" @change="onProject">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="releaseId" aria-label="版本" placeholder="版本" :disabled="releasesLoading || analyzing || dispatching" size="small" style="width:200px" clearable
                       no-data-text="该项目暂无发版记录" @change="onRelease">
              <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
            </el-select>
            <el-select v-model="runner" aria-label="运行机" :disabled="dispatching" size="small" style="width:180px" placeholder="运行机"
                       :no-data-text="devices.length ? '' : '去『我的设备』注册'">
              <el-option label="自动挑选" value="auto" />
              <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
            </el-select>
      </template>

      <div v-if="reco" class="reco" :class="`risk-${reco.overall_risk}`">
        <div class="reco-h">整体风险：<b>{{ riskLabel(reco.overall_risk) }}</b>　建议跑 {{ reco.recommended_count }} / {{ reco.candidate_count }} 条</div>
        <div class="reco-s">{{ reco.summary }}</div>
        <div class="reco-r">{{ reco.rationale }}</div>
        <div class="reco-f"><span v-for="(f,i) in reco.focus_points" :key="i" class="fp">{{ f }}</span></div>
      </div>

      <template #selection><div v-if="releaseId" class="bar">
        <span>候选 {{ candidates.length }} 条（属本版本 {{ inReleaseCount }}）· 已选 {{ checked.length }}</span>
        <el-button size="small" :loading="analyzing" :disabled="loading || !!loadError || dispatching || !candidates.length" @click="runAnalyze">AI 生成推荐</el-button>
        <el-button size="small" type="primary" :loading="dispatching" :disabled="loading || !!loadError || analyzing || !checked.length" @click="dispatch">下发所选回归</el-button>
      </div></template>

      <el-result v-if="loadError" icon="error" :title="loadError"><template #extra><el-button @click="releaseId ? onRelease() : onProject()">重试</el-button></template></el-result>
      <el-empty v-else-if="!releaseId && !releasesLoading" description="请选择版本" />
      <el-table v-else ref="tableRef" :data="candidates" size="small" @selection-change="onSel" v-loading="loading || releasesLoading" max-height="560" empty-text="该版本暂无候选用例">
        <el-table-column type="selection" width="44" :selectable="() => !dispatching" />
        <el-table-column label="风险分" width="90" sortable :sort-method="(a,b)=>a.risk_score-b.risk_score">
          <template #default="{ row }"><b :style="{color: scoreColor(row.risk_score)}">{{ row.risk_score }}</b></template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="70" />
        <el-table-column prop="title" label="用例" show-overflow-tooltip />
        <el-table-column label="命中信号">
          <template #default="{ row }">
            <el-tag v-for="(v,k) in row.signals" :key="k" size="small" class="sig">{{ sigLabel(k) }}+{{ v }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { ref, nextTick, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { rtsCandidates, rtsAnalyze, rtsRecommendation, enqueueCases, listReleases, listMyDevices, pollAiJob } from '@/api'
import { useAppStore } from '@/store/app'

const projects = ref([]); const releases = ref([]); const devices = ref([])
const projectId = ref(null); const releaseId = ref(null); const runner = ref('auto')
const candidates = ref([]); const checked = ref([]); const reco = ref(null)
const inReleaseCount = ref(0)
const loading = ref(false); const analyzing = ref(false); const dispatching = ref(false)
const releasesLoading = ref(false); const loadError = ref('')
let projectVersion = 0; let releaseVersion = 0; let disposed = false; let analysisController
const tableRef = ref(null)

const riskLabel = (r) => ({ high: '高', medium: '中', low: '低' }[r] || r)
const sigLabel = (k) => ({ in_release: '属本版本', fail_rate: '失败率', priority: '优先级', flaky: 'flaky', had_bug: '曾出bug', stale: '陈旧' }[k] || k)
const scoreColor = (s) => s >= 70 ? '#d03b3b' : s >= 50 ? '#fab219' : '#909399'

async function init() {
  try {
    const app = useAppStore()
    projects.value = await app.fetchProjects()
    if (disposed) return
    projectId.value = app.resolveProjectId(projects.value)
    if (projectId.value) await onProject()
    if (!disposed) try { devices.value = await listMyDevices() } catch { devices.value = [] }
  } catch { if (!disposed) loadError.value = '项目加载失败' }
}
async function onProject() {
  if (!projectId.value) return init()
  const version = ++projectVersion
  ++releaseVersion
  releases.value = []; releaseId.value = null; candidates.value = []; reco.value = null
  checked.value = []; inReleaseCount.value = 0; loading.value = false; loadError.value = ''
  releasesLoading.value = true
  useAppStore().setLastProject(projectId.value)   // 记住选择，跨页/下次进页复原
  try {
    const data = await listReleases({ project_id: projectId.value })
    if (version === projectVersion && !disposed) releases.value = data.items || []
  } catch { if (version === projectVersion && !disposed) loadError.value = '版本加载失败' }
  finally { if (version === projectVersion && !disposed) releasesLoading.value = false }
}
async function onRelease() {
  const version = ++releaseVersion, id = releaseId.value
  candidates.value = []; reco.value = null; checked.value = []
  inReleaseCount.value = 0; loadError.value = ''; loading.value = false
  if (!id) return
  loading.value = true
  try {
    const d = await rtsCandidates(id)
    if (version !== releaseVersion || disposed) return
    const r = await rtsRecommendation(id)
    if (version !== releaseVersion || disposed) return
    candidates.value = d.items || []; inReleaseCount.value = d.in_release_count || 0
    reco.value = r.exists ? r : null
    // 默认勾选高分候选（risk_score≥50），数据渲染后用 table ref 逐行 toggle
    await nextTick()
    if (version !== releaseVersion || disposed) return
    candidates.value.forEach((row) => {
      if (row.risk_score >= 50) tableRef.value?.toggleRowSelection(row, true)
    })
  } catch { if (version === releaseVersion && !disposed) loadError.value = '候选用例或推荐加载失败' }
  finally { if (version === releaseVersion && !disposed) loading.value = false }
}
function onSel(rows) { checked.value = rows }
async function runAnalyze() {
  if (analyzing.value || dispatching.value || loading.value || loadError.value || !releaseId.value || !candidates.value.length) return
  const id = releaseId.value
  analyzing.value = true
  analysisController = new AbortController()
  try {
    const { job_id } = await rtsAnalyze({ project_id: projectId.value, release_id: id })
    if (disposed) return
    await pollAiJob(job_id, { signal: analysisController.signal })
    if (disposed) return
    const r = await rtsRecommendation(id)
    if (disposed) return
    reco.value = r.exists ? r : null
    ElMessage.success('推荐已生成')
  } catch (e) { if (!disposed) ElMessage.error('生成失败:' + (e?.message || '请重试')) } finally { analyzing.value = false }
}
async function dispatch() {
  if (dispatching.value || analyzing.value || loading.value || loadError.value || !releaseId.value || !checked.value.length) return
  const ids = checked.value.map(r => r.case_id), pid = projectId.value, rid = releaseId.value, target = runner.value
  const version = releases.value.find(r => r.id === rid)?.version || `#${rid}`
  dispatching.value = true
  try {
    await ElMessageBox.confirm(`将版本 ${version} 的 ${ids.length} 条用例下发到${target === 'auto' ? '自动挑选的运行机' : target}？`, '确认下发回归', { confirmButtonText: '确认下发', cancelButtonText: '取消', type: 'warning' })
    if (disposed) return
    await enqueueCases(pid, target, ids, rid)
    if (!disposed) ElMessage.success(`已下发 ${ids.length} 条，结果计入该版本质量卡`)
  } catch (e) { /* 拦截器已提示 */ } finally { dispatching.value = false }
}
onBeforeUnmount(() => { disposed = true; ++projectVersion; ++releaseVersion; analysisController?.abort() })
init()
</script>

<style scoped>
.rts-page { padding: 4px; }
.reco { margin: 8px 0; padding: 12px 14px; border-radius: 8px; background: #f7f9fc; border-left: 4px solid #909399; }
.reco.risk-high { border-left-color: #d03b3b; } .reco.risk-medium { border-left-color: #fab219; } .reco.risk-low { border-left-color: #0ca30c; }
.reco-h { font-size: 14px; } .reco-s { color: #303133; margin: 6px 0; } .reco-r { color: #606266; font-size: 13px; }
.reco-f { margin-top: 6px; } .fp { color: #d03b3b; font-size: 12px; margin-right: 12px; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; color: #606266; }
.sig { margin: 0 4px 2px 0; }
.reco { overflow-wrap: anywhere; }
</style>
