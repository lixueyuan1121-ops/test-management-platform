<template>
  <div class="workload">
    <el-card>
      <template #header>
        <div class="header">
          <span>工作量统计</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="range" type="daterange" range-separator="-" start-placeholder="开始" end-placeholder="结束"
              value-format="YYYY-MM-DD" size="small" style="width:240px" @change="load" />
          </div>
        </div>
      </template>
      <el-row :gutter="12" v-loading="loading">
        <el-col :span="8"><div class="stat"><div class="num">{{ data.total_tasks }}</div><div class="lbl">总任务数(条)</div></div></el-col>
        <el-col :span="8"><div class="stat"><div class="num green">{{ data.total_online }}</div><div class="lbl">累计上线数</div></div></el-col>
        <el-col :span="8"><div class="stat"><div class="num">{{ data.members?.length || 0 }}</div><div class="lbl">参与人数</div></div></el-col>
      </el-row>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header><span>成员工作量对比</span></template>
      <div ref="barEl" style="height:320px"></div>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header><span>每日工作量趋势</span></template>
      <div ref="lineEl" style="height:320px"></div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { listProjects, workloadStats } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const projects = ref([])
const pid = ref(null)
const range = ref([])
const data = reactive({ total_tasks: 0, total_online: 0, members: [], daily: [] })
const loading = ref(false)
const barEl = ref(null)
const lineEl = ref(null)
let barChart = null, lineChart = null

onMounted(async () => {
  const today = new Date()
  const start = new Date(today.getTime() - 13 * 86400000)
  range.value = [start.toISOString().slice(0, 10), today.toISOString().slice(0, 10)]
  projects.value = await listProjects()
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await load() }
  await nextTick()
  barChart = echarts.init(barEl.value)
  lineChart = echarts.init(lineEl.value)
  render()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); barChart?.dispose(); lineChart?.dispose() })
function onResize() { barChart?.resize(); lineChart?.resize() }

async function load() {
  if (!pid.value || !range.value?.length) return
  setLastProjectId(pid.value)
  loading.value = true
  try {
    const d = await workloadStats(pid.value, range.value[0], range.value[1])
    Object.assign(data, d)
    render()
  } finally { loading.value = false }
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
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.stat { text-align: center; padding: 8px 0; }
.stat .num { font-size: 22px; font-weight: 600; color: #1f2d3d; }
.stat .num.green { color: #67c23a; }
.stat .lbl { color: #999; font-size: 12px; margin-top: 4px; }
</style>
