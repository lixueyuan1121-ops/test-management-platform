<template>
  <div class="wr">
    <!-- 顶部品牌条 + 时钟 + 四大数字 -->
    <div class="wr-head">
      <div class="wr-brand">
        <div class="wr-eyebrow">// QUALITY WAR ROOM · 质量作战大屏</div>
        <div class="wr-title">全平台质量脉搏</div>
      </div>
      <div class="wr-clock">
        {{ clock }}
        <div class="wr-date">{{ dateLine }}</div>
      </div>
    </div>

    <div class="wr-kpis">
      <div class="wr-kpi">
        <div class="wr-num">{{ funnel.funnel[3]?.count ?? 0 }}</div>
        <div class="wr-lbl">近30天执行</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num green">{{ passRate }}<span class="wr-u">%</span></div>
        <div class="wr-lbl">执行通过率</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num blue">{{ devs.online_devices }}<span class="wr-u">/ {{ devs.total_devices }}</span></div>
        <div class="wr-lbl">在线设备</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num" :class="cal.streak ? 'green' : 'amber'">{{ cal.streak }}</div>
        <div class="wr-lbl">防线连续值守(天)</div>
      </div>
      <div class="wr-kpi">
        <div class="wr-num red">{{ funnel.bugs_found }}</div>
        <div class="wr-lbl">近30天揪出真Bug</div>
      </div>
    </div>

    <!-- 中部：漏斗 + 设备编队 -->
    <div class="wr-mid">
      <div class="wr-panel">
        <div class="wr-ph">// AI VALUE FUNNEL · 价值转化</div>
        <div class="wr-funnel">
          <div v-for="(s, i) in funnel.funnel" :key="s.stage" class="wr-step"
               :style="{ width: stepWidth(i), background: STEP_COLORS[i] }">
            <span class="wr-step-n">{{ s.count }}</span>
            <span class="wr-step-l">{{ s.label }}</span>
          </div>
        </div>
      </div>
      <div class="wr-panel">
        <div class="wr-ph">// DEVICE FLEET · 执行编队</div>
        <div class="wr-fleet">
          <div v-for="d in devs.devices.slice(0, 8)" :key="d.id" class="wr-dev"
               :class="{ off: !d.online, busy: d.run_counts.running > 0 }">
            <span class="wr-light" :class="d.online ? 'on' : ''"></span>
            <span class="wr-dev-name">{{ d.name }}</span>
            <span v-if="d.run_counts.running" class="wr-dev-run">{{ d.run_counts.running }} 执行中</span>
            <span v-else class="wr-dev-idle">{{ d.online ? '待命' : '离线' }}</span>
          </div>
          <div v-if="!devs.devices.length" class="wr-empty">暂无注册设备</div>
        </div>
      </div>
    </div>

    <!-- 底部：防线日历 + 今日活动 -->
    <div class="wr-bottom">
      <div class="wr-panel">
        <div class="wr-ph">// DEFENSE LINE · 回归防线({{ cal.total_guard_days }}天值守)</div>
        <div class="wr-wall">
          <span v-for="d in cal.days" :key="d.date" class="wr-cell" :class="`c-${d.state}`"
                :title="`${d.date} ${d.runs}批`"/>
        </div>
      </div>
      <div class="wr-panel">
        <div class="wr-ph">// LIVE ACTIVITY · 执行中任务</div>
        <div class="wr-live">
          <template v-if="liveRuns.length">
            <div v-for="r in liveRuns" :key="r.run_id" class="wr-live-row">
              <span class="wr-live-pip"></span>
              <span class="wr-live-dev">{{ r.dev }}</span>
              <span class="wr-live-title">{{ r.title }}</span>
              <span class="wr-live-t">{{ fmtElapsed(r.started_at) }}</span>
            </div>
          </template>
          <div v-else class="wr-empty">当前无执行中任务 · 编队待命</div>
        </div>
      </div>
    </div>

    <div class="wr-foot">// AUTO REFRESH 每 {{ POLL_SEC }}s · {{ lastAt }} · 数据源: 设备/漏斗/防线聚合端点</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getDeviceOverview, aiFunnel, defenseCalendar } from '@/api'

const POLL_SEC = 30
const devs = ref({ total_devices: 0, online_devices: 0, running_devices: 0, devices: [] })
const funnel = ref({ funnel: [], bugs_found: 0, selector_pending: 0, saved_hours: 0 })
const cal = ref({ days: [], streak: 0, total_guard_days: 0 })
const lastAt = ref('—')

const now = ref(Date.now())
let clockTimer = null
let pollTimer = null

const clock = computed(() => new Date(now.value).toLocaleTimeString('zh-CN', { hour12: false }))
const dateLine = computed(() =>
  new Date(now.value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' }))

const STEP_COLORS = ['#2a78d6', '#3f8fc9', '#31a3ab', '#19b394', '#00b386']
function stepWidth(i) {
  const max = funnel.value.funnel[0]?.count || 1
  const c = funnel.value.funnel[i]?.count || 0
  return Math.max(22, Math.round((c / max) * 100)) + '%'
}
const passRate = computed(() => {
  const ex = funnel.value.funnel[3]?.count || 0
  const ps = funnel.value.funnel[4]?.count || 0
  return ex ? Math.round((ps / ex) * 100) : 0
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
  // 三源并发；单源失败静默保留上次数据
  const [d, f, c] = await Promise.all([
    getDeviceOverview().catch(() => null),
    aiFunnel(30).catch(() => null),
    defenseCalendar(12).catch(() => null),
  ])
  if (d) devs.value = d
  if (f) funnel.value = f
  if (c) cal.value = c
  lastAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
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
/* 全站唯一整页深色页——大屏定位刻意差异 */
.wr {
  margin: -20px; padding: 24px 28px 32px; min-height: calc(100vh - 60px); box-sizing: border-box;
  background: linear-gradient(180deg, #10151d 0%, #0d1118 100%);
  color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.wr-head { display: flex; justify-content: space-between; align-items: flex-start; }
.wr-eyebrow { font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: 3px; color: #00e5a0; }
.wr-title { font-size: 28px; font-weight: 800; letter-spacing: 1px; margin-top: 6px; color: #fff; }
.wr-clock { font-family: 'JetBrains Mono', monospace; font-size: 34px; font-weight: 700; color: #fff; text-align: right; line-height: 1.1; }
.wr-date { font-size: 12px; color: #7d8a9b; margin-top: 4px; letter-spacing: 1px; }

.wr-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin: 22px 0; }
.wr-kpi { background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.09); border-radius: 12px; padding: 16px 20px; }
.wr-num { font-family: 'JetBrains Mono', monospace; font-size: 38px; font-weight: 800; color: #fff; line-height: 1; }
.wr-num.green { color: #00e5a0; }
.wr-num.blue { color: #35b6ff; }
.wr-num.red { color: #ff5c6c; }
.wr-num.amber { color: #e8a23d; }
.wr-u { font-size: 16px; color: #5f6b7a; margin-left: 3px; }
.wr-lbl { font-size: 12px; color: #7d8a9b; margin-top: 8px; }

.wr-mid, .wr-bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
.wr-panel { background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.08); border-radius: 12px; padding: 16px 20px; min-height: 180px; }
.wr-ph { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: 2px; color: #00e5a0; margin-bottom: 14px; }

.wr-funnel { display: flex; flex-direction: column; gap: 5px; }
.wr-step { min-height: 34px; border-radius: 5px; padding: 4px 12px; display: flex; align-items: center; gap: 10px; color: #fff;
  clip-path: polygon(0 0, 100% 0, calc(100% - 12px) 100%, 0 100%); transition: width .5s ease; }
.wr-step-n { font-family: 'JetBrains Mono', monospace; font-size: 18px; font-weight: 800; }
.wr-step-l { font-size: 11px; opacity: .9; }

.wr-fleet { display: flex; flex-direction: column; gap: 8px; }
.wr-dev { display: flex; align-items: center; gap: 10px; padding: 7px 12px; background: rgba(255,255,255,.04); border-radius: 8px; font-size: 13px; }
.wr-dev.off { opacity: .45; }
.wr-dev.busy { border: 1px solid #35b6ff44; }
.wr-light { width: 8px; height: 8px; border-radius: 50%; background: #55606e; flex: none; }
.wr-light.on { background: #00e5a0; box-shadow: 0 0 7px #00e5a0; animation: wrbreathe 1.8s ease-in-out infinite; }
.wr-dev-name { color: #cdd7e2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wr-dev-run { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #35b6ff; flex: none; }
.wr-dev-idle { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #5f6b7a; flex: none; }

.wr-wall { display: grid; grid-template-rows: repeat(7, 11px); grid-auto-flow: column; grid-auto-columns: 11px; gap: 3px; overflow-x: auto; }
.wr-cell { width: 11px; height: 11px; border-radius: 2px; }
.c-green { background: #00b386; }
.c-red { background: #e5565f; }
.c-gray { background: rgba(255,255,255,.08); }

.wr-live { display: flex; flex-direction: column; gap: 8px; }
.wr-live-row { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.wr-live-pip { width: 6px; height: 6px; border-radius: 50%; background: #35b6ff; flex: none; animation: wrbreathe 1.2s ease-in-out infinite; }
.wr-live-dev { font-family: 'JetBrains Mono', monospace; color: #7d8a9b; flex: none; }
.wr-live-title { color: #c3cedb; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wr-live-t { margin-left: auto; font-family: 'JetBrains Mono', monospace; color: #35b6ff; flex: none; font-variant-numeric: tabular-nums; }

.wr-empty { color: #5f6b7a; font-size: 12px; font-family: 'JetBrains Mono', monospace; padding: 20px 0; text-align: center; }
.wr-foot { text-align: center; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #45505e; letter-spacing: 1px; margin-top: 8px; }

@keyframes wrbreathe { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@media (max-width: 1000px) { .wr-kpis { grid-template-columns: repeat(2, 1fr); } .wr-mid, .wr-bottom { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .wr-light.on, .wr-live-pip { animation: none; } }
</style>
