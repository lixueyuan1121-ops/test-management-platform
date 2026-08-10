<template>
  <div class="viz-root">
    <!-- 顶部标题条 + 范围选择 -->
    <div class="head">
      <div>
        <div class="eyebrow">// AI CONTRIBUTION · QA COPILOT</div>
        <h1>AI 战绩墙</h1>
        <div class="sub">QA Copilot 在所选区间内为测试团队生成、被采纳的测试点，以及折算的提效与成本</div>
      </div>
      <div class="controls">
        <el-radio-group v-model="range" size="small" @change="onRangeChange">
          <el-radio-button value="7d">近 7 天</el-radio-button>
          <el-radio-button value="30d">近 30 天</el-radio-button>
          <el-radio-button value="90d">近 90 天</el-radio-button>
          <el-radio-button value="mtd">本月至今</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          range-separator="→"
          start-placeholder="开始"
          end-placeholder="结束"
          :clearable="false"
          class="range-picker"
          @change="onCustomChange"
        />
      </div>
    </div>

    <div v-loading="loading" element-loading-background="rgba(255,255,255,0.6)" class="viz-body">
      <!-- 空态：所选区间无 AI 生成数据 -->
      <div v-if="!loading && isEmpty" class="empty-state panel">
        <div class="grid-bg"></div>
        <div class="es-inner">
          <TargetMark :size="96" :animated="true" class="es-mark" />
          <div class="es-eyebrow">// NO SIGNAL · 暂无 AI 数据</div>
          <div class="es-title">所选区间暂无 AI 生成数据</div>
          <div class="es-desc">
            换个时间范围，或去「AI 测试助手」把需求拆解为测试点，战绩会在这里累积。
          </div>
          <el-button type="primary" class="es-btn" @click="$router.push('/ai-testgen')">
            去 AI 测试助手 →
          </el-button>
          <div class="es-metrics">
            <span>GEN // --</span><i></i>
            <span>ADOPT // --</span><i></i>
            <span>COST // --</span><i></i>
            <span>DUR // --</span>
          </div>
        </div>
      </div>

      <template v-else>
        <!-- 顶部：hero 节省工时 + 4 KPI -->
        <div class="top">
          <div class="panel hero">
            <div class="grid-bg"></div>
            <div class="hero-inner">
              <div class="eyebrow" style="margin-bottom:10px">// 区间内节省工时（估）</div>
              <div class="val">{{ savedHours.toLocaleString() }}<small>人时</small></div>
              <div class="cap">
                按「每条采纳测试点折算
                <b>{{ factor }}</b>
                人时」估算<br>≈ 节省 <b>{{ savedDays }}</b> 个人日
              </div>
              <div class="factor-row">
                <span class="factor-lbl">// 折算系数</span>
                <el-input-number
                  v-model="factor"
                  :min="0"
                  :max="8"
                  :step="0.25"
                  size="small"
                  controls-position="right"
                  class="factor-input"
                  @change="onFactorChange"
                />
                <span class="factor-hint">按 {{ factor }} 人时/条估算</span>
              </div>
              <div class="delta">共采纳 {{ (stats?.total_adopted || 0).toLocaleString() }} 条 · {{ stats?.project_cnt || 0 }} 项目</div>
            </div>
          </div>

          <div class="kpis">
            <div class="panel kpi">
              <div class="lbl">区间内生成测试点</div>
              <div class="num">{{ (stats?.total_generated || 0).toLocaleString() }}</div>
              <div class="foot">across {{ stats?.project_cnt || 0 }} 项目 · {{ stats?.run_cnt || 0 }} 次生成</div>
            </div>
            <div class="panel kpi">
              <div class="lbl">采纳率</div>
              <div class="num">{{ adoptRatePct }}%</div>
              <div class="foot">{{ (stats?.total_adopted || 0).toLocaleString() }} / {{ (stats?.total_reviewed || 0).toLocaleString() }} 已采纳</div>
            </div>
            <div class="panel kpi">
              <div class="lbl">区间内 AI 花费</div>
              <div class="num">${{ (stats?.total_cost_usd || 0).toFixed(2) }}</div>
              <div class="foot">{{ costPerRun }}</div>
            </div>
            <div class="panel kpi">
              <div class="lbl">平均生成耗时</div>
              <div class="num">{{ stats?.avg_duration_s || 0 }}<small style="font-size:15px;color:var(--dim)">s</small></div>
              <div class="foot">按 {{ stats?.run_cnt || 0 }} 次生成均值</div>
            </div>
          </div>
        </div>

        <!-- 主图：生成 vs 采纳 趋势（categorical 双线 + hover） -->
        <div class="panel trend">
          <div class="grid-bg"></div>
          <div class="row-head" style="position:relative;z-index:2">
            <div class="row-title">{{ trendTitle }}：生成 vs 采纳</div>
            <div class="legend">
              <span class="lg"><i style="background:var(--series-gen)"></i>生成</span>
              <span class="lg"><i style="background:var(--series-adopt)"></i>采纳</span>
            </div>
          </div>
          <div class="trend-wrap" style="position:relative;z-index:2">
            <svg class="trend-svg" :viewBox="`0 0 ${VB.w} ${VB.h}`" aria-label="生成与采纳趋势折线图"
                 @mouseleave="hoverIdx = -1">
              <!-- 网格 + y 轴刻度 -->
              <g>
                <line v-for="(tk, i) in tv.ticks" :key="'g' + i"
                      :x1="VB.l" :y1="tk.y" :x2="VB.w - VB.r" :y2="tk.y"
                      stroke="var(--grid)" stroke-width="1" />
                <text v-for="(tk, i) in tv.ticks" :key="'gt' + i"
                      :x="VB.l - 8" :y="tk.y + 4" text-anchor="end"
                      font-size="10" font-family="var(--mono)" fill="var(--muted)">{{ tk.v }}</text>
              </g>
              <!-- x 轴标签（隔一个显示） -->
              <text v-for="lb in tv.xlabels" :key="'x' + lb.i"
                    :x="lb.x" :y="VB.h - 12" text-anchor="middle"
                    font-size="10" font-family="var(--mono)" fill="var(--muted)">{{ lb.label }}</text>
              <!-- 两条线 -->
              <path :d="tv.pathGen" fill="none" stroke="var(--series-gen)" stroke-width="2"
                    stroke-linejoin="round" stroke-linecap="round" />
              <path :d="tv.pathAdopt" fill="none" stroke="var(--series-adopt)" stroke-width="2"
                    stroke-linejoin="round" stroke-linecap="round" />
              <!-- 末点 marker + 直接标签 -->
              <g v-if="tv.last">
                <circle :cx="tv.last.x" :cy="tv.last.yGen" r="4.5" fill="var(--series-gen)"
                        stroke="var(--surface-1)" stroke-width="2" />
                <text :x="tv.last.x + 8" :y="tv.last.yGen + 4" font-size="11" font-family="var(--mono)"
                      font-weight="600" fill="var(--text-primary)">{{ tv.last.gen }}</text>
                <circle :cx="tv.last.x" :cy="tv.last.yAdopt" r="4.5" fill="var(--series-adopt)"
                        stroke="var(--surface-1)" stroke-width="2" />
                <text :x="tv.last.x + 8" :y="tv.last.yAdopt + 4" font-size="11" font-family="var(--mono)"
                      font-weight="600" fill="var(--text-primary)">{{ tv.last.adopt }}</text>
              </g>
              <!-- crosshair + 命中层 -->
              <line v-if="hoverIdx >= 0 && tv.points[hoverIdx]" class="crosshair"
                    :x1="tv.points[hoverIdx].x" :x2="tv.points[hoverIdx].x"
                    :y1="VB.t" :y2="VB.h - VB.b" style="opacity:1" />
              <rect v-for="p in tv.points" :key="'hit' + p.i"
                    :x="p.hitX" :y="VB.t" :width="p.hitW" :height="VB.h - VB.t - VB.b"
                    fill="transparent" @mouseenter="hoverIdx = p.i" />
            </svg>
            <!-- tooltip（百分比定位，随 SVG 自适应缩放） -->
            <div v-if="hoverIdx >= 0 && tv.points[hoverIdx]" class="tt"
                 :style="{ left: tv.points[hoverIdx].pctX + '%', top: tv.points[hoverIdx].pctTop + '%', opacity: 1 }">
              <div class="d">{{ tv.points[hoverIdx].isLast ? '今日 ' : '' }}{{ tv.points[hoverIdx].date }}</div>
              <div class="r"><i style="background:var(--series-gen)"></i>生成 <b>{{ tv.points[hoverIdx].gen }}</b></div>
              <div class="r"><i style="background:var(--series-adopt)"></i>采纳 <b>{{ tv.points[hoverIdx].adopt }}</b>（{{ tv.points[hoverIdx].pct }}%）</div>
            </div>
          </div>
        </div>

        <!-- 底部两栏 -->
        <div class="bottom">
          <!-- 维度覆盖 sequential bar -->
          <div class="panel card">
            <div class="grid-bg"></div>
            <div style="position:relative;z-index:2">
              <div class="row-head"><div class="row-title">测试点维度覆盖</div></div>
              <div class="barrow" v-for="d in dimView" :key="d.name">
                <span class="name">{{ d.name }}</span>
                <div class="bartrack"><div class="barfill" :style="{ width: d.pct + '%', background: d.color }"></div></div>
                <span class="v">{{ d.count }}</span>
              </div>
            </div>
          </div>

          <!-- 采纳率 meter + 优先级 ordinal -->
          <div class="panel card">
            <div class="grid-bg"></div>
            <div style="position:relative;z-index:2">
              <div class="row-head"><div class="row-title">采纳质量</div></div>
              <div class="meter-wrap">
                <div class="meter-top">
                  <span class="pct">{{ adoptRatePct }}%</span>
                  <span class="of">整体采纳率 · 目标 45%</span>
                </div>
                <div class="meter-track"><div class="meter-fill" :style="{ width: adoptRatePct + '%' }"></div></div>
              </div>
              <div class="prio-title">// 采纳测试点的优先级分布</div>
              <div class="prio-bar">
                <div v-for="seg in prioView" :key="seg.p" class="prio-seg"
                     :style="{ flex: seg.n || 0.001, background: seg.color }" :title="`${seg.p}: ${seg.n}`">
                  {{ seg.label }}
                </div>
              </div>
              <div class="prio-legend">
                <span v-for="seg in prioView" :key="'lg' + seg.p"><i :style="{ background: seg.color }"></i>{{ seg.p }} · {{ seg.n }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="foot-note">// 数据源 ai_task / test_case · 配色经 dataviz 校验 · 折算系数可调（本地记忆）</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { aiStats } from '@/api'
import TargetMark from '@/components/TargetMark.vue'

const stats = ref(null)
const loading = ref(false)
const hoverIdx = ref(-1)
const factor = ref(Number(localStorage.getItem('tp_ai_save_factor')) || 0.5)
const range = ref('30d')            // '7d'|'30d'|'90d'|'mtd'|'custom'
const customRange = ref([])

const pad = (n) => String(n).padStart(2, '0')
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

function calcFromTo() {
  const to = new Date()
  let from = new Date()
  if (range.value === '7d') from.setDate(to.getDate() - 6)
  else if (range.value === '30d') from.setDate(to.getDate() - 29)
  else if (range.value === '90d') from.setDate(to.getDate() - 89)
  else if (range.value === 'mtd') from = new Date(to.getFullYear(), to.getMonth(), 1)
  else if (range.value === 'custom' && customRange.value?.length === 2)
    return { from: customRange.value[0], to: customRange.value[1] }
  return { from: fmt(from), to: fmt(to) }
}

async function load() {
  loading.value = true
  hoverIdx.value = -1
  try { stats.value = await aiStats(calcFromTo()) }
  catch { stats.value = null }
  finally { loading.value = false }
}

function onRangeChange() {
  // 切到预设时清掉自定义区间，避免选择器残留旧值
  if (range.value !== 'custom') customRange.value = []
  load()
}
function onCustomChange(val) {
  if (val?.length === 2) { range.value = 'custom'; load() }
}
function onFactorChange(v) {
  const f = Number(v) || 0
  factor.value = f
  localStorage.setItem('tp_ai_save_factor', String(f))
}

onMounted(load)

// ---- hero / KPI 派生 ----
const savedHours = computed(() => Math.round((stats.value?.total_adopted || 0) * factor.value))
const savedDays = computed(() => Math.round(savedHours.value / 8))
const adoptRatePct = computed(() => ((stats.value?.adopt_rate || 0) * 100).toFixed(1))
const costPerRun = computed(() => {
  const runs = stats.value?.run_cnt || 0
  const cost = stats.value?.total_cost_usd || 0
  return runs ? `avg $${(cost / runs).toFixed(3)} / 次` : '暂无生成记录'
})

// ---- 空态：无生成 且 无采纳 且 趋势全 0 ----
const isEmpty = computed(() => {
  const s = stats.value
  if (!s) return true
  const noGen = (s.total_generated || 0) === 0
  const noAdopt = (s.total_adopted || 0) === 0
  const noTrend = (s.trend || []).every((d) => (d.generated || 0) === 0 && (d.adopted || 0) === 0)
  return noGen && noAdopt && noTrend
})

// ---- 维度覆盖：sequential 单 hue，越多越深（按 count 降序取 ramp）----
const SEQ = ['--seq-600', '--seq-450', '--seq-350', '--seq-250', '--seq-100']
const dimView = computed(() => {
  const dims = stats.value?.dims || []
  const max = Math.max(1, ...dims.map((d) => d.count))
  return [...dims].sort((a, b) => b.count - a.count).map((d, i) => ({
    name: d.name,
    count: d.count,
    pct: (d.count / max * 100).toFixed(1),
    color: `var(${SEQ[Math.min(i, SEQ.length - 1)]})`,
  }))
})

// ---- 优先级 ordinal 分段（深=P0 高）----
const PRIO_COLOR = { P0: '--ord-p0', P1: '--ord-p1', P2: '--ord-p2', P3: '--ord-p3' }
const prioView = computed(() => {
  const prio = stats.value?.prio || []
  const total = prio.reduce((s, x) => s + x.n, 0)
  return prio.map((x) => {
    const w = total ? (x.n / total * 100) : 0
    return {
      p: x.p,
      n: x.n,
      color: `var(${PRIO_COLOR[x.p] || '--ord-p2'})`,
      // 内嵌标签仅在够宽时显示（放不下就不硬塞）
      label: w > 10 ? `${x.p} ${x.n}` : '',
    }
  })
})

// ---- 主趋势：双线 categorical + crosshair hover ----
const VB = { w: 720, h: 220, l: 44, r: 16, t: 20, b: 34 }
const trendTitle = computed(() => {
  const n = stats.value?.trend?.length || 0
  return n ? `近 ${n} 天` : '趋势'
})
const tv = computed(() => {
  const data = stats.value?.trend || []
  const innerW = VB.w - VB.l - VB.r
  const innerH = VB.h - VB.t - VB.b
  const empty = { ticks: [], xlabels: [], points: [], pathGen: '', pathAdopt: '', last: null }
  if (!data.length) return empty

  const maxRaw = Math.max(...data.map((d) => Math.max(d.generated, d.adopted)))
  const maxY = Math.max(20, Math.ceil(maxRaw / 20) * 20)   // 至少 20，向上取整到 20
  const X = (i) => VB.l + innerW * (data.length === 1 ? 0 : i / (data.length - 1))
  const Y = (v) => VB.t + innerH * (1 - v / maxY)
  const seg = data.length > 1 ? innerW / (data.length - 1) : innerW

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = Math.round(maxY * f)
    return { v, y: +Y(v).toFixed(1) }
  })
  const xlabels = data.map((d, i) => ({ i, x: +X(i).toFixed(1), label: i === data.length - 1 ? '今日' : d.date }))
    .filter((_, i) => i % 2 === 0 || i === data.length - 1)

  const pathOf = (key) => data.map((d, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(d[key]).toFixed(1)}`).join(' ')

  const points = data.map((d, i) => {
    const x = +X(i).toFixed(1)
    const yTop = Y(Math.max(d.generated, d.adopted))
    return {
      i,
      x,
      date: d.date,
      gen: d.generated,
      adopt: d.adopted,
      pct: d.generated ? Math.round(d.adopted / d.generated * 100) : 0,
      isLast: i === data.length - 1,
      hitX: +(x - (data.length > 1 ? seg / 2 : innerW / 2)).toFixed(1),
      hitW: +(data.length > 1 ? seg : innerW).toFixed(1),
      pctX: +(x / VB.w * 100).toFixed(2),
      pctTop: +(yTop / VB.h * 100).toFixed(2),
    }
  })
  const li = data.length - 1
  const last = { x: +X(li).toFixed(1), yGen: +Y(data[li].generated).toFixed(1), yAdopt: +Y(data[li].adopted).toFixed(1), gen: data[li].generated, adopt: data[li].adopted }

  return { ticks, xlabels, points, pathGen: pathOf('generated'), pathAdopt: pathOf('adopted'), last }
})
</script>

<style scoped>
.viz-root {
  color-scheme: light;
  /* —— 平台科技风 chrome（取自 theme.css / Dashboard.vue）—— */
  --bg:            #f4f6f9;
  --surface-1:     #ffffff;
  --surface-2:     #f7f9fc;
  --line:          #e3e8ef;
  --line-strong:   #d0d7e2;
  --text-primary:  #1a1d21;
  --text-secondary:#52514e;
  --muted:         #6b7280;
  --dim:           #9aa3b2;
  --signal:        #00b386;      /* 品牌强调色（chrome/hero，不做数据识别） */
  --signal-weak:   rgba(0,179,134,0.10);
  --mono: 'JetBrains Mono','SFMono-Regular',ui-monospace,'Menlo',monospace;
  --sans: system-ui,-apple-system,'Segoe UI',sans-serif;

  /* —— dataviz 校验过的数据色 —— */
  --series-gen:    #2a78d6;      /* categorical slot1 blue —— 生成 */
  --series-adopt:  #1baf7a;      /* categorical slot2 aqua —— 采纳 */
  --seq-100:#cde2fb; --seq-250:#86b6ef; --seq-350:#5598e7; --seq-450:#2a78d6; --seq-600:#184f95;
  --ord-p0:#184f95; --ord-p1:#2a78d6; --ord-p2:#5598e7; --ord-p3:#86b6ef; /* 优先级 ordinal（深=P0 高） */
  --status-good:#0ca30c; --status-warn:#fab219; --status-critical:#d03b3b;
  --grid: #e8ecf2;

  /* 铺满内容区底色：抵消 el-main 的 20px padding（对齐 Dashboard） */
  background: var(--bg); color: var(--text-primary);
  font-family: var(--sans);
  margin: -20px; padding: 20px;
  min-height: calc(100vh - 60px);
}
/* dark 值声明两份：媒体查询覆盖 OS 深色偏好；data-theme 属性覆盖手动切换（双向生效）。
   平台目前仅亮色，故页内不提供明暗切换按钮；dark 变量块保留以备将来接入全站暗色。 */
:root[data-theme="dark"] .viz-root,
.viz-root[data-theme="dark"] {
  color-scheme: dark;
  --bg:#0d0d0d; --surface-1:#1a1a19; --surface-2:#222220; --line:#2c2c2a; --line-strong:#383835;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781; --dim:#6d6c66;
  --signal-weak:rgba(0,179,134,0.14);
  --series-gen:#3987e5; --series-adopt:#199e70;
  --seq-100:#184f95; --seq-250:#1c5cab; --seq-350:#256abf; --seq-450:#3987e5; --seq-600:#6da7ec;
  --ord-p0:#184f95; --ord-p1:#3987e5; --ord-p2:#6da7ec; --ord-p3:#9ec5f4;
  --grid:#2c2c2a;
}
@media (prefers-color-scheme: dark) {
  .viz-root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --bg:#0d0d0d; --surface-1:#1a1a19; --surface-2:#222220; --line:#2c2c2a; --line-strong:#383835;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781; --dim:#6d6c66;
    --signal-weak:rgba(0,179,134,0.14);
    --series-gen:#3987e5; --series-adopt:#199e70;
    --seq-100:#184f95; --seq-250:#1c5cab; --seq-350:#256abf; --seq-450:#3987e5; --seq-600:#6da7ec;
    --ord-p0:#184f95; --ord-p1:#3987e5; --ord-p2:#6da7ec; --ord-p3:#9ec5f4;
    --grid:#2c2c2a;
  }
}

/* panel 通用（对齐 Dashboard .panel） */
.panel { position: relative; background: var(--surface-1); border: 1px solid var(--line);
  border-radius: 8px; overflow: hidden; box-shadow: 0 1px 2px rgba(16,24,40,0.04); }
.grid-bg { position:absolute; inset:0; pointer-events:none; opacity:.55;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:40px 40px;
  -webkit-mask-image:radial-gradient(120% 120% at 15% 0%,#000 30%,transparent 80%);
  mask-image:radial-gradient(120% 120% at 15% 0%,#000 30%,transparent 80%); }
.eyebrow { font-family:var(--mono); font-size:12px; letter-spacing:3px; color:var(--signal); text-transform:uppercase; }

/* 顶部标题条 */
.head { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin-bottom:16px; flex-wrap:wrap; }
.head h1 { font-size:24px; font-weight:700; letter-spacing:1px; margin-top:12px; }
.head .sub { font-size:13px; color:var(--muted); margin-top:8px; }
.controls { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.range-picker { width:250px; }

.viz-body { display:flex; flex-direction:column; gap:14px; }

/* hero + KPI 行 */
.top { display:grid; grid-template-columns: 1.3fr 2fr; gap:14px; }
.hero { padding:24px 28px; display:flex; flex-direction:column; justify-content:center; }
.hero-inner { position:relative; z-index:2; }
.hero .val { font-family:var(--mono); font-size:52px; font-weight:700; line-height:1.05; letter-spacing:1px;
  color:var(--signal); font-variant-numeric:tabular-nums; filter:drop-shadow(0 0 10px rgba(0,179,134,.24)); }
.hero .val small { font-family:var(--sans); font-size:18px; color:var(--dim); margin-left:6px; font-weight:400; letter-spacing:0; }
.hero .cap { font-size:13px; color:var(--muted); margin-top:12px; line-height:1.55; }
.hero .cap b { color:var(--text-primary); }
.factor-row { display:flex; align-items:center; gap:10px; margin-top:14px; flex-wrap:wrap; }
.factor-lbl { font-family:var(--mono); font-size:11px; letter-spacing:.5px; color:var(--muted); }
.factor-input { width:120px; }
.factor-hint { font-family:var(--mono); font-size:11px; color:var(--dim); letter-spacing:.3px; }
.hero .delta { font-family:var(--mono); font-size:12px; letter-spacing:.5px; margin-top:12px; color:var(--signal); }

.kpis { display:grid; grid-template-columns:repeat(2,1fr); gap:14px; }
.kpi { padding:18px; transition:border-color .18s ease, box-shadow .2s ease, transform .18s ease; }
.kpi .lbl { font-family:var(--mono); font-size:10px; letter-spacing:1.5px; color:var(--muted); text-transform:uppercase; }
.kpi .num { font-family:var(--mono); font-size:34px; font-weight:700; line-height:1.1; margin-top:10px;
  color:var(--signal); font-variant-numeric:tabular-nums; }
.kpi .num small { font-family:var(--sans); font-size:15px; color:var(--dim); margin-left:3px; font-weight:400; }
.kpi .foot { font-size:11px; color:var(--muted); margin-top:8px; font-family:var(--mono); letter-spacing:.3px; }
.kpi:hover { border-color:var(--signal); box-shadow:0 0 16px rgba(0,179,134,.18); transform:translateY(-2px); }

/* 主趋势图 */
.trend { padding:20px 24px; }
.row-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:14px; }
.row-title { font-size:14px; font-weight:600; letter-spacing:.3px; display:flex; align-items:center; gap:8px; }
.row-title::before { content:''; width:3px; height:13px; background:var(--signal); border-radius:2px;
  box-shadow:0 0 6px rgba(0,179,134,.5); }
.legend { font-family:var(--mono); font-size:11px; display:flex; gap:16px; }
.lg { display:inline-flex; align-items:center; gap:6px; color:var(--muted); }
.lg i { width:14px; height:3px; border-radius:2px; display:inline-block; }
.trend-wrap { position:relative; }
.trend-svg { width:100%; height:auto; display:block; overflow:visible; }
.crosshair { stroke:var(--line-strong); stroke-width:1; stroke-dasharray:3 3; }
.tt { position:absolute; pointer-events:none; transform:translate(-50%,-115%);
  background:var(--surface-1); border:1px solid var(--line-strong); border-radius:6px; padding:8px 10px;
  font-size:12px; box-shadow:0 4px 14px rgba(16,24,40,.12); white-space:nowrap; transition:opacity .1s; z-index:5; }
.tt .d { font-family:var(--mono); color:var(--muted); font-size:10px; margin-bottom:4px; letter-spacing:.5px; }
.tt .r { display:flex; align-items:center; gap:6px; margin-top:2px; }
.tt .r i { width:8px; height:8px; border-radius:2px; }
.tt .r b { font-variant-numeric:tabular-nums; }

/* 底部两栏 */
.bottom { display:grid; grid-template-columns:1.1fr 1fr; gap:14px; }
.card { padding:20px 24px; }

/* 维度覆盖：sequential 横向 bar */
.barrow { display:flex; align-items:center; gap:12px; margin:11px 0; }
.barrow .name { font-size:12px; color:var(--text-secondary); width:52px; flex:0 0 auto; font-family:var(--mono); }
.bartrack { flex:1; height:16px; background:var(--surface-2); border-radius:3px; overflow:hidden; position:relative; }
.barfill { height:100%; border-radius:3px 0 0 3px; transition:width .4s ease; }
.barrow .v { font-size:12px; color:var(--text-primary); width:34px; text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono); flex:0 0 auto; }

/* 采纳率 meter */
.meter-wrap { margin-bottom:22px; }
.meter-top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.meter-top .pct { font-family:var(--mono); font-size:30px; font-weight:700; color:var(--signal); font-variant-numeric:tabular-nums; letter-spacing:.5px; }
.meter-top .of { font-family:var(--mono); font-size:11px; color:var(--muted); }
.meter-track { height:12px; background:var(--surface-2); border-radius:6px; overflow:hidden; }
.meter-fill { height:100%; background:var(--signal); border-radius:6px; box-shadow:0 0 8px rgba(0,179,134,.35); transition:width .4s ease; }

/* 优先级 ordinal 分段 */
.prio-title { font-size:12px; color:var(--muted); font-family:var(--mono); letter-spacing:.5px; margin-bottom:10px; }
.prio-bar { display:flex; height:26px; border-radius:4px; overflow:hidden; gap:2px; background:var(--surface-1); }
.prio-seg { display:flex; align-items:center; justify-content:center; font-size:11px; font-family:var(--mono);
  color:#fff; min-width:28px; }
.prio-legend { display:flex; gap:14px; margin-top:10px; font-family:var(--mono); font-size:11px; color:var(--muted); flex-wrap:wrap; }
.prio-legend span { display:inline-flex; align-items:center; gap:5px; }
.prio-legend i { width:9px; height:9px; border-radius:2px; }

.foot-note { margin-top:6px; font-family:var(--mono); font-size:11px; color:var(--dim); letter-spacing:.5px; text-align:center; }

/* 空态卡（仿 Dashboard） */
.empty-state { min-height:420px; display:flex; align-items:center; justify-content:center; padding:40px 24px; }
.es-inner { position:relative; z-index:2; display:flex; flex-direction:column; align-items:center; text-align:center; }
.es-mark { --tm-line:#cbd5e1; --tm-dim:#94a3b8; --tm-signal:var(--signal);
  filter:drop-shadow(0 0 10px rgba(0,179,134,.25)); margin-bottom:22px; }
.es-eyebrow { font-family:var(--mono); font-size:12px; letter-spacing:3px; color:var(--signal); }
.es-title { font-size:22px; font-weight:700; letter-spacing:.5px; margin-top:12px; color:var(--text-primary); }
.es-desc { font-size:13px; color:var(--muted); margin-top:8px; max-width:440px; line-height:1.6; }
.es-btn { margin-top:24px; font-family:var(--mono); letter-spacing:.5px; }
.es-metrics { display:flex; align-items:center; gap:14px; margin-top:30px;
  font-family:var(--mono); font-size:11px; letter-spacing:1.5px; color:var(--dim); }
.es-metrics i { width:1px; height:12px; background:var(--line); display:inline-block; }

/* 错落入场 */
.viz-body > * { animation: vizUp .5s ease-out both; }
.viz-body > *:nth-child(2) { animation-delay:.06s; }
.viz-body > *:nth-child(3) { animation-delay:.12s; }
@keyframes vizUp { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }

/* 响应式 */
@media (max-width: 1080px) {
  .top { grid-template-columns:1fr; }
  .bottom { grid-template-columns:1fr; }
}
@media (max-width: 560px) {
  .kpis { grid-template-columns:1fr; }
  .range-picker { width:100%; }
}
@media (prefers-reduced-motion: reduce) {
  .viz-body > *, .barfill, .meter-fill { animation:none; transition:none; }
}
</style>
