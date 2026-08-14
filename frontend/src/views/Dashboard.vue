<template>
  <div class="dashboard">
    <!-- ① 控制台头条 -->
    <div class="panel hero">
      <div class="grid-bg"></div>
      <div class="hero-l">
        <div class="eyebrow">// OPERATIONS CENTER</div>
        <div class="hero-hi">{{ greeting }}，{{ auth.user?.name || auth.user?.username }}</div>
        <div class="hero-sub">
          {{ auth.user?.is_platform_admin ? '平台管理员视角 · 管理所有项目与人员' : '项目成员视角 · 查看你的任务与日报' }}
        </div>
      </div>
      <div class="hero-r">
        <div class="clock">
          {{ clock }}
          <div class="date">{{ dateLine }}</div>
        </div>
        <div class="status-row"><span class="dot"></span> SYSTEM // READY</div>
        <div class="proj-cnt">NODES // {{ overview?.project_cnt ?? projects.length }} 项目 · {{ auth.memberships.length }} 成员关系</div>
      </div>
    </div>

    <!-- ② KPI 指标墙：今日派单流转状态维度 -->
    <div v-if="!isEmpty" class="kpi-wall" v-loading="ovLoading" element-loading-background="rgba(255,255,255,0.6)">
      <div class="kpi ring">
        <div class="ring-wrap">
          <svg width="72" height="72" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#e3e8ef" stroke-width="9" />
            <circle cx="50" cy="50" r="42" fill="none" stroke="#00b386" stroke-width="9"
                    stroke-linecap="round" :stroke-dasharray="RING_C" :stroke-dashoffset="ringOffset"
                    transform="rotate(-90 50 50)" class="ring-arc" />
            <text x="50" y="56" text-anchor="middle" fill="#1a1d21" font-size="24"
                  font-family="'JetBrains Mono',monospace" font-weight="700">{{ Math.round(t.done_rate) }}</text>
          </svg>
        </div>
        <div class="ring-text">
          <div class="kpi-lbl">今日完成率</div>
          <div class="kpi-foot">{{ t.online }} / {{ t.total }} 已上线</div>
        </div>
      </div>

      <div class="kpi">
        <div class="kpi-lbl">今日任务</div>
        <div class="kpi-num">{{ t.total }}</div>
        <div class="kpi-foot">待测 {{ t.pending }}</div>
      </div>

      <div class="kpi info">
        <div class="kpi-lbl">测试中</div>
        <div class="kpi-num">{{ t.testing }}</div>
        <div class="kpi-foot">正在执行</div>
      </div>

      <div class="kpi danger">
        <div class="kpi-lbl">阻塞</div>
        <div class="kpi-num">{{ t.blocked }}</div>
        <div class="kpi-foot">需处理</div>
      </div>

      <div class="kpi">
        <div class="kpi-lbl">已上线</div>
        <div class="kpi-num">{{ t.online }}</div>
        <div class="kpi-foot">今日完成</div>
      </div>

      <div class="kpi warn">
        <div class="kpi-lbl">未解决遗留</div>
        <div class="kpi-num">{{ overview?.open_issues ?? 0 }}</div>
        <div class="kpi-foot">跨项目存量</div>
      </div>
    </div>

    <!-- ③ 近 7 天趋势（有数据时） -->
    <div v-if="!isEmpty" class="panel trend">
      <div class="trend-head">
        <div class="trend-title">近 7 天派单趋势</div>
        <div class="trend-legend">
          <span class="lg"><i style="background:#00b386"></i>任务量</span>
          <span class="lg"><i style="background:#5b9bd5"></i>上线量</span>
        </div>
      </div>
      <svg viewBox="0 0 700 180" preserveAspectRatio="none" class="trend-svg">
        <!-- 网格 -->
        <g stroke="#e8ecf2" stroke-width="1">
          <line x1="40" y1="30" x2="660" y2="30" />
          <line x1="40" y1="70" x2="660" y2="70" />
          <line x1="40" y1="110" x2="660" y2="110" />
          <line x1="40" y1="150" x2="660" y2="150" />
        </g>
        <defs>
          <linearGradient id="dashArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#00b386" stop-opacity="0.22" />
            <stop offset="100%" stop-color="#00b386" stop-opacity="0" />
          </linearGradient>
        </defs>
        <!-- 面积 + 折线 -->
        <path :d="areaPath" fill="url(#dashArea)" />
        <path :d="linePath" fill="none" stroke="#00b386" stroke-width="2.5" stroke-linejoin="round" class="line-glow" />
        <!-- 提交人次次要柱 -->
        <g fill="#3b82c4" opacity="0.5">
          <rect v-for="(p, i) in points" :key="'b' + i" :x="p.x - 4" :y="p.by" width="8" :height="150 - p.by" rx="1" />
        </g>
        <!-- 数据点 -->
        <g fill="#ffffff" stroke="#00b386" stroke-width="2">
          <circle v-for="(p, i) in points" :key="'p' + i" :cx="p.x" :cy="p.y"
                  :r="i === points.length - 1 ? 4.5 : 3.5"
                  :fill="i === points.length - 1 ? '#00b386' : '#ffffff'" />
        </g>
        <!-- x 轴标签 -->
        <g fill="#6b7280" font-size="11" font-family="'JetBrains Mono',monospace" text-anchor="middle">
          <text v-for="(p, i) in points" :key="'x' + i" :x="p.x" y="172">{{ p.label }}</text>
        </g>
      </svg>
    </div>

    <!-- ②③ 空数据态：整块科技风引导卡（替代 KPI 墙 + 趋势图） -->
    <div v-if="isEmpty" class="empty-state panel">
      <div class="grid-bg"></div>
      <div class="es-inner">
        <TargetMark :size="96" :animated="true" class="es-mark" />
        <div class="es-eyebrow">// NO SIGNAL · 暂无运营数据</div>
        <div class="es-title">今日尚未产生数据</div>
        <div class="es-desc">{{ emptyCta.hint }}</div>
        <el-button type="primary" class="es-btn" @click="$router.push(emptyCta.path)">
          {{ emptyCta.label }} →
        </el-button>
        <div class="es-metrics">
          <span>TASK // --</span><i></i>
          <span>TESTING // --</span><i></i>
          <span>ONLINE // --</span><i></i>
          <span>ISSUES // --</span>
        </div>
      </div>
    </div>

    <!-- ④ 快捷入口 -->
    <div class="quick">
      <div class="qcard" v-for="q in quickEntries" :key="q.path" @click="$router.push(q.path)">
        <div class="qicon"><el-icon><component :is="q.icon" /></el-icon></div>
        <div>
          <div class="qtitle">{{ q.title }}</div>
          <div class="qdesc">{{ q.desc }}</div>
        </div>
      </div>
    </div>

    <!-- ④ 我的项目 -->
    <div class="panel">
      <div class="block-head">
        <span class="block-title">我的项目</span>
        <el-button v-if="auth.isPlatformAdmin" link class="link-btn" @click="$router.push('/projects')">去管理 →</el-button>
      </div>
      <el-table :data="projects" v-loading="projectsLoading" size="default" class="dark-table"
                empty-text="暂无项目，去「组织管理-项目管理」创建">
        <el-table-column prop="code" label="编码" width="150">
          <template #default="{ row }"><span class="mono">{{ row.code }}</span></template>
        </el-table-column>
        <el-table-column prop="name" label="名称" />
        <el-table-column label="我的角色" width="120">
          <template #default="{ row }">
            <span class="dtag" :class="roleClass(row.id)">{{ roleLabel(row.id) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <span class="dtag" :class="row.status === 'active' ? 'active' : 'guest'">
              {{ row.status === 'active' ? '活跃' : '归档' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link class="link-btn" @click="$router.push(`/projects/${row.id}/members`)">成员管理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { overviewStats } from '@/api'
import { Files, List, DataLine, TrendCharts } from '@element-plus/icons-vue'
import TargetMark from '@/components/TargetMark.vue'

const auth = useAuthStore()
const app = useAppStore()
const projects = ref([])
const projectsLoading = ref(false)
const overview = ref(null)
const ovLoading = ref(false)

const RING_C = 2 * Math.PI * 42  // 环形周长

onMounted(() => {
  startClock()
  loadProjects()
  loadOverview()
})
onUnmounted(stopClock)

async function loadProjects() {
  projectsLoading.value = true
  try { projects.value = await app.fetchProjects() }
  finally { projectsLoading.value = false }
}
async function loadOverview() {
  ovLoading.value = true
  try { overview.value = await overviewStats() }
  catch { overview.value = null }  // 降级：KPI 显示 0/占位，不阻断项目表
  finally { ovLoading.value = false }
}

// ---- 今日 KPI（带默认值，overview 未到时显示 0）----
const t = computed(() => overview.value?.today ?? {
  total: 0, pending: 0, testing: 0, blocked: 0, online: 0, done_rate: 0,
})
const ringOffset = computed(() => RING_C * (1 - (t.value.done_rate || 0) / 100))

// ---- 空数据态判定：今日无任务、无遗留，且近 7 天零派单/零上线 ----
const isEmpty = computed(() => {
  const today = t.value
  const noToday = (today.total || 0) === 0
  const noIssues = (overview.value?.open_issues ?? 0) === 0
  const trend = overview.value?.trend ?? []
  const noTrend = trend.every((d) => (d.total || 0) === 0 && (d.online || 0) === 0)
  return !ovLoading.value && noToday && noIssues && noTrend
})
// 空态引导入口：管理员先去派任务，成员则今日暂无指派
const emptyCta = computed(() => auth.isPlatformAdmin
  ? { path: '/tasks', label: '去分配今日任务', hint: '为项目成员指派测试任务，开启今日流转' }
  : { path: '/my-reports', label: '查看我的日报', hint: '今日暂无指派给你的测试任务' })

// ---- 7 天趋势 SVG 坐标（折线=任务量，次要柱=上线量）----
const points = computed(() => {
  const data = overview.value?.trend ?? []
  if (!data.length) return []
  const x0 = 40, x1 = 660, yTop = 40, yBot = 150
  const maxT = Math.max(1, ...data.map((d) => d.total))
  const maxO = Math.max(1, ...data.map((d) => d.online))
  const step = data.length > 1 ? (x1 - x0) / (data.length - 1) : 0
  return data.map((d, i) => {
    const x = x0 + step * i
    const y = yBot - (d.total / maxT) * (yBot - yTop)          // 任务量折线 y
    const bh = (d.online / maxO) * 18                           // 柱高（0-18）
    const by = yBot - bh                                        // 柱顶 y
    const label = i === data.length - 1 ? '今日' : d.date.slice(5)
    return { x, y, by, label }
  })
})
const linePath = computed(() =>
  points.value.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y.toFixed(1)}`).join(' '))
const areaPath = computed(() => {
  const p = points.value
  if (!p.length) return ''
  return `M${p[0].x},${p[0].y.toFixed(1)} `
    + p.slice(1).map((q) => `L${q.x},${q.y.toFixed(1)}`).join(' ')
    + ` L${p[p.length - 1].x},150 L${p[0].x},150 Z`
})

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

// ---- 实时时钟 ----
const clock = ref('--:--:--')
const dateLine = ref('')
let timer = null
const pad = (n) => String(n).padStart(2, '0')
const WK = ['日', '一', '二', '三', '四', '五', '六']
function tick() {
  const d = new Date()
  clock.value = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  dateLine.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} 周${WK[d.getDay()]}`
}
function startClock() { tick(); timer = setInterval(tick, 1000) }
function stopClock() { if (timer) clearInterval(timer) }

const quickEntries = computed(() => {
  const base = [
    { path: '/stats', title: '日报统计', desc: '应交/已交/上线一览', icon: DataLine },
    { path: '/workload', title: '工作量统计', desc: '人时趋势图表', icon: TrendCharts },
    { path: '/issues', title: '遗留问题', desc: '未解决缺陷跟踪', icon: Files },
  ]
  if (auth.isPlatformAdmin) {
    base.unshift({ path: '/tasks', title: '任务分配', desc: '分配每日工作任务', icon: List })
  } else {
    base.unshift({ path: '/my-reports', title: '我的日报', desc: '填报今日测试进度', icon: List })
  }
  return base
})

function roleLabel(pid) {
  const r = auth.roleIn(pid)
  return { admin: '管理员', member: '成员', guest: '嘉宾' }[r] || r || '-'
}
function roleClass(pid) {
  return auth.roleIn(pid) || 'guest'
}
</script>

<style scoped>
/* 亮色科技风 token（与全局 theme.css 呼应），仅作用于本页 */
.dashboard {
  --ink: #f4f6f9;
  --panel: #ffffff;
  --panel-2: #f7f9fc;
  --line: #e3e8ef;
  --dim: #9aa3b2;
  --fg: #1a1d21;
  --muted: #6b7280;
  --signal: #00b386;
  --warn: #e6a23c;
  --info: #3b82c4;
  --mono: 'JetBrains Mono', 'SFMono-Regular', ui-monospace, monospace;

  display: flex;
  flex-direction: column;
  gap: 16px;
  /* 铺满内容区底色：抵消 el-main 的 20px padding */
  margin: -20px;
  padding: 20px;
  min-height: calc(100vh - 60px);
  background: var(--ink);
  color: var(--fg);
}

.panel { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: 0 1px 2px rgba(16,24,40,0.04); }
.grid-bg {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 40px 40px; opacity: 0.6;
  mask-image: radial-gradient(120% 120% at 15% 0%, #000 30%, transparent 80%);
  -webkit-mask-image: radial-gradient(120% 120% at 15% 0%, #000 30%, transparent 80%);
  pointer-events: none;
}

/* ① 控制台头条 */
.hero { padding: 26px 30px; display: flex; justify-content: space-between; align-items: center; }
.hero-l { position: relative; z-index: 2; }
.eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: 3px; color: var(--signal); margin-bottom: 12px; }
.hero-hi { font-size: 24px; font-weight: 700; letter-spacing: 1px; }
.hero-sub { font-size: 13px; color: var(--muted); margin-top: 8px; }
.hero-r { position: relative; z-index: 2; text-align: right; font-family: var(--mono); }
.clock { font-size: 30px; font-weight: 700; letter-spacing: 2px; font-variant-numeric: tabular-nums; }
.clock .date { font-size: 12px; color: var(--muted); letter-spacing: 2px; font-weight: 400; }
.status-row { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 12px; font-size: 11px; letter-spacing: 1.5px; color: var(--fg); }
.dot { width: 7px; height: 7px; background: var(--signal); box-shadow: 0 0 6px rgba(0,179,134,.6); animation: pulse 1.8s ease-in-out infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.proj-cnt { margin-top: 6px; font-size: 11px; letter-spacing: 1.5px; color: var(--dim); }

/* ②③ 空数据态引导卡 */
.empty-state {
  min-height: 340px;
  display: flex; align-items: center; justify-content: center;
  padding: 40px 24px;
}
.es-inner {
  position: relative; z-index: 2;
  display: flex; flex-direction: column; align-items: center; text-align: center;
}
.es-mark {
  --tm-line: #cbd5e1; --tm-dim: #94a3b8; --tm-signal: var(--signal);
  filter: drop-shadow(0 0 10px rgba(0,179,134,.25));
  margin-bottom: 22px;
}
.es-eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: 3px; color: var(--signal); }
.es-title { font-size: 22px; font-weight: 700; letter-spacing: .5px; margin-top: 12px; color: var(--fg); }
.es-desc { font-size: 13px; color: var(--muted); margin-top: 8px; max-width: 420px; line-height: 1.6; }
.es-btn {
  margin-top: 24px; font-family: var(--mono); letter-spacing: .5px;
  --el-button-bg-color: var(--signal); --el-button-border-color: var(--signal);
  --el-button-hover-bg-color: #00c896; --el-button-hover-border-color: #00c896;
}
.es-metrics {
  display: flex; align-items: center; gap: 14px; margin-top: 30px;
  font-family: var(--mono); font-size: 11px; letter-spacing: 1.5px; color: var(--dim);
}
.es-metrics i { width: 1px; height: 12px; background: var(--line); display: inline-block; }

/* ② KPI 指标墙 */
.kpi-wall { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }
.kpi {
  position: relative; background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 18px; overflow: hidden;
  transition: border-color .18s ease, box-shadow .2s ease, transform .18s ease;
}
.kpi:hover { border-color: var(--signal); box-shadow: 0 0 16px rgba(0,179,134,.18); transform: translateY(-2px); }
.kpi-lbl { font-family: var(--mono); font-size: 10px; letter-spacing: 1.5px; color: var(--muted); text-transform: uppercase; }
.kpi-num { font-family: var(--mono); font-size: 34px; font-weight: 700; line-height: 1.1; margin-top: 10px; color: var(--signal); font-variant-numeric: tabular-nums; }
.kpi-num small { font-size: 15px; color: var(--dim); margin-left: 3px; }
.kpi-foot { font-size: 11px; color: var(--muted); margin-top: 8px; font-family: var(--mono); }
.kpi.warn .kpi-num { color: var(--warn); }
.kpi.info .kpi-num { color: var(--info); }
.kpi.danger .kpi-num { color: #e05561; }
.kpi.ring { display: flex; align-items: center; gap: 14px; }
.ring-wrap { flex: 0 0 auto; }
.ring-text { flex: 1; }
.ring-text .kpi-foot { margin-top: 10px; }
.ring-arc { filter: drop-shadow(0 0 3px rgba(0,179,134,.4)); transition: stroke-dashoffset .6s ease; }

/* ③ 趋势条 */
.trend { padding: 20px 24px; }
.trend-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.trend-title { font-size: 14px; font-weight: 600; letter-spacing: .5px; }
.trend-legend { font-family: var(--mono); font-size: 11px; color: var(--muted); display: flex; gap: 16px; }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.lg i { width: 10px; height: 2px; display: inline-block; }
.trend-svg { width: 100%; height: auto; display: block; }
.line-glow { filter: drop-shadow(0 0 2px rgba(0,179,134,.3)); }

/* ④ 快捷入口 */
.quick { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.qcard {
  display: flex; align-items: center; gap: 14px; padding: 16px 18px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  cursor: pointer; transition: border-color .18s ease, box-shadow .2s ease, transform .18s ease;
}
.qcard:hover { border-color: var(--signal); box-shadow: 0 0 14px rgba(0,179,134,.16); transform: translateY(-2px); }
.qicon { width: 40px; height: 40px; border: 1px solid var(--line); border-radius: 6px; display: flex; align-items: center; justify-content: center; color: var(--signal); font-size: 20px; flex: 0 0 auto; }
.qtitle { font-size: 15px; font-weight: 600; }
.qdesc { font-size: 12px; color: var(--muted); margin-top: 3px; }

/* ④ 项目表块头 */
.block-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--line); }
.block-title { font-size: 14px; font-weight: 600; letter-spacing: .5px; }
.link-btn { color: var(--signal) !important; font-family: var(--mono); }
.mono { font-family: var(--mono); }

/* 深色标签 */
.dtag { display: inline-block; font-size: 11px; font-family: var(--mono); padding: 2px 9px; border-radius: 3px; border: 1px solid; letter-spacing: .5px; }
.dtag.admin { color: #e05561; border-color: rgba(224,85,97,.35); background: rgba(224,85,97,.07); }
.dtag.member { color: var(--signal); border-color: rgba(0,179,134,.4); background: rgba(0,179,134,.08); }
.dtag.guest { color: var(--muted); border-color: var(--line); background: transparent; }
.dtag.active { color: var(--signal); border-color: rgba(0,179,134,.4); background: rgba(0,179,134,.08); }

/* 深色皮 el-table（仅作用于本页表格） */
.dark-table { background: transparent; --el-table-border-color: var(--line); }
.dark-table :deep(.el-table__inner-wrapper) { background: transparent; }
.dark-table :deep(.el-table__header-wrapper th) {
  background: var(--panel-2) !important; color: var(--muted) !important;
  font-family: var(--mono); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; font-weight: 500;
  border-bottom: 1px solid var(--line);
}
.dark-table :deep(.el-table tr) { background: transparent; }
.dark-table :deep(.el-table td) { border-bottom: 1px solid var(--line); color: var(--fg); }
.dark-table :deep(.el-table__row:hover > td) { background: var(--panel-2) !important; }
.dark-table :deep(.el-table__body tr.hover-row > td) { background: var(--panel-2) !important; }
.dark-table :deep(.el-table__empty-block) { background: transparent; }
.dark-table :deep(.el-table__empty-text) { color: var(--muted); }
.dark-table::before, .dark-table :deep(.el-table__border-left-patch) { display: none; }

/* 错落入场 */
.dashboard > * { animation: dashUp .5s ease-out both; }
.dashboard > *:nth-child(2) { animation-delay: .05s; }
.dashboard > *:nth-child(3) { animation-delay: .12s; }
.dashboard > *:nth-child(4) { animation-delay: .19s; }
.dashboard > *:nth-child(5) { animation-delay: .26s; }
@keyframes dashUp { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }

/* 响应式 */
@media (max-width: 1300px) {
  .kpi-wall { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 980px) {
  .kpi-wall { grid-template-columns: repeat(2, 1fr); }
  .quick { grid-template-columns: repeat(2, 1fr); }
  .hero { flex-direction: column; align-items: flex-start; gap: 18px; }
  .hero-r { text-align: left; }
  .status-row, .proj-cnt { justify-content: flex-start; }
}

@media (prefers-reduced-motion: reduce) {
  .dashboard > *, .ring-arc { animation: none; transition: none; }
  .dot { animation: none; }
}
</style>
