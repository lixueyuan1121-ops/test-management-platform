<template>
  <div class="release-notes">
    <div class="board">
      <div class="board-head">
        <div class="board-title">
          <span class="kicker">// RELEASE DASHBOARD</span>
          <span class="board-h1">发版记录</span>
        </div>
        <div class="board-tools">
          <el-select v-model="pid" placeholder="全部项目" clearable size="small" style="width:200px" @change="onProjectChange">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <el-button v-if="isAdmin" type="primary" size="small" :disabled="!pid" @click="openCreate">登记发版</el-button>
        </div>
      </div>
      <div class="stat-row">
        <div class="stat-card"><div class="stat-num">{{ stats.total_releases }}</div><div class="stat-label">发布版本总数</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.total_reqs }}</div><div class="stat-label">发布需求总数</div></div>
        <div class="stat-card"><div class="stat-num">{{ stats.this_month }}</div><div class="stat-label">本月发版</div></div>
        <div class="stat-card"><div class="stat-num sm">{{ stats.latest_date || '—' }}</div><div class="stat-label">最近发版日期</div></div>
      </div>
      <div class="chart-wrap">
        <div class="chart-title">近 12 个月发版趋势</div>
        <div ref="chartEl" class="chart" />
      </div>
    </div>

    <el-card class="list-card">
      <template #header>
        <div class="list-head">
          <span>版本列表<span v-if="!pid" class="dim"> · 请选择项目查看明细</span></span>
        </div>
      </template>
      <div v-if="pid" class="sub-tabs">
        <el-radio-group v-model="subProduct" size="small" @change="reload">
          <el-radio-button :value="''">全部</el-radio-button>
          <el-radio-button v-for="sp in subProducts" :key="sp" :value="sp">{{ sp }}</el-radio-button>
        </el-radio-group>
      </div>
      <el-table v-if="pid" :data="rows" v-loading="loading" size="small" border stripe empty-text="该项目暂无发版记录">
        <el-table-column prop="version" label="版本号" width="140" />
        <el-table-column label="子产品" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.sub_product" :type="spType(row.sub_product)" size="small">{{ row.sub_product }}</el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="isApp" label="发版渠道" width="190">
          <template #default="{ row }">
            <template v-if="row.channel && row.channel.length">
              <el-tag v-for="c in row.channel" :key="c" size="small" class="chan-tag">{{ c }}</el-tag>
            </template>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="release_date" label="发版日期" width="130" />
        <el-table-column prop="req_count" label="需求数" width="90" align="center" />
        <el-table-column label="上线内容" min-width="240">
          <template #default="{ row }"><span class="one-line">{{ plain(row.content) }}</span></template>
        </el-table-column>
        <el-table-column prop="created_by_name" label="登记人" width="100" />
        <el-table-column label="操作" :width="isAdmin ? 180 : 70" align="center">
          <template #default="{ row }">
            <el-button link type="info" @click="openDetail(row)">详情</el-button>
            <el-button v-if="isAdmin" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="isAdmin" link type="danger" @click="onDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="从上方选择一个项目查看其版本明细" :image-size="80" />
      <div v-if="pid" class="pager">
        <el-pagination
          v-model:current-page="page" v-model:page-size="pageSize" :total="total"
          :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" background
          @current-change="load" @size-change="reload"
        />
      </div>
    </el-card>

    <el-drawer v-model="detail.visible" :title="`发版详情 · ${detail.row?.version || ''}`" size="560px">
      <div v-if="detail.row" class="detail">
        <p class="d-row"><span class="d-k">版本号</span> {{ detail.row.version }}</p>
        <p class="d-row"><span class="d-k">子产品</span> {{ detail.row.sub_product || '—' }}</p>
        <p v-if="isApp" class="d-row"><span class="d-k">发版渠道</span> {{ (detail.row.channel && detail.row.channel.length) ? detail.row.channel.join('、') : '—' }}</p>
        <p class="d-row"><span class="d-k">发版日期</span> {{ detail.row.release_date }}</p>
        <p class="d-row"><span class="d-k">需求数</span> {{ detail.row.req_count }}</p>
        <p class="d-row"><span class="d-k">登记人</span> {{ detail.row.created_by_name || '—' }}</p>
        <p class="d-row"><span class="d-k">上线内容</span></p>
        <div v-if="detail.row.content" class="md-body" v-html="renderMarkdown(detail.row.content)" />
        <div v-else class="d-empty">（未填写）</div>
        <p class="d-row"><span class="d-k">备忘</span></p>
        <pre class="d-pre">{{ detail.row.memo || '（无）' }}</pre>
      </div>
    </el-drawer>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.id ? '编辑发版' : '登记发版'" width="640px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="版本号" required><el-input v-model="form.version" placeholder="如 v2.3.0" /></el-form-item>
        <el-form-item label="子产品">
          <el-select v-model="form.sub_product" placeholder="（未指定）" clearable style="width:100%" @change="form.channel = []">
            <el-option v-for="sp in subProducts" :key="sp" :label="sp" :value="sp" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="isApp" label="发版渠道">
          <el-select v-model="form.channel" multiple filterable allow-create default-first-option
                     placeholder="选择或手填渠道" style="width:100%">
            <el-option v-for="c in channelOptions(form.sub_product)" :key="c" :label="c" :value="c" />
          </el-select>
          <div class="form-hint">按所选子产品给出建议渠道，可多选，也可手动输入</div>
        </el-form-item>
        <el-form-item label="发版日期" required>
          <el-date-picker v-model="form.release_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item label="需求数"><el-input-number v-model="form.req_count" :min="0" :step="1" /></el-form-item>
        <el-form-item label="上线内容">
          <el-input v-model="form.content" type="textarea" :rows="6" placeholder="支持 Markdown：标题 / 列表 / 表格…" />
          <div class="form-hint">支持 Markdown，详情页会渲染展示</div>
        </el-form-item>
        <el-form-item label="备忘"><el-input v-model="form.memo" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { listReleases, releaseStats, getRelease, createRelease, updateRelease, deleteRelease } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { renderMarkdown } from '@/utils/markdown'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

// 子产品固定枚举：按项目平台类型分两套。须与后端 api/release.py 的 SUB_PRODUCTS_BY_TYPE 保持一致。
const SUB_PRODUCTS_PC = ['纳米Work云端版', '纳米Work桌面版', '360安全龙虾云端版', '360安全龙虾WSL']
const SUB_PRODUCTS_APP = ['360安全龙虾Android端', '360安全龙虾iOS端', '纳米Work Android端', '纳米Work iOS端']
// 发版渠道候选（仅 APP 端）：按子产品所属平台(iOS/Android)给建议值，登记时支持手填。
const CHANNELS_IOS = ['App Store', '内测']
const CHANNELS_ANDROID = ['官网', '自升级', '全渠道']

const auth = useAuthStore()
const app = useAppStore()
const isAdmin = auth.isPlatformAdmin

const projects = ref([])
const pid = ref(null)
const subProduct = ref('')
const rows = ref([])

// 当前项目平台类型 → 决定子产品枚举、是否显示渠道列（从已缓存的 projects 里按 pid 取）
const currentPlatform = computed(() => projects.value.find((p) => p.id === pid.value)?.platform_type || '')
const isApp = computed(() => currentPlatform.value === 'app')
const subProducts = computed(() => (isApp.value ? SUB_PRODUCTS_APP : SUB_PRODUCTS_PC))
// 渠道候选随所选子产品的平台(iOS/Android)联动；无法判断时给全部候选。手填仍可任意输入。
function channelOptions(sub) {
  if (sub && sub.includes('iOS')) return CHANNELS_IOS
  if (sub && sub.includes('Android')) return CHANNELS_ANDROID
  return [...CHANNELS_IOS, ...CHANNELS_ANDROID]
}
const loading = ref(false)
const stats = reactive({ total_releases: 0, total_reqs: 0, this_month: 0, latest_date: null, trend: [] })

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const chartEl = ref(null)
let chart = null

function plain(md) {
  if (!md) return '—'
  return String(md).replace(/[#*`>_-]/g, '').replace(/\s+/g, ' ').trim().slice(0, 120)
}

// 子产品标签配色：el-tag 预设色，同品牌成对（纳米=冷色 success/primary、360龙虾=暖色 warning/danger）。
// PC/APP 各 4 个；同一项目只展示其中一套，故颜色在两套间复用不冲突。
function spType(sp) {
  return {
    '纳米Work云端版': 'success', '纳米Work桌面版': 'primary',
    '360安全龙虾云端版': 'warning', '360安全龙虾WSL': 'danger',
    '纳米Work Android端': 'success', '纳米Work iOS端': 'primary',
    '360安全龙虾Android端': 'warning', '360安全龙虾iOS端': 'danger',
  }[sp] || 'info'
}

onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { projects.value = [] }
  pid.value = pickDefaultProjectId(projects.value)
  await nextTick()
  chart = echarts.init(chartEl.value)
  window.addEventListener('resize', onResize)
  await loadStats()
  if (pid.value) await reload()
})
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
function onResize() { chart?.resize() }

async function onProjectChange() {
  if (pid.value) setLastProjectId(pid.value)
  subProduct.value = ''
  await loadStats()
  await reload()
}

async function loadStats() {
  const s = await releaseStats(pid.value || undefined)
  Object.assign(stats, s)
  renderChart()
}

function renderChart() {
  if (!chart) return
  const months = stats.trend.map((t) => t.month.slice(2))
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['版本数', '需求数'], textStyle: { color: '#5a6b7b' }, top: 0 },
    grid: { left: 40, right: 40, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: months, axisLabel: { color: '#90a4ae' } },
    yAxis: [
      { type: 'value', name: '版本', axisLabel: { color: '#90a4ae' }, splitLine: { lineStyle: { color: '#eef1f5' } } },
      { type: 'value', name: '需求', axisLabel: { color: '#90a4ae' }, splitLine: { show: false } },
    ],
    series: [
      { name: '版本数', type: 'bar', data: stats.trend.map((t) => t.releases), barWidth: '46%',
        itemStyle: { color: '#00b386', borderRadius: [3, 3, 0, 0] } },
      { name: '需求数', type: 'line', yAxisIndex: 1, smooth: true, data: stats.trend.map((t) => t.reqs),
        lineStyle: { color: '#3b9ad9', width: 2 }, itemStyle: { color: '#3b9ad9' } },
    ],
  })
}

async function reload() { page.value = 1; await load() }
async function load() {
  if (!pid.value) { rows.value = []; total.value = 0; return }
  loading.value = true
  try {
    const { items, total: t } = await listReleases({
      project_id: pid.value, sub_product: subProduct.value || undefined,
      limit: pageSize.value, offset: (page.value - 1) * pageSize.value,
    })
    rows.value = items || []
    total.value = t || 0
  } finally { loading.value = false }
}

const detail = reactive({ visible: false, row: null })
async function openDetail(row) {
  detail.row = { ...row }
  detail.visible = true
  try { detail.row = await getRelease(row.id) } catch { /* handled */ }
}

const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ version: '', sub_product: '', channel: [], release_date: '', req_count: 0, content: '', memo: '' })
function openCreate() {
  dialog.id = null
  Object.assign(form, { version: '', sub_product: subProduct.value || '', channel: [], release_date: new Date().toISOString().slice(0, 10), req_count: 0, content: '', memo: '' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    version: row.version, sub_product: row.sub_product || '', channel: Array.isArray(row.channel) ? [...row.channel] : [],
    release_date: row.release_date, req_count: row.req_count || 0,
    content: row.content || '', memo: row.memo || '',
  })
  dialog.visible = true
}
async function submit() {
  if (!form.version.trim() || !form.release_date) { ElMessage.warning('版本号与发版日期必填'); return }
  dialog.saving = true
  try {
    if (dialog.id) await updateRelease(dialog.id, { ...form })
    else await createRelease({ ...form, project_id: pid.value })
    ElMessage.success('已保存')
    dialog.visible = false
    await loadStats()
    await load()
  } finally { dialog.saving = false }
}
async function onDel(row) {
  try { await ElMessageBox.confirm(`删除版本「${row.version}」？`, '确认', { type: 'warning' }) } catch { return }
  await deleteRelease(row.id)
  ElMessage.success('已删除')
  await loadStats()
  await load()
}
</script>

<style scoped>
.release-notes { display: flex; flex-direction: column; gap: 16px; }
.board { background: linear-gradient(135deg, #1f2d3d 0%, #24344a 100%); border-radius: 10px; padding: 20px 24px; color: #fff; box-shadow: 0 4px 20px rgba(31,45,61,.25); }
.board-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.board-title { display: flex; flex-direction: column; gap: 4px; }
.kicker { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #4fd8c4; letter-spacing: 1px; }
.board-h1 { font-size: 22px; font-weight: 700; letter-spacing: .5px; }
.board-tools { display: flex; gap: 10px; align-items: center; }
.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
.stat-card { background: rgba(255,255,255,.06); border: 1px solid rgba(79,216,196,.18); border-radius: 8px; padding: 16px 18px; }
.stat-num { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 30px; font-weight: 700; background: linear-gradient(90deg, #00e5a0, #3b9ad9); -webkit-background-clip: text; background-clip: text; color: transparent; line-height: 1.1; }
.stat-num.sm { font-size: 18px; }
.stat-label { margin-top: 6px; font-size: 13px; color: #bfcbd9; }
.chart-wrap { background: rgba(255,255,255,.04); border-radius: 8px; padding: 14px 12px 6px; }
.chart-title { font-size: 13px; color: #bfcbd9; margin: 0 0 6px 8px; }
.chart { height: 260px; }
.list-head { display: flex; justify-content: space-between; align-items: center; }
.sub-tabs { margin-bottom: 12px; }
.chan-tag { margin: 0 4px 2px 0; }
.dim { color: #90a4ae; font-weight: normal; font-size: 13px; }
.one-line { color: #5a6b7b; font-size: 13px; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.detail { font-size: 13px; color: #334; }
.detail .d-row { margin: 8px 0 2px; }
.detail .d-k { display: inline-block; min-width: 72px; color: #90a4ae; }
.detail .d-pre { background: #f5f7fa; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 2px 0 0; }
.detail .d-empty { color: #a8abb2; font-size: 13px; }
.form-hint { color: #90a4ae; font-size: 12px; }
.md-body { font-size: 14px; line-height: 1.7; color: #1a1d21; background: #f9fafb; border-radius: 6px; padding: 10px 12px; }
.md-body :deep(h1) { font-size: 19px; }
.md-body :deep(h2) { font-size: 16px; }
.md-body :deep(h3) { font-size: 15px; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3) { margin: 10px 0 6px; font-weight: 600; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md-body :deep(code) { background: #f2f4f7; border-radius: 3px; padding: 1px 5px; font-family: ui-monospace, monospace; font-size: 13px; }
.md-body :deep(pre) { background: #f5f7fa; border-radius: 6px; padding: 10px 12px; overflow: auto; }
.md-body :deep(table) { border-collapse: collapse; margin: 8px 0; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid #dcdfe6; padding: 5px 9px; }
.md-body :deep(a) { color: #00926e; }
</style>
