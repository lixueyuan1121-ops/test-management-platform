<template>
  <div class="perf-report">
    <div class="toolbar">
      <div class="title">性能报告 <span class="sub">{{ headSub }}</span></div>
      <div class="ops">
        <el-select v-model="currentSet" placeholder="选报告集" style="width:200px" @change="onSetChange">
          <el-option label="（全部采集）" :value="0" />
          <el-option v-for="s in sets" :key="s.id" :label="`${s.name}（${s.completed_count}）`" :value="s.id" />
        </el-select>
        <el-button size="small" :disabled="!currentSet" @click="onRename">重命名</el-button>
        <el-button size="small" :disabled="!currentSet" @click="openThresholds">性能红线</el-button>
        <el-select v-model="scenarioFilter" placeholder="全部场景" clearable size="small" style="width:130px" @change="load">
          <el-option v-for="s in scenarioOptions" :key="s" :label="s" :value="s" />
        </el-select>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && !groups.length" :description="currentSet ? '该报告集下暂无已完成的采集' : '暂无性能数据，先在「任务下发」建报告集并采集'" />

    <template v-else>
      <div v-if="verdict" class="verdict" :class="verdict.tone">{{ verdict.text }}</div>

      <div v-if="kpis.length" class="kpi-row">
        <div v-for="(k, i) in kpis" :key="i" class="kpi-card" :class="k.rate">
          <div class="kpi-scene">{{ k.scenario }}</div>
          <div class="kpi-label">{{ k.label }}</div>
          <div v-if="k.single" class="kpi-val">{{ fmtVal(k.value, k.unit) }}</div>
          <div v-else class="kpi-val">{{ fmtVal(k.aVal, k.unit) }} <span class="kpi-vs">vs</span> {{ fmtVal(k.bVal, k.unit) }}</div>
          <div v-if="!k.single" class="kpi-delta">{{ k.deltaText }}</div>
        </div>
      </div>

      <el-card v-for="g in groups" :key="g.scenario" class="scene-card" shadow="never">
        <template #header>
          <div class="scene-head">
            <span class="scene-name">{{ g.scenario }}</span>
            <span class="scene-objs">{{ g.objects.map(o => o.variant).join('  ·  ') }}</span>
            <el-select v-model="metricByScene[g.scenario]" size="small" style="width:130px" @change="renderCharts">
              <el-option v-for="m in metricOptions" :key="m.key" :label="m.label" :value="m.key" />
            </el-select>
          </div>
        </template>
        <el-table :data="dimRows(g)" size="small" class="cmp-table" :show-overflow-tooltip="true">
          <el-table-column prop="label" label="指标" width="120" />
          <el-table-column v-for="o in g.objects" :key="o.variant" :label="o.variant" min-width="100">
            <template #default="{ row }">{{ row.vals[o.variant] }}</template>
          </el-table-column>
        </el-table>
        <div :ref="(el) => setChartRef(g.scenario, el)" class="chart"></div>
      </el-card>
    </template>

    <!-- 性能红线:超线的采集完成即推飞书告警 -->
    <el-dialog v-model="thVisible" title="性能红线（阈值告警）" width="560px">
      <el-alert type="info" :closable="false" show-icon class="th-tip"
        title="给本报告集设红线：采集完成时逐指标比对，超线自动推飞书告警（需配置通知通道）。留空=不检查该指标。" />
      <el-table :data="thRows" size="small" border>
        <el-table-column prop="label" label="指标" width="110" />
        <el-table-column label="单位" width="60" align="center">
          <template #default="{ row }">{{ row.unit || '—' }}</template>
        </el-table-column>
        <el-table-column label="红线" min-width="220">
          <template #default="{ row }">
            <span class="th-op">{{ row.lowGood ? '不得超过' : '不得低于' }}</span>
            <el-input-number v-model="row.limit" :min="0" :controls="false" size="small"
                             style="width:120px" placeholder="留空不检查" />
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="thVisible = false">取消</el-button>
        <el-button type="primary" :loading="thSaving" @click="saveThresholds">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { perfReport, listPerfSets, renamePerfSet, setPerfThresholds } from '@/api'
import { groupByScenario, buildVerdict, pickKpis, DIMENSIONS, fmtVal } from '@/utils/perf-report-logic'

const loading = ref(false)
const groups = ref([])
const verdict = ref(null)
const kpis = ref([])
const scenarioFilter = ref('')
const scenarioOptions = ref([])
const sets = ref([])
const currentSet = ref(0)
const metricByScene = reactive({})
const chartRefs = {}
const charts = {}

const currentSetName = computed(() => (currentSet.value ? (sets.value.find((s) => s.id === currentSet.value)?.name || '') : ''))
const headSub = computed(() => (currentSet.value ? `报告集：${currentSetName.value}` : 'nami-perfdog · 全部采集'))

const metricOptions = [
  { key: 'cpuPct', label: 'CPU %' },
  { key: 'memMB', label: '内存 MB' },
  { key: 'fps', label: 'FPS' },
  { key: 'gpuPct', label: 'GPU %' },
  { key: 'jsHeapMB', label: 'JS 堆 MB' },
  { key: 'netRttMs', label: '网络 RTT' },
  { key: 'pingMs', label: 'Ping ms' },
]

function setChartRef(scene, el) { if (el) chartRefs[scene] = el }
function disposeCharts() { Object.keys(charts).forEach((k) => { charts[k].dispose(); delete charts[k] }) }

async function loadSets() { try { sets.value = await listPerfSets() } catch { sets.value = [] } }

async function load() {
  loading.value = true
  disposeCharts()   // 切集/刷新前清旧图，避免残留
  try {
    const params = {}
    if (scenarioFilter.value) params.scenario = scenarioFilter.value
    if (currentSet.value) params.report_set_id = currentSet.value
    const payload = await perfReport(params)
    const gs = groupByScenario(payload)
    groups.value = gs
    verdict.value = buildVerdict(gs)
    kpis.value = pickKpis(gs)
    scenarioOptions.value = [...new Set(payload.map((p) => p.meta.scenario))]
    gs.forEach((g) => { if (!metricByScene[g.scenario]) metricByScene[g.scenario] = 'cpuPct' })
    await nextTick()
    renderCharts()
  } finally {
    loading.value = false
  }
}

function onSetChange() { scenarioFilter.value = ''; load() }

async function onRename() {
  const cur = sets.value.find((s) => s.id === currentSet.value)
  try {
    const { value } = await ElMessageBox.prompt('新名称', '重命名报告集', { inputValue: cur?.name || '', confirmButtonText: '保存', cancelButtonText: '取消' })
    await renamePerfSet(currentSet.value, (value || cur.name).trim())
    await loadSets(); ElMessage.success('已重命名')
  } catch { /* 取消 */ }
}

function dimRows(g) {
  return DIMENSIONS.filter((d) => d.key !== 'sampleCount').map((d) => {
    const vals = {}
    g.objects.forEach((o) => { vals[o.variant] = fmtVal(d.get(o.meta), d.unit) })
    return { label: d.label, vals }
  })
}

// ---- 性能红线(阈值告警):后端 perf_guard.METRIC_DEFS 的可配指标子集 ----
const TH_KEYS = ['ttftMs', 'cpuPeak', 'cpuAvg', 'memDelta', 'memPeak', 'memTrendMB', 'gpuPeak', 'fpsAvg', 'wsRtt', 'ping']
const thVisible = ref(false)
const thRows = ref([])
const thSaving = ref(false)

function openThresholds() {
  const cur = sets.value.find((s) => s.id === currentSet.value)
  const saved = cur?.thresholds || {}
  thRows.value = DIMENSIONS.filter((d) => TH_KEYS.includes(d.key)).map((d) => {
    const rule = saved[d.key] || {}
    return {
      key: d.key, label: d.label, unit: d.unit, lowGood: d.lowGood !== false,
      limit: d.lowGood !== false ? (rule.max ?? undefined) : (rule.min ?? undefined),
    }
  })
  thVisible.value = true
}

async function saveThresholds() {
  const payload = {}
  for (const r of thRows.value) {
    if (r.limit === undefined || r.limit === null || r.limit === '') continue
    payload[r.key] = r.lowGood ? { max: Number(r.limit) } : { min: Number(r.limit) }
  }
  thSaving.value = true
  try {
    await setPerfThresholds(currentSet.value, payload)
    ElMessage.success(Object.keys(payload).length ? '红线已保存' : '红线已清空')
    thVisible.value = false
    await loadSets()
  } catch { /* 拦截器已提示 */ } finally { thSaving.value = false }
}

function renderCharts() {
  groups.value.forEach((g) => {
    const el = chartRefs[g.scenario]
    if (!el) return
    const metric = metricByScene[g.scenario] || 'cpuPct'
    let c = charts[g.scenario]
    if (!c) { c = echarts.init(el); charts[g.scenario] = c }
    const series = g.objects.map((o) => ({
      name: o.variant,
      type: 'line',
      showSymbol: false,
      smooth: true,
      connectNulls: true,
      data: (o.samples || []).filter((s) => s.metric === metric).map((s) => [+(s.t / 1000).toFixed(1), s.value]),
    }))
    c.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: g.objects.map((o) => o.variant), top: 0 },
      grid: { left: 48, right: 16, top: 28, bottom: 30 },
      xAxis: { type: 'value', name: '秒', axisLabel: { formatter: '{value}s' } },
      yAxis: { type: 'value', scale: true },
      series,
    }, true)
    c.resize()
  })
}

const onResize = () => Object.values(charts).forEach((c) => c.resize())
onMounted(async () => { await loadSets(); await load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  disposeCharts()
})
</script>

<style scoped>
.th-tip { margin-bottom: 10px; }
.th-op { font-size: 12px; color: #909399; margin-right: 8px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.title { font-size: 18px; font-weight: 600; }
.title .sub { font-size: 12px; color: #909399; font-weight: 400; margin-left: 8px; }
.ops { display: flex; gap: 8px; align-items: center; }
.verdict { padding: 10px 14px; border-radius: 8px; font-size: 14px; margin-bottom: 14px; border-left: 4px solid #909399; background: #f4f4f5; }
.verdict.better, .verdict.slightly-better { border-left-color: #00b386; background: rgba(0, 179, 134, .08); }
.verdict.worse { border-left-color: #f56c6c; background: rgba(245, 108, 108, .08); }
.verdict.mixed { border-left-color: #e6a23c; background: rgba(230, 162, 60, .08); }
.kpi-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.kpi-card { min-width: 150px; flex: 1; padding: 12px 14px; border-radius: 8px; background: #fff; border: 1px solid #ebeef5; border-top: 3px solid #909399; }
.kpi-card.good { border-top-color: #00b386; }
.kpi-card.mid { border-top-color: #e6a23c; }
.kpi-card.bad { border-top-color: #f56c6c; }
.kpi-scene { font-size: 12px; color: #909399; }
.kpi-label { font-size: 13px; color: #606266; margin: 2px 0 6px; }
.kpi-val { font-size: 18px; font-weight: 600; font-family: 'JetBrains Mono', ui-monospace, monospace; }
.kpi-vs { font-size: 12px; color: #c0c4cc; font-weight: 400; }
.kpi-delta { font-size: 12px; color: #909399; margin-top: 4px; }
.scene-card { margin-bottom: 16px; }
.scene-head { display: flex; align-items: center; gap: 12px; }
.scene-name { font-size: 15px; font-weight: 600; }
.scene-objs { font-size: 12px; color: #909399; flex: 1; }
.cmp-table { margin-bottom: 14px; }
.chart { width: 100%; height: 280px; }
</style>
