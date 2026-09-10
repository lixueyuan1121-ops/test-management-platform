<template>
  <WorkspacePage title="作战大屏" class="wr">
    <template #actions>
      <div class="wr-clock">
        {{ clock }}
        <div class="wr-date">{{ dateLine }}</div>
      </div>
      <el-button :icon="Refresh" circle title="刷新大屏" aria-label="刷新大屏" :loading="loading" @click="load" />
    </template>
    <el-alert v-if="failedSources.length" :title="`${failedSources.join('、')}更新失败`" description="有历史数据的区域保留上次结果；没有成功加载过的区域显示为未获取。" type="warning" :closable="false" show-icon />

    <div class="wr-kpis">
      <div class="wr-kpi">
        <div class="wr-num">{{ loaded.funnel ? executedCount : '—' }}</div>
        <div class="wr-lbl">近30天执行用例</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num green">{{ loaded.funnel ? passRate : '—' }}<span class="wr-u">%</span></div>
        <div class="wr-lbl">执行通过率</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num blue">{{ loaded.devices ? devs.online_devices : '—' }}<span class="wr-u">/ {{ loaded.devices ? devs.total_devices : '—' }}</span></div>
        <div class="wr-lbl">在线设备</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num" :class="cal.streak ? 'green' : 'amber'">{{ loaded.calendar ? cal.streak : '—' }}</div>
        <div class="wr-lbl">防线连续值守(天)</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num red">{{ loaded.funnel ? funnel.bugs_found : '—' }}</div>
        <div class="wr-lbl">近30天揪出真Bug</div>
      </div>
    </div>

    <!-- 中部：漏斗 + 设备编队 -->
    <div class="wr-mid">
      <div class="wr-panel">
        <h2 class="wr-ph">价值转化 <span>更新于 {{ updated.funnel }}</span></h2>
        <div v-if="!loaded.funnel" class="wr-empty">{{ loading ? '加载中' : '尚未获取漏斗数据' }}</div>
        <div v-else-if="!funnelGroups.length" class="wr-empty">暂无漏斗数据</div>
        <div class="wr-funnel">
          <template v-for="g in funnelGroups" :key="g.key">
            <div class="wr-funnel-cap">{{ g.title }}</div>
            <div v-for="s in g.steps" :key="s.stage" class="wr-step"
                 :style="{ width: s.width, background: s.color }">
              <span class="wr-step-n">{{ s.count }}</span>
              <span class="wr-step-l">{{ s.label }}</span>
            </div>
          </template>
        </div>
      </div>
      <div class="wr-panel">
        <h2 class="wr-ph">执行设备 <span>更新于 {{ updated.devices }}</span></h2>
        <div class="wr-fleet">
          <div v-for="d in devs.devices.slice(0, 8)" :key="d.id" class="wr-dev"
               :class="{ off: !d.online, busy: d.run_counts.running > 0 }">
            <span class="wr-light" :class="d.online ? 'on' : ''"></span>
            <span class="wr-dev-name">{{ d.name }}</span>
            <span v-if="d.run_counts.running" class="wr-dev-run">{{ d.run_counts.running }} 执行中</span>
            <span v-else class="wr-dev-idle">{{ d.online ? '待命' : '离线' }}</span>
          </div>
          <div v-if="!devs.devices.length" class="wr-empty">{{ loaded.devices ? '暂无注册设备' : (loading ? '加载中' : '尚未获取设备数据') }}</div>
        </div>
      </div>
    </div>

    <!-- 底部：防线日历 + 今日活动 -->
    <div class="wr-bottom">
      <div class="wr-panel">
        <h2 class="wr-ph">回归防线 <span>更新于 {{ updated.calendar }}</span></h2>
        <div v-if="!loaded.calendar" class="wr-empty">{{ loading ? '加载中' : '尚未获取回归数据' }}</div>
        <p v-else class="wr-caption">累计值守 {{ cal.total_guard_days }} 天</p>
        <div class="wr-wall">
          <span v-for="d in cal.days" :key="d.date" class="wr-cell" :class="`c-${d.state}`"
                :title="`${d.date} ${d.runs}批`"/>
        </div>
      </div>
      <div class="wr-panel">
        <h2 class="wr-ph">执行中任务 <span>更新于 {{ updated.devices }}</span></h2>
        <div class="wr-live">
          <template v-if="liveRuns.length">
            <div v-for="r in liveRuns" :key="r.run_id" class="wr-live-row">
              <span class="wr-live-pip"></span>
              <span class="wr-live-dev">{{ r.dev }}</span>
              <span class="wr-live-title">{{ r.title }}</span>
              <span class="wr-live-t">{{ fmtElapsed(r.started_at) }}</span>
            </div>
          </template>
          <div v-else class="wr-empty">{{ loaded.devices ? '当前无执行中任务' : (loading ? '加载中' : '尚未获取执行数据') }}</div>
        </div>
      </div>
    </div>

    <div class="wr-foot">每 {{ POLL_SEC }} 秒自动刷新 · 最近完整更新 {{ lastAt }}</div>
  </WorkspacePage>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getDeviceOverview, aiFunnel, defenseCalendar } from '@/api'
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'

const POLL_SEC = 30
const devs = ref({ total_devices: 0, online_devices: 0, running_devices: 0, devices: [] })
const funnel = ref({ funnel: [], bugs_found: 0, selector_pending: 0, saved_hours: 0 })
const cal = ref({ days: [], streak: 0, total_guard_days: 0 })
const lastAt = ref('—')
const loading = ref(false)
const failedSources = ref([])
const loaded = ref({ devices:false, funnel:false, calendar:false })
const updated = ref({ devices:'—', funnel:'—', calendar:'—' })

const now = ref(Date.now())
let clockTimer = null
let pollTimer = null

const clock = computed(() => new Date(now.value).toLocaleTimeString('zh-CN', { hour12: false }))
const dateLine = computed(() =>
  new Date(now.value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' }))

const STEP_COLORS = ['#2a78d6', '#3f8fc9', '#31a3ab', '#19b394', '#00b386']
// 价值漏斗分两组各自归一:AI 产出链(生成→采纳→自动化)与执行链(已执行→通过)口径不同,
// 混在一起按全局最大值算宽会让「已执行」(常>AI生成)凸出、破坏漏斗形态。分组后每组内部单调收窄。
const GEN_STAGES = ['generated', 'adopted', 'automatable']
const funnelGroups = computed(() => {
  const f = funnel.value.funnel || []
  if (!f.length) return []
  const build = (steps) => {
    const max = Math.max(1, ...steps.map((s) => s.count || 0))  // 按本组最大值归一
    return steps.map((s) => ({
      ...s,
      color: STEP_COLORS[f.indexOf(s)] || STEP_COLORS[STEP_COLORS.length - 1],
      width: Math.max(22, Math.round(((s.count || 0) / max) * 100)) + '%',
    }))
  }
  const gen = f.filter((s) => GEN_STAGES.includes(s.stage))
  const exec = f.filter((s) => !GEN_STAGES.includes(s.stage))
  const groups = []
  if (gen.length) groups.push({ key: 'gen', title: '// 产出', steps: build(gen) })
  if (exec.length) groups.push({ key: 'exec', title: '// 执行', steps: build(exec) })
  return groups
})
const executedCount = computed(() => funnel.value.funnel.find(stage => stage.stage === 'executed')?.count ?? 0)
const passRate = computed(() => {
  const ex = executedCount.value
  const ps = funnel.value.funnel.find(stage => stage.stage === 'passed')?.count || 0
  return ex ? Math.round((ps / ex) * 100) : '—'
})
// 全平台执行中任务流（从设备 overview 的 active_runs 汇总）
const liveRuns = computed(() =>
  devs.value.devices.flatMap((d) =>
    d.active_runs.map((r) => ({ ...r, dev: d.name }))).slice(0, 8))

function fmtElapsed(startedAt) {
  if (!startedAt) return '—'
  const s = Math.max(0, Math.floor((now.value - new Date(startedAt).getTime()) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

async function load() {
  if (loading.value) return
  loading.value = true
  const [d, f, c] = await Promise.all([
    getDeviceOverview({ silent:true }).catch(() => null),
    aiFunnel(30, { silent:true }).catch(() => null),
    defenseCalendar(12, { silent:true }).catch(() => null),
  ])
  const timestamp = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const failures = []
  for (const [key, label, value, target] of [['devices', '设备', d, devs], ['funnel', '漏斗', f, funnel], ['calendar', '回归防线', c, cal]]) {
    if (value) { target.value = value; loaded.value[key] = true; updated.value[key] = timestamp }
    else failures.push(label)
  }
  failedSources.value = failures
  if (!failures.length) lastAt.value = timestamp
  loading.value = false
}

onMounted(() => {
  load()
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  pollTimer = setInterval(load, POLL_SEC * 1000)
})
onUnmounted(() => {
  clearInterval(clockTimer)
  clearInterval(pollTimer)
})
</script>

<style scoped>
.wr {
  color: var(--el-text-color-primary); font-family: system-ui,-apple-system,'Segoe UI',sans-serif;
}
.wr-clock { font-family:ui-monospace,monospace; font-size:16px; font-weight:600; text-align:right; line-height:1.3; font-variant-numeric:tabular-nums; }
.wr-date { font-size:12px; color:#687181; margin-top:4px; letter-spacing:0; }

.wr-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin: 22px 0; }
.wr-kpi { background:#fff; border:1px solid #e0e4ea; border-radius:8px; padding:16px; min-width:0; }
.wr-num { font-family:ui-monospace,monospace; font-size:28px; font-weight:700; line-height:1.2; overflow-wrap:anywhere; }
.wr-num.green { color:#16845b; }
.wr-num.blue { color:var(--el-color-primary); }
.wr-num.red { color:#d34049; }
.wr-num.amber { color:#a36a18; }
.wr-u { font-size: 16px; color: #5f6b7a; margin-left: 3px; }
.wr-lbl { font-size: 12px; color: #7d8a9b; margin-top: 8px; }

.wr-mid, .wr-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
.wr-panel { border-top:1px solid #dce1e8; padding:16px 0; min-height:180px; min-width:0; }
.wr-ph { font-size:15px; letter-spacing:0; margin:0 0 16px; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }
.wr-ph span { font-size:12px; font-weight:400; color:#687181; }
.wr-caption { font-size:13px; color:#687181; }

.wr-funnel { display: flex; flex-direction: column; gap: 5px; }
.wr-funnel-cap { font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: 0;
  color: #5f6b7a; margin-top: 6px; margin-bottom: 1px; }
.wr-funnel-cap:first-child { margin-top: 0; }
.wr-step { min-height: 34px; border-radius: 5px; padding: 4px 12px; display: flex; align-items: center; gap: 10px; color: #fff;
  box-sizing:border-box; min-width:100px; max-width:100%; flex-wrap:wrap; overflow-wrap:anywhere;
  clip-path: polygon(0 0, 100% 0, calc(100% - 12px) 100%, 0 100%); transition: width .5s ease; }
.wr-step-n { font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 800; }
.wr-step-l { font-size: 11px; opacity: .9; }

.wr-fleet { display: flex; flex-direction: column; gap: 8px; }
.wr-dev { display:flex; align-items:center; gap:10px; padding:10px 12px; background:#fff; border:1px solid #e0e4ea; border-radius:6px; font-size:13px; }
.wr-dev.off { border-style:dashed; }
.wr-dev.busy { border: 1px solid #35b6ff44; }
.wr-light { width: 8px; height: 8px; border-radius: 50%; background: #55606e; flex: none; }
.wr-light.on { background:#16845b; }
.wr-dev-name { color:#303743; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.wr-dev-run { margin-left:auto; font-size:12px; color:var(--el-color-primary); flex:none; }
.wr-dev-idle { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #5f6b7a; flex: none; }

.wr-wall { display: grid; grid-template-rows: repeat(7, 11px); grid-auto-flow: column; grid-auto-columns: 11px; gap: 3px; overflow-x: auto; }
.wr-cell { width: 11px; height: 11px; border-radius: 2px; }
.c-green { background: #00b386; }
.c-red { background: #e5565f; }
.c-gray { background:#dfe3e9; }

.wr-live { display: flex; flex-direction: column; gap: 8px; }
.wr-live-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.wr-live-pip { width: 6px; height: 6px; border-radius: 50%; background: #35b6ff; flex: none; animation: wrbreathe 1.2s ease-in-out infinite; }
.wr-live-dev { color:#687181; max-width:28%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.wr-live-title { color:#303743; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.wr-live-t { margin-left:auto; font-family:ui-monospace,monospace; color:var(--el-color-primary); flex:none; font-variant-numeric:tabular-nums; }

.wr-empty { color: #5f6b7a; font-size: 12px; font-family: 'JetBrains Mono', monospace; padding: 20px 0; text-align: center; }
.wr-foot { text-align:center; font-size:12px; color:#687181; letter-spacing:0; margin-top:8px; }

@keyframes wrbreathe { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@media (max-width: 1000px) { .wr-kpis { grid-template-columns: repeat(2, 1fr); } .wr-mid, .wr-bottom { grid-template-columns: 1fr; } }
@media (max-width: 400px) { .wr-kpis { grid-template-columns:1fr; } }
@media (prefers-reduced-motion: reduce) { .wr-light.on, .wr-live-pip { animation: none; } }
</style>
