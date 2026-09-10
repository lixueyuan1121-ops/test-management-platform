<template>
  <div class="workload">
    <WorkspacePage title="工作量统计">
      <template #actions>
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="range" type="daterange" range-separator="-" start-placeholder="开始" end-placeholder="结束"
              value-format="YYYY-MM-DD" size="small" style="width:240px" @change="load" />
      </template>
      <el-result v-if="loadError" icon="error" title="工作量统计加载失败"><template #extra><el-button @click="load">重试统计</el-button></template></el-result>
      <el-skeleton v-else-if="loading" :rows="4" animated />
      <el-empty v-else-if="!ready" description="请选择项目和日期范围" />
      <div v-show="ready && !loading && !loadError">
      <el-row :gutter="12">
        <el-col :span="8"><div class="stat"><div class="num">{{ data.total_tasks }}</div><div class="lbl">总任务数(条)</div></div></el-col>
        <el-col :span="8"><div class="stat"><div class="num green">{{ data.total_online }}</div><div class="lbl">累计上线数</div></div></el-col>
        <el-col :span="8"><div class="stat"><div class="num">{{ data.members?.length || 0 }}</div><div class="lbl">参与人数</div></div></el-col>
      </el-row>
    <section class="stat-section">
      <h2>成员工作量对比</h2>
      <div ref="barEl" style="height:320px"></div>
    </section>

    <section class="stat-section">
      <h2>每日工作量趋势</h2>
      <div ref="lineEl" style="height:320px"></div>
    </section>
      </div>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { ref, reactive, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { workloadStats } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const range = ref([])
const data = reactive({ total_tasks: 0, total_online: 0, members: [], daily: [] })
const loading = ref(false)
const ready = ref(false), loadError = ref(false)
let version = 0, disposed = false
const barEl = ref(null)
const lineEl = ref(null)
let barChart = null, lineChart = null

onMounted(async () => {
  window.addEventListener('resize', onResize)
  const today = new Date()
  const start = new Date(today.getTime() - 13 * 86400000)
  range.value = [start.toISOString().slice(0, 10), today.toISOString().slice(0, 10)]
  try {
    projects.value = await app.fetchProjects()
    if (!disposed && projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await load() }
  } catch { if (!disposed) loadError.value = true }
})
onBeforeUnmount(() => { disposed = true; ++version; window.removeEventListener('resize', onResize); barChart?.dispose(); lineChart?.dispose() })
function onResize() { barChart?.resize(); lineChart?.resize() }

async function load() {
  const current = ++version
  ready.value = false; loadError.value = false; loading.value = false
  if (!pid.value || !range.value?.length) return
  setLastProjectId(pid.value)
  loading.value = true
  try {
    const d = await workloadStats(pid.value, range.value[0], range.value[1])
    if (disposed || current !== version) return
    Object.assign(data, d)
    ready.value = true
  } catch { if (!disposed && current === version) loadError.value = true }
  finally {
    if (!disposed && current === version) {
      loading.value = false
      await nextTick()
      if (!disposed && current === version && ready.value) {
        if (!barChart) barChart = echarts.init(barEl.value)
        if (!lineChart) lineChart = echarts.init(lineEl.value)
        render(); onResize()
      }
    }
  }
}

function render() {
  if (!barChart || !lineChart) return
  const members = data.members || []
  barChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: members.map((m) => m.name), axisLabel: { interval: 0 } },
    yAxis: [{ type: 'value', name: '任务数' }],
    series: [
      { name: '任务数', type: 'bar', data: members.map((m) => m.task_cnt), itemStyle: { color: '#00b386' }, barMaxWidth: 40 },
      { name: '上线数', type: 'bar', data: members.map((m) => m.online_cnt), itemStyle: { color: '#67c23a' }, barMaxWidth: 40 },
    ],
  })
  const daily = data.daily || []
  lineChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['任务数', '当日上线'] },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: daily.map((d) => d.date), boundaryGap: false },
    yAxis: [{ type: 'value', name: '任务数' }, { type: 'value', name: '上线数' }],
    series: [
      { name: '任务数', type: 'line', smooth: true, data: daily.map((d) => d.task_cnt), areaStyle: { opacity: 0.15 }, itemStyle: { color: '#00b386' } },
      { name: '当日上线', type: 'line', yAxisIndex: 1, data: daily.map((d) => d.online_cnt), itemStyle: { color: '#67c23a' } },
    ],
  })
}
</script>

<style scoped>
.stat-section { padding: 20px 0; border-top: 1px solid var(--el-border-color); margin-top: 16px; }
.stat-section h2 { font-size: 16px; margin: 0 0 12px; }
@media (max-width: 700px) { .workload :deep(.el-col-8) { flex: 0 0 100%; max-width: 100%; margin-bottom: 8px; } }
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.stat { text-align: center; padding: 8px 0; }
.stat .num { font-size: 22px; font-weight: 600; color: #1f2d3d; }
.stat .num.green { color: #67c23a; }
.stat .lbl { color: #999; font-size: 12px; margin-top: 4px; }
</style>
