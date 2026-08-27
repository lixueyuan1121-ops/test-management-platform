<template>
  <div class="board">
    <!-- ① 头条：设备编队总览 -->
    <div class="panel hero">
      <div class="grid-bg"></div>
      <div class="hero-l">
        <div class="eyebrow">// DEVICE FLEET · 执行机监控</div>
        <div class="hero-hi">设备看板</div>
        <div class="hero-sub">全平台注册执行机的在线状态与任务执行实时视图（只读）</div>
      </div>
      <div class="hero-r">
        <div class="clock">{{ clock }}<div class="date">{{ dateLine }}</div></div>
        <div class="status-row">
          <span class="dot" :class="{ live: ov.online_devices > 0 }"></span>
          {{ ov.online_devices }} / {{ ov.total_devices }} ONLINE
        </div>
        <div class="proj-cnt">POLL // 每 {{ POLL_SEC }}s 刷新 · {{ lastAt }}</div>
      </div>
    </div>

    <!-- ② KPI 指标条 -->
    <div class="kpi-wall">
      <div class="kpi">
        <div class="kpi-num">{{ ov.total_devices }}</div>
        <div class="kpi-lbl">注册设备</div>
      </div>
      <div class="kpi">
        <div class="kpi-num on">{{ ov.online_devices }}</div>
        <div class="kpi-lbl">在线</div>
      </div>
      <div class="kpi">
        <div class="kpi-num running">{{ ov.running_devices }}<span class="live-pip" v-if="ov.running_devices"></span></div>
        <div class="kpi-lbl">执行中设备</div>
      </div>
      <div class="kpi">
        <div class="kpi-num">{{ todayDone }}</div>
        <div class="kpi-lbl">今日完成</div>
      </div>
    </div>

    <!-- ③ 设备卡片网格 -->
    <div v-loading="loading && !ov.devices.length" element-loading-background="rgba(17,20,26,0.6)">
      <div v-if="!ov.devices.length && !loading" class="empty">
        <div class="empty-mark">∅</div>
        暂无注册设备。成员在「我的设备」登记执行机后，这里会显示其在线状态与执行情况。
      </div>

      <div v-else class="grid">
        <div v-for="d in ov.devices" :key="d.id" class="card"
             :class="{ offline: !d.online, 'is-running': d.run_counts.running > 0 }">
          <!-- 执行中：边缘扫描流光 -->
          <div v-if="d.run_counts.running > 0" class="scan"></div>

          <div class="card-hd">
            <span class="light" :class="d.online ? 'on' : 'off'"></span>
            <div class="dev-name" :title="d.name">{{ d.name }}</div>
            <!-- 正在执行的任务类型标识(去重):功能测试/测评任务/…(未知类型显示原文,便于扩展) -->
            <span v-for="k in runKinds(d)" :key="k" class="kind-tag" :class="'k-' + k">{{ KIND_LABEL[k] || k }}</span>
            <div class="runner-id">{{ d.runner_id }}</div>
            <el-tag v-if="d.platform && d.platform !== 'web'" :type="d.platform === 'ios' ? 'warning' : 'success'" size="small" effect="plain" class="plat-tag">{{ d.platform.toUpperCase() }}</el-tag>
          </div>

          <div class="meta">
            <span class="owner">{{ d.owner.name || '—' }}</span>
            <span class="seen">{{ d.online ? '在线' : lastSeenText(d.last_seen_at) }}</span>
          </div>

          <!-- 四格计数 -->
          <div class="counts">
            <div class="c c-run" :class="{ hot: d.run_counts.running > 0 }">
              <div class="c-n">{{ d.run_counts.running }}</div><div class="c-l">执行中</div>
            </div>
            <div class="c c-pend">
              <div class="c-n">{{ d.run_counts.pending }}</div><div class="c-l">排队</div>
            </div>
            <div class="c c-pass">
              <div class="c-n">{{ d.today.passed }}</div><div class="c-l">今日通过</div>
            </div>
            <div class="c c-fail">
              <div class="c-n">{{ d.today.failed }}</div><div class="c-l">今日失败</div>
            </div>
          </div>

          <!-- 执行中明细：当前正在跑的用例（非批次/非历史），最多展示 3 条，超出汇总 -->
          <div v-if="d.active_runs.length" class="active">
            <div v-for="r in d.active_runs.slice(0, 3)" :key="(r.kind || 'func') + r.run_id" class="run">
              <span class="run-pip" :class="{ 'pip-eval': r.kind === 'eval' }"></span>
              <span class="run-kind" :class="'k-' + (r.kind || 'func')">{{ KIND_SHORT[r.kind] || r.kind || '功能' }}</span>
              <span class="run-title" :title="r.title">{{ r.title }}</span>
              <span class="run-time">{{ fmtElapsed(r.started_at) }}</span>
            </div>
            <div v-if="d.active_runs.length > 3" class="run-more">
              +{{ d.active_runs.length - 3 }} 项并发执行中
            </div>
          </div>
          <div v-else class="idle">{{ d.online ? '空闲待命' : '离线' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getDeviceOverview } from '@/api'

const POLL_SEC = 5
const ONLINE_STATE = { online_devices: 0, total_devices: 0, running_devices: 0, devices: [] }
const ov = ref({ ...ONLINE_STATE })
const loading = ref(true)
const lastAt = ref('—')

// 执行类型标识:后端 active_runs[].kind → 展示文案(卡片头全称/明细行短标)。
// 老数据无 kind 按 func 兜底;未知新类型直接显示原文,后端扩展类型时前端无需先行发版。
const KIND_LABEL = { func: '功能测试', eval: '测评任务' }
const KIND_SHORT = { func: '功能', eval: '测评' }
const runKinds = (d) => [...new Set((d.active_runs || []).map((r) => r.kind || 'func'))]

// 每秒 tick：驱动时钟与"执行中"计时器（用统一 now 让所有卡片同步跳动）
const now = ref(Date.now())
let clockTimer = null
let pollTimer = null

const clock = computed(() => new Date(now.value).toLocaleTimeString('zh-CN', { hour12: false }))
const dateLine = computed(() =>
  new Date(now.value).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' }))
const todayDone = computed(() =>
  ov.value.devices.reduce((s, d) => s + d.today.passed + d.today.failed, 0))

async function load() {
  try {
    ov.value = await getDeviceOverview()
    lastAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    // 轮询失败静默：保留上次数据，不打断看板（下次 tick 再试）
  } finally {
    loading.value = false
  }
}

// 已用时长：从 started_at 到当前 now，随每秒 tick 跳动
function fmtElapsed(startedAt) {
  if (!startedAt) return '—'
  const ms = now.value - new Date(startedAt).getTime()
  if (ms < 0) return '0s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function lastSeenText(iso) {
  if (!iso) return '从未上报'
  const ms = now.value - new Date(iso).getTime()
  const m = Math.floor(ms / 60000)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
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
/* 浅色主题：白底 + 浅灰卡片浮起；hero 保留深色科技条作顶部锚点（对齐 Dashboard 浅底+深色hero 的模式） */
.board {
  /* 负 margin 抵消 MainLayout .main 的 20px 灰底 padding，让看板浅底铺满，与侧栏衔接处自然过渡 */
  margin: -20px;
  padding: 20px 20px 40px;
  min-height: calc(100vh - 60px);
  box-sizing: border-box;
  background: linear-gradient(180deg, #eef1f5 0%, #f6f8fa 120px);
  color: #1a1d21;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.panel { border-radius: 14px; }

/* ① Hero：深色科技条（顶部视觉锚点，网格背景 + 信号青绿点缀） */
.hero {
  position: relative; overflow: hidden;
  background: linear-gradient(135deg, #1a2836 0%, #212f43 100%);
  border: 1px solid rgba(255,255,255,.08); padding: 26px 30px;
  display: flex; justify-content: space-between; align-items: flex-start; gap: 20px;
}
.grid-bg {
  position: absolute; inset: 0;
  background-image: linear-gradient(#ffffff0a 1px, transparent 1px), linear-gradient(90deg, #ffffff0a 1px, transparent 1px);
  background-size: 26px 26px; mask-image: radial-gradient(ellipse at 30% 0%, #000 40%, transparent 80%);
}
.hero-l, .hero-r { position: relative; z-index: 1; }
.eyebrow { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; letter-spacing: 2px; color: #00e5a0; }
.hero-hi { font-size: 30px; font-weight: 800; margin-top: 6px; letter-spacing: 1px; color: #fff; }
.hero-sub { color: #8b98a9; font-size: 13px; margin-top: 6px; }
.hero-r { text-align: right; }
.clock { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 30px; font-weight: 700; color: #fff; line-height: 1.1; }
.clock .date { font-size: 12px; color: #7d8a9b; font-weight: 500; margin-top: 4px; letter-spacing: 1px; }
.status-row { margin-top: 12px; font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #a7b4c4; letter-spacing: 1px; }
.status-row .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #55606e; margin-right: 6px; vertical-align: middle; }
.status-row .dot.live { background: #00e5a0; box-shadow: 0 0 8px #00e5a0; animation: breathe 1.6s ease-in-out infinite; }
.proj-cnt { margin-top: 6px; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #5f6b7a; letter-spacing: 1px; }

/* ② KPI：白底浮起，数字深色着色 */
.kpi-wall { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 16px 0; }
.kpi {
  background: #fff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 2px 8px rgba(31,45,61,.05);
}
.kpi-num { font-family: 'JetBrains Mono', monospace; font-size: 34px; font-weight: 800; color: #1a1d21; line-height: 1; position: relative; display: inline-block; }
.kpi-num.on { color: #00b386; }
.kpi-num.running { color: #2f7dd1; }
.kpi-lbl { color: #7d8a9b; font-size: 13px; margin-top: 8px; }
.live-pip { position: absolute; top: 2px; right: -14px; width: 8px; height: 8px; border-radius: 50%; background: #2f7dd1; box-shadow: 0 0 8px #2f7dd1; animation: breathe 1.4s ease-in-out infinite; }

/* ③ 卡片：白底浮起（严格等高） */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; align-items: stretch; grid-auto-rows: 1fr; }
.card {
  position: relative; overflow: hidden;
  height: 100%; box-sizing: border-box;   /* 配合 grid-auto-rows:1fr 强制同行卡片严格等高 */
  display: flex; flex-direction: column;   /* 执行区 flex:1 吸收高度差 */
  background: #fff; border: 1px solid #e3e8ef; border-radius: 12px; padding: 16px 18px;
  box-shadow: 0 2px 8px rgba(31,45,61,.05);
  transition: border-color .3s, box-shadow .3s, opacity .3s;
}
.card.is-running { border-color: #2f7dd166; box-shadow: 0 0 0 1px #2f7dd122, 0 8px 28px -8px #2f7dd144; }
.card.offline { opacity: .6; }

/* 签名动效：执行中卡片顶部扫描流光 */
.scan {
  position: absolute; top: 0; left: -40%; width: 40%; height: 2px;
  background: linear-gradient(90deg, transparent, #2f7dd1, transparent);
  animation: scan 2.4s linear infinite;
}

.card-hd { display: flex; align-items: center; gap: 8px; }
.light { width: 9px; height: 9px; border-radius: 50%; flex: none; margin-top: 2px; }
.light.on { background: #00b386; box-shadow: 0 0 7px rgba(0,179,134,.7); animation: breathe 1.8s ease-in-out infinite; }
.light.off { background: #c0c6ce; }
.dev-name { font-size: 15px; font-weight: 700; color: #1a1d21; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.runner-id { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #7d8a9b; background: #f0f2f5; padding: 2px 7px; border-radius: 5px; flex: none; }
.plat-tag { margin-left: 4px; flex: none; }
/* 任务类型标识:卡片头全称 tag + 明细行短标。功能=蓝系、测评=紫系;新类型在 KIND_* 与此处补一组即可 */
.kind-tag { flex: none; font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 5px; letter-spacing: .5px; }
.run-kind { flex: none; font-size: 10px; padding: 1px 5px; border-radius: 4px; letter-spacing: .3px; }
.k-func { background: #e8f1fb; color: #2f7dd1; }
.k-eval { background: #f1ebfa; color: #7a4fd0; }
.pip-eval { background: #7a4fd0; }
.meta { display: flex; justify-content: space-between; margin-top: 8px; font-size: 12px; color: #8b98a9; }
.meta .seen { font-family: 'JetBrains Mono', monospace; color: #9aa5b1; }

.counts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 14px 0 4px; }
.c { text-align: center; background: #f5f7fa; border: 1px solid #eef1f5; border-radius: 8px; padding: 8px 4px; }
.c-n { font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 800; color: #4a5568; line-height: 1; }
.c-l { font-size: 11px; margin-top: 4px; color: #8b98a9; }
.c-run.hot .c-n { color: #2f7dd1; }
.c-pass .c-n { color: #00b386; }
.c-fail .c-n { color: #e5565f; }

/* 执行中明细 */
.active { margin-top: 12px; border-top: 1px dashed #e3e8ef; padding-top: 10px; display: flex; flex-direction: column; gap: 7px; flex: 1; }
.run { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.run-more { font-size: 11px; color: #8b98a9; font-family: 'JetBrains Mono', monospace; padding-left: 14px; letter-spacing: .3px; }
.run-pip { width: 6px; height: 6px; border-radius: 50%; background: #2f7dd1; flex: none; animation: breathe 1.2s ease-in-out infinite; }
.run-title { color: #4a5568; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.run-time { margin-left: auto; font-family: 'JetBrains Mono', monospace; color: #2f7dd1; flex: none; font-variant-numeric: tabular-nums; }
.idle { margin-top: 12px; border-top: 1px dashed #e3e8ef; padding-top: 10px; font-size: 12px; color: #9aa5b1; font-family: 'JetBrains Mono', monospace; flex: 1; }

.empty { text-align: center; color: #7d8a9b; padding: 80px 20px; font-size: 14px; line-height: 2; }
.empty-mark { font-size: 46px; color: #c0c6ce; margin-bottom: 10px; }

@keyframes breathe { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes scan { 0% { left: -40%; } 100% { left: 100%; } }

@media (prefers-reduced-motion: reduce) {
  .scan, .light.on, .run-pip, .status-row .dot.live, .live-pip { animation: none; }
  .scan { display: none; }
}
</style>
