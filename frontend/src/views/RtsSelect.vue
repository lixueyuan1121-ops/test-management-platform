<template>
  <div class="rts-page">
    <el-card>
      <template #header>
        <div class="hd"><span>回归智选 · 风险驱动的回归范围</span>
          <div class="hd-r">
            <el-select v-model="projectId" size="small" style="width:180px" @change="onProject">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="releaseId" size="small" style="width:200px" clearable
                       no-data-text="该项目暂无发版记录" @change="onRelease">
              <el-option v-for="r in releases" :key="r.id" :label="`${r.version}（${r.release_date}）`" :value="r.id" />
            </el-select>
            <el-select v-model="runner" size="small" style="width:180px" placeholder="运行机"
                       :no-data-text="devices.length ? '' : '去『我的设备』注册'">
              <el-option label="⚡ 自动挑选" value="auto" />
              <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
            </el-select>
          </div>
        </div>
      </template>

      <div v-if="reco" class="reco" :class="`risk-${reco.overall_risk}`">
        <div class="reco-h">整体风险：<b>{{ riskLabel(reco.overall_risk) }}</b>　建议跑 {{ reco.recommended_count }} / {{ reco.candidate_count }} 条</div>
        <div class="reco-s">{{ reco.summary }}</div>
        <div class="reco-r">{{ reco.rationale }}</div>
        <div class="reco-f"><span v-for="(f,i) in reco.focus_points" :key="i" class="fp">⚠ {{ f }}</span></div>
      </div>

      <div v-if="releaseId" class="bar">
        <span>候选 {{ candidates.length }} 条（属本版本 {{ inReleaseCount }}）· 已选 {{ checked.length }}</span>
        <el-button size="small" :loading="analyzing" @click="runAnalyze">AI 生成推荐</el-button>
        <el-button size="small" type="primary" :loading="dispatching" :disabled="!checked.length" @click="dispatch">下发所选回归</el-button>
      </div>

      <el-table ref="tableRef" :data="candidates" size="small" @selection-change="onSel" v-loading="loading" max-height="560">
        <el-table-column type="selection" width="44" />
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
    </el-card>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { rtsCandidates, rtsAnalyze, rtsRecommendation, enqueueCases, listReleases, listMyDevices, pollAiJob } from '@/api'
import { useAppStore } from '@/store/app'

const projects = ref([]); const releases = ref([]); const devices = ref([])
const projectId = ref(null); const releaseId = ref(null); const runner = ref('auto')
const candidates = ref([]); const checked = ref([]); const reco = ref(null)
const inReleaseCount = ref(0)
const loading = ref(false); const analyzing = ref(false); const dispatching = ref(false)
const tableRef = ref(null)

const riskLabel = (r) => ({ high: '高', medium: '中', low: '低' }[r] || r)
const sigLabel = (k) => ({ in_release: '属本版本', fail_rate: '失败率', priority: '优先级', flaky: 'flaky', had_bug: '曾出bug', stale: '陈旧' }[k] || k)
const scoreColor = (s) => s >= 70 ? '#d03b3b' : s >= 50 ? '#fab219' : '#909399'

async function init() {
  projects.value = await useAppStore().fetchProjects()
  try { devices.value = await listMyDevices() } catch { devices.value = [] }
  if (projects.value.length) { projectId.value = projects.value[0].id; await onProject() }
}
async function onProject() {
  releases.value = (await listReleases({ project_id: projectId.value })).items || []
  releaseId.value = null; candidates.value = []; reco.value = null
}
async function onRelease() {
  candidates.value = []; reco.value = null; checked.value = []
  if (!releaseId.value) return
  loading.value = true
  try {
    const d = await rtsCandidates(releaseId.value)
    candidates.value = d.items || []; inReleaseCount.value = d.in_release_count || 0
    const r = await rtsRecommendation(releaseId.value)
    reco.value = r.exists ? r : null
    // 默认勾选高分候选（risk_score≥50），数据渲染后用 table ref 逐行 toggle
    await nextTick()
    candidates.value.forEach((row) => {
      if (row.risk_score >= 50) tableRef.value?.toggleRowSelection(row, true)
    })
  } finally { loading.value = false }
}
function onSel(rows) { checked.value = rows }
async function runAnalyze() {
  analyzing.value = true
  try {
    const { job_id } = await rtsAnalyze({ project_id: projectId.value, release_id: releaseId.value })
    await pollAiJob(job_id)
    const r = await rtsRecommendation(releaseId.value); reco.value = r.exists ? r : null
    ElMessage.success('推荐已生成')
  } catch (e) { ElMessage.error('生成失败:' + (e?.message || '请重试')) } finally { analyzing.value = false }
}
async function dispatch() {
  dispatching.value = true
  try {
    await enqueueCases(projectId.value, runner.value, checked.value.map((r) => r.case_id), releaseId.value)
    ElMessage.success(`已下发 ${checked.value.length} 条，结果计入该版本质量卡`)
  } catch (e) { /* 拦截器已提示 */ } finally { dispatching.value = false }
}
init()
</script>

<style scoped>
.rts-page { padding: 4px; }
.hd { display: flex; justify-content: space-between; align-items: center; }
.hd-r { display: flex; gap: 10px; }
.reco { margin: 8px 0; padding: 12px 14px; border-radius: 8px; background: #f7f9fc; border-left: 4px solid #909399; }
.reco.risk-high { border-left-color: #d03b3b; } .reco.risk-medium { border-left-color: #fab219; } .reco.risk-low { border-left-color: #0ca30c; }
.reco-h { font-size: 14px; } .reco-s { color: #303133; margin: 6px 0; } .reco-r { color: #606266; font-size: 13px; }
.reco-f { margin-top: 6px; } .fp { color: #d03b3b; font-size: 12px; margin-right: 12px; }
.bar { display: flex; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; color: #606266; }
.sig { margin: 0 4px 2px 0; }
</style>
