<template>
  <div class="ai-evalgen">
    <!-- 输入区 -->
    <el-card class="input-card">
      <template #header>
        <div class="card-head">
          <div class="title-wrap">
            <el-icon class="title-icon"><ChatDotRound /></el-icon>
            <div>
              <div class="title">对话测评生成 · Eval Copilot</div>
              <div class="subtitle">粘贴需求，AI 按选定维度拆解出可评测的对话 query 清单（含多轮）</div>
            </div>
          </div>
          <el-tag v-if="!aiAvailable" type="danger" effect="light" round>AI 服务不可用</el-tag>
          <!-- 仅在有 2 个及以上可用引擎时才显示切换器;只有一个引擎无需切换,直接隐藏 -->
          <div v-else-if="availProviders.length > 1" class="engine-picker">
            <span class="engine-label">生成引擎</span>
            <el-radio-group v-model="engine" size="small" :disabled="running" class="engine-seg">
              <el-radio-button
                v-for="p in availProviders"
                :key="p.id"
                :value="p.id"
              >
                <span class="eng-opt">
                  <i class="eng-dot" :style="{ background: engineMeta(p.id).dot }"></i>
                  {{ engineMeta(p.id).label }}
                </span>
              </el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </template>

      <div class="form-row">
        <el-select v-model="pid" placeholder="选择项目" style="width:200px" :disabled="running" @change="onProjectChange">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <!-- 关联测评任务（可选）：下拉列表是本项目的测评任务 -->
        <el-select v-model="taskId" placeholder="关联测评任务（可选）" style="width:220px" clearable :disabled="running || !pid">
          <el-option v-for="t in evalTasks" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
      </div>

      <!-- 测评维度多选:至少选一个 -->
      <div class="dim-head">
        <span class="dim-label">测评维度</span>
        <span class="dim-sub">至少选一个;multi_turn(多轮追问)会产出同组多条 query</span>
      </div>
      <el-checkbox-group v-model="dimensions" :disabled="running" class="dim-group">
        <el-checkbox v-for="d in DIMENSIONS" :key="d.k" :value="d.k" border>{{ d.label }}</el-checkbox>
      </el-checkbox-group>

      <div class="req-head">
        <span class="req-label">需求内容</span>
        <el-button text type="primary" size="small" :disabled="running" @click="fillDemo">填充示例需求</el-button>
      </div>
      <el-input
        v-model="requirement"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 14 }"
        placeholder="粘贴需求描述、PRD 片段或验收标准……越具体，生成的测评 query 越贴合。"
        :disabled="running"
        class="req-input"
      />

      <div class="actions">
        <el-button
          type="primary"
          size="large"
          :loading="running"
          :disabled="!aiAvailable || !pid || !requirement.trim() || !dimensions.length"
          @click="generate"
        >
          <el-icon v-if="!running" class="btn-icon"><MagicStick /></el-icon>
          {{ running ? '生成中…' : '生成测评 query' }}
        </el-button>
        <el-button v-if="running" size="large" @click="cancel">取消</el-button>
      </div>
    </el-card>

    <!-- 生成过程反馈 -->
    <el-card v-if="running" class="stream-card">
      <div class="running-head">
        <span class="pulse-dot" />
        <span class="running-text">{{ phaseText }}</span>
        <span class="elapsed mono">{{ (elapsed / 1000).toFixed(1) }}s</span>
      </div>
      <el-progress :percentage="100" :indeterminate="true" :duration="3" :show-text="false" color="#00b386" />
      <pre v-if="rawStream" class="raw-stream">{{ rawStream }}</pre>
      <div v-else class="raw-hint">AI 正在阅读需求并设计测评 query，通常需要 30–60 秒，请稍候…</div>
    </el-card>

    <!-- 结果区 -->
    <el-card v-if="queries.length" class="result-card">
      <template #header>
        <div class="card-head">
          <span class="result-title">测评 query 清单 · {{ queries.length }} 条</span>
          <div class="dispatch-bar">
            <span v-if="selectedQueries.length" class="sel-info">已选 {{ selectedQueries.length }} 条</span>
            <el-select
              v-model="chosenRunner" size="small" style="width:180px"
              :placeholder="devices.length ? '选择执行机' : '未登记设备'"
              no-data-text="去『我的设备』注册"
              @change="loadClientDevices"
            >
              <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
            </el-select>
            <el-select
              v-model="chosenDevice" size="small" style="width:200px" clearable
              :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'"
              no-data-text="CLI platform 连客户端后自动上报"
            >
              <el-option
                v-for="dev in clientDevices" :key="dev.vm_id"
                :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`"
                :value="dev.vm_id"
              />
            </el-select>
            <el-button
              type="success" size="small" :loading="dispatching"
              :disabled="!selectedQueries.length || !chosenRunner"
              @click="dispatchSelected"
            >发送到执行机</el-button>
          </div>
        </div>
      </template>

      <!-- 战绩统计条 -->
      <div v-if="meta" class="stat-strip">
        <div class="stat"><div class="stat-num">{{ meta.case_count ?? queries.length }}</div><div class="stat-label">query</div></div>
        <div class="stat"><div class="stat-num">{{ meta.duration_ms ? (meta.duration_ms / 1000).toFixed(1) + 's' : '—' }}</div><div class="stat-label">生成耗时</div></div>
        <div class="stat"><div class="stat-num">{{ meta.output_tokens ?? '—' }}</div><div class="stat-label">输出 tokens</div></div>
        <div class="stat"><div class="stat-num">{{ meta.cost_usd != null ? '$' + meta.cost_usd.toFixed(3) : '—' }}</div><div class="stat-label">成本</div></div>
      </div>

      <el-table :data="sortedQueries" size="small" border stripe class="case-table" @selection-change="(s) => (selectedQueries = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column label="维度" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="dimTagType(row.dimension)" effect="plain" size="small">{{ dimLabel(row.dimension) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="180">
          <template #default="{ row }"><div>{{ row.title }}</div></template>
        </el-table-column>
        <el-table-column label="提问 prompt" min-width="240">
          <template #default="{ row }"><span class="multiline">{{ row.prompt || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期 expected" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="对话组" min-width="120">
          <template #default="{ row }"><span class="mono cg">{{ row.conversation_group || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="轮次" width="70" align="center">
          <template #default="{ row }"><span class="mono">{{ row.turn_index ?? 0 }}</span></template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { MagicStick, ChatDotRound } from '@element-plus/icons-vue'
import { aiStatus, streamEvalQueries, listMyDevices, enqueueEvalQueries, listEvalDevices, listEvalDimensions, listEvalTasks } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 测评维度(从服务端动态拉取,前后端单一事实来源)。拉取前用内置兜底,拉取成功后替换。
const DIMENSIONS_DEFAULT = [
  { k: 'thinking', label: '思考推理' },
  { k: 'tool_use', label: '工具·MCP调用' },
  { k: 'artifact', label: '产物生成' },
  { k: 'multi_turn', label: '多轮追问' },
  { k: 'instruction', label: '指令遵循' },
  { k: 'workflow', label: '工作流' },
  { k: 'clarification', label: '反问澄清' },
  { k: 'context', label: '上下文记忆' },
  { k: 'safety', label: '安全合规' },
  { k: 'refusal', label: '拒答质量' },
  { k: 'hallucination', label: '事实可靠' },
  { k: 'creativity', label: '创意生成' },
  { k: 'consistency', label: '一致性' },
]
const DIMENSIONS = ref(DIMENSIONS_DEFAULT)
const DIM_TYPE_MAP = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning',
  multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success',
  safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}
const DIM_LABEL_MAP = computed(() => Object.fromEntries(DIMENSIONS.value.map((d) => [d.k, d.label])))
const dimLabel = (k) => DIM_LABEL_MAP.value[k] || k || '—'
const dimTagType = (k) => DIM_TYPE_MAP[k] || 'info'
// 生成引擎 → 选择器友好名 + 圆点色(与 AITestGen 口径一致)；未知引擎回落 id + 灰点
const ENGINE_META = {
  claude: { label: 'Claude', dot: '#f59e0b' },
  deepseek: { label: 'DeepSeek', dot: '#3b82f6' },
}
const engineMeta = (id) => ENGINE_META[id] || { label: id, dot: '#94a3b8' }
const PHASES = ['正在拆解需求要点…', '按维度设计对话意图…', '补充多轮追问与预期…', '整理成稿…']
const DEMO = `纳米 AI 搜索助手：
1. 用户可就一个话题连续追问，助手需保持上下文连贯。
2. 涉及实时信息（天气/股价/新闻）时应联网检索后作答，并给出来源。
3. 可要求助手生成一份带表格的调研报告（产物）。
4. 回答需严格遵循用户指定的格式与字数约束。`

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const evalTasks = ref([])   // 本项目的测评任务列表（关联任务下拉）
const taskId = ref(null)
const requirement = ref('')
const dimensions = ref(['thinking'])   // 默认勾选「思考推理」,保证可直接提交
const aiAvailable = ref(true)
const providers = ref([])          // [{id, available}] 来自 /ai/status
const engine = ref('claude')       // 当前选中的生成引擎
// 只展示「可用」引擎:选择器仅在可用引擎 ≥2 个时渲染(见模板);单引擎场景直接隐藏。
const availProviders = computed(() => providers.value.filter((p) => p.available))

const running = ref(false)
const rawStream = ref('')
const elapsed = ref(0)          // 毫秒
const phaseIdx = ref(0)
const queries = ref([])
const meta = ref(null)

// 下发到执行机:勾选的 query + 我的设备(runner)→ /api/eval-queue/enqueue
const selectedQueries = ref([])
const devices = ref([])            // 我的执行设备(复用 listMyDevices)
const chosenRunner = ref('')       // 选中的 runner_id
const clientDevices = ref([])       // 选中执行机上报的客户端设备(vm)列表
const chosenDevice = ref('')        // 选中的目标设备 vm_id(空=用执行机当前设备)
const dispatching = ref(false)

let timer = null
let ctrl = null

const phaseText = computed(() => PHASES[Math.min(phaseIdx.value, PHASES.length - 1)])
// 多轮同组的按 conversation_group 聚拢、组内按 turn_index 升序(便于阅读对话顺序)
const sortedQueries = computed(() =>
  [...queries.value].sort((a, b) =>
    String(a.conversation_group || '').localeCompare(String(b.conversation_group || ''))
    || (a.turn_index ?? 0) - (b.turn_index ?? 0)))

onMounted(async () => {
  // aiStatus / 项目列表 / 我的设备 / 维度注册表 互无依赖，并行拉取
  const [aiRes, projRes, devRes, dimRes] = await Promise.allSettled([aiStatus(), app.fetchProjects(), listMyDevices(), listEvalDimensions()])
  const aiData = aiRes.status === 'fulfilled' ? (aiRes.value || {}) : {}
  aiAvailable.value = !!aiData.available
  providers.value = aiData.providers || []
  // 维度注册表:服务端为准(key→k/label 映射),失败保持内置兜底
  if (dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length) {
    DIMENSIONS.value = dimRes.value.dimensions.map((d) => ({ k: d.key, label: d.label }))
  }
  // 默认选中:后端 default 若可用则用它,否则第一个可用引擎,再兜底 claude
  const firstAvail = providers.value.find((p) => p.available)
  const dft = providers.value.find((p) => p.id === aiData.default && p.available)
  engine.value = (dft || firstAvail || { id: 'claude' }).id
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
  // 我的设备(下发选 runner);默认选中第一台,便于直接下发
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
  if (devices.value.length) { chosenRunner.value = devices.value[0].runner_id; await loadClientDevices() }
})

async function onProjectChange() {
  taskId.value = null
  queries.value = []
  meta.value = null
  if (!pid.value) { evalTasks.value = []; return }
  setLastProjectId(pid.value)
  try { evalTasks.value = await listEvalTasks(pid.value) || [] } catch { evalTasks.value = [] }
}

function fillDemo() { requirement.value = DEMO }

function generate() {
  if (!pid.value || !requirement.value.trim() || !dimensions.value.length) return
  queries.value = []
  meta.value = null
  rawStream.value = ''
  elapsed.value = 0
  phaseIdx.value = 0
  running.value = true

  const startedAt = Date.now()
  timer = setInterval(() => {
    elapsed.value = Date.now() - startedAt
    phaseIdx.value = Math.floor(elapsed.value / 12000)  // 每 ~12s 推进一档文案
  }, 100)

  ctrl = new AbortController()
  streamEvalQueries(
    { project_id: pid.value, task_id: taskId.value, input_type: 'text', provider: engine.value, requirement: requirement.value, dimensions: dimensions.value },
    {
      signal: ctrl.signal,
      onDelta: (t) => { rawStream.value += t },
      onDone: (evt) => {
        queries.value = evt.queries || []
        meta.value = evt.meta || null
        if (evt.status === 'failed') ElMessage.error(evt.msg || '生成失败，未得到有效 query')
        else ElMessage.success(`已生成 ${queries.value.length} 条测评 query`)
        stop()
      },
      onError: (msg) => { ElMessage.error(msg || '生成失败'); stop() },
    },
  )
}

function stop() {
  running.value = false
  if (timer) { clearInterval(timer); timer = null }
  ctrl = null
}

function cancel() { ctrl?.abort() }

// 选中执行机后,拉该执行机上报的客户端设备(vm)供下拉选。执行机变了要重拉、重置已选设备。
async function loadClientDevices() {
  chosenDevice.value = ''
  clientDevices.value = []
  if (!chosenRunner.value) return
  try { clientDevices.value = await listEvalDevices(chosenRunner.value) || [] } catch { clientDevices.value = [] }
}

// 下发选中 query 到执行机:调 /api/eval-queue/enqueue(target_engine 固定 namiwork)。
// projectId 用本页 pid;eval_query_ids 取选中行的 id(done 帧 query 已含 DB id)。
async function dispatchSelected() {
  if (!selectedQueries.value.length || !chosenRunner.value) return
  dispatching.value = true
  try {
    const res = await enqueueEvalQueries({
      project_id: pid.value,
      runner: chosenRunner.value,
      target_engine: 'namiwork',
      target_device: chosenDevice.value || null,
      eval_query_ids: selectedQueries.value.map((q) => q.id),
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条到 ${chosenRunner.value}(批次 ${res.batch_id})`)
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}
</script>

<style scoped>
.ai-evalgen { display: flex; flex-direction: column; gap: 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon {
  font-size: 26px; color: #00b386;
  filter: drop-shadow(0 0 6px rgba(0, 179, 134, 0.35));
}
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }

/* 生成引擎分段切换 */
.engine-picker { display: flex; align-items: center; gap: 10px; }
.engine-label { font-size: 12px; color: #8a94a6; white-space: nowrap; }
.eng-opt { display: inline-flex; align-items: center; gap: 6px; }
.eng-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none;
  box-shadow: 0 0 0 2px rgba(255,255,255,.6); }
.engine-seg :deep(.el-radio-button__inner) {
  border: none; background: transparent; color: #5b6472; font-weight: 500;
  padding: 5px 14px; box-shadow: none; transition: all .15s ease;
}
.engine-seg { background: #f0f2f5; border: 1px solid #e3e8ef; border-radius: 16px; padding: 2px; }
.engine-seg :deep(.el-radio-button:first-child .el-radio-button__inner),
.engine-seg :deep(.el-radio-button:last-child .el-radio-button__inner) { border-radius: 14px; }
.engine-seg :deep(.el-radio-button__inner:hover) { color: #1f2d3d; }
.engine-seg :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: #00b386; color: #fff; border-radius: 14px;
  box-shadow: 0 1px 4px rgba(0,179,134,.35);
}
.engine-seg :deep(.el-radio-button.is-active .eng-dot) { box-shadow: 0 0 0 2px rgba(255,255,255,.5); }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }

.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }

/* 维度多选 */
.dim-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.dim-label { font-size: 13px; font-weight: 600; color: #1a1d21; }
.dim-sub { font-size: 12px; color: #a0a8b3; }
.dim-group { margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px; }
.dim-group :deep(.el-checkbox) { margin-right: 0; }

.req-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.req-label { font-size: 13px; font-weight: 600; color: #1a1d21; }
.req-input { font-size: 14px; }
.actions { margin-top: 14px; display: flex; gap: 12px; }
.btn-icon { margin-right: 4px; }

/* 生成过程 */
.stream-card :deep(.el-card__body) { padding: 16px 20px; }
.running-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.running-text { font-size: 14px; color: #303133; flex: 1; }
.elapsed { font-size: 13px; color: #00926e; }
.pulse-dot {
  width: 10px; height: 10px; border-radius: 50%; background: #00b386;
  box-shadow: 0 0 0 0 rgba(0, 179, 134, 0.6); animation: pulse 1.4s infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(0, 179, 134, 0.5); }
  70% { box-shadow: 0 0 0 10px rgba(0, 179, 134, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 179, 134, 0); }
}
.raw-stream {
  margin-top: 12px; max-height: 220px; overflow: auto;
  background: #0f1c2e; color: #7fe7c4; border-radius: 6px; padding: 12px;
  font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px;
  white-space: pre-wrap; word-break: break-all;
}
.raw-hint { margin-top: 10px; font-size: 13px; color: #909399; }

/* 战绩统计条 */
.stat-strip { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.stat {
  flex: 1; min-width: 96px; text-align: center;
  padding: 12px 8px; border-radius: 8px;
  background: linear-gradient(160deg, #f3fbf8, #eaf5ff);
  border: 1px solid #e3eef0;
}
.stat-num { font-size: 22px; font-weight: 700; color: #00926e; font-family: 'JetBrains Mono', ui-monospace, monospace; }
.stat-label { font-size: 12px; color: #8a94a6; margin-top: 4px; }

.result-title { font-weight: 600; color: #1f2d3d; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; }
.sel-info { font-weight: 600; color: #00926e; font-size: 13px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.cg { font-size: 12px; color: #5a6b7b; }
.case-table { margin-top: 4px; }
</style>
