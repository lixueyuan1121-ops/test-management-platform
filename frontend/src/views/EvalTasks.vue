<template>
  <div class="eval-tasks">
    <el-card>
      <template #header>
        <div class="head">
          <div class="title-wrap">
            <el-icon class="title-icon"><Tickets /></el-icon>
            <div>
              <div class="title">测评任务</div>
              <div class="subtitle">定制用例集合 → 整体执行 → 逐条结果 + AI 综合评价</div>
            </div>
          </div>
          <div class="head-right">
            <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button type="primary" :icon="Plus" :disabled="!pid" @click="openEdit(null)">新建任务</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!loading && !tasks.length" description="暂无测评任务，点右上角「新建任务」创建" :image-size="70" />
      <el-table v-else :data="tasks" v-loading="loading" size="small" border stripe>
        <el-table-column prop="id" label="#" width="60" align="center" />
        <el-table-column label="任务名" min-width="160">
          <template #default="{ row }"><b class="tname" @click="openDetail(row)">{{ row.name }}</b></template>
        </el-table-column>
        <el-table-column label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="80" align="center">
          <template #default="{ row }"><span class="mono">{{ row.query_ids.length }}</span></template>
        </el-table-column>
        <el-table-column label="对话选项" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="fmtDialogOptions(row.dialog_options)" class="opts">{{ fmtDialogOptions(row.dialog_options) }}</span>
            <span v-else class="muted">默认</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="TS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ TS_LABEL[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近执行" width="150" align="center">
          <template #default="{ row }">
            <span v-if="row.last_batch_id" class="mono batch">{{ row.last_batch_id }}<br/>{{ row.done_count }}/{{ row.run_count }} 完成</span>
            <span v-else class="muted">未执行</span>
          </template>
        </el-table-column>
        <el-table-column label="综合评价" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.summary_status==='done'" type="success" size="small">已生成</el-tag>
            <el-tag v-else-if="row.summary_status==='running'" type="warning" size="small">生成中</el-tag>
            <el-tag v-else-if="row.summary_status==='failed'" type="danger" size="small">失败</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openDetail(row)">详情/结果</el-button>
            <el-button size="small" type="success" text @click="openRun(row)">执行</el-button>
            <el-button size="small" text @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="删除该任务？(执行记录保留)" @confirm="removeTask(row)">
              <template #reference><el-button size="small" type="danger" text>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑任务 -->
    <el-dialog v-model="editVisible" :title="editing?.id ? '编辑测评任务' : '新建测评任务'" width="860px" top="6vh">
      <el-form label-width="80px">
        <el-form-item label="任务名" required>
          <el-input v-model="editForm.name" maxlength="128" placeholder="如：多轮上下文专项 / v2.3 回归测评" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="这个任务考察什么(会喂给综合评价 AI 做背景)" />
        </el-form-item>
        <el-form-item label="用例">
          <div class="qpick">
            <div class="qpick-head">
              <span>勾选纳入任务的用例（已选 {{ editForm.query_ids.length }} 条）</span>
              <el-button size="small" type="primary" text :icon="Plus" @click="customVisible = true">新增自定义用例</el-button>
            </div>
            <el-table :data="allQueries" size="small" border max-height="360" @selection-change="s => editForm.query_ids = s.map(q => q.id)" ref="qTable">
              <el-table-column type="selection" width="40" />
              <el-table-column label="维度" width="104" align="center">
                <template #default="{ row }"><el-tag size="small" effect="plain" :type="DIM_TAG_TYPE[row.dimension] || 'info'">{{ dimLabel(row.dimension) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="标题" min-width="150" show-overflow-tooltip><template #default="{ row }">{{ row.title }}</template></el-table-column>
              <el-table-column label="提问" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ row.prompt }}</template></el-table-column>
              <el-table-column label="来源" width="80" align="center">
                <template #default="{ row }"><span class="muted">{{ row.ai_task_id ? 'AI' : '手工' }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!editForm.name.trim()" @click="saveTask">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增自定义用例 -->
    <el-dialog v-model="customVisible" title="新增自定义用例" width="560px" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="标题" required><el-input v-model="customForm.title" maxlength="512" /></el-form-item>
        <el-form-item label="维度">
          <el-select v-model="customForm.dimension" clearable placeholder="选主考维度(可空)">
            <el-option v-for="d in DIMENSIONS" :key="d.k" :label="d.label" :value="d.k" />
          </el-select>
        </el-form-item>
        <el-form-item label="提问" required>
          <el-input v-model="customForm.prompt" type="textarea" :rows="4" placeholder="发给被测大模型的完整提问" />
        </el-form-item>
        <el-form-item label="期望">
          <el-input v-model="customForm.expected" type="textarea" :rows="3" placeholder="期望被测模型做到什么(判定参照,建议填写)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="customVisible = false">取消</el-button>
        <el-button type="primary" :loading="customSaving" :disabled="!customForm.title.trim() || !customForm.prompt.trim()" @click="saveCustom">保存并加入</el-button>
      </template>
    </el-dialog>

    <!-- 执行 -->
    <el-dialog v-model="runVisible" title="执行测评任务" width="480px">
      <el-form label-width="90px">
        <el-form-item label="执行机" required>
          <el-select v-model="runForm.runner" style="width:100%" :placeholder="devices.length ? '选择执行机' : '未登记设备,去「我的设备」注册'" @change="loadClientDevices">
            <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标设备">
          <el-select v-model="runForm.target_device" clearable style="width:100%" :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'">
            <el-option v-for="dev in clientDevices" :key="dev.vm_id" :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`" :value="dev.vm_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="对话模式">
          <el-select v-model="runForm.chat_mode" clearable style="width:100%" placeholder="留空=客户端默认">
            <el-option v-for="m in CHAT_MODES" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="runForm.model" clearable :placeholder="MODEL_PLACEHOLDER" />
        </el-form-item>
        <el-form-item label="思考深度">
          <el-select v-model="runForm.thinking_depth" clearable style="width:100%" placeholder="留空=客户端默认">
            <el-option v-for="d in THINKING_DEPTHS" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon
          :title="`将下发 ${runTask?.query_ids?.length || 0} 条用例到执行机;重复执行会生成新批次,综合评价需重新生成`" />
      </el-form>
      <template #footer>
        <el-button @click="runVisible = false">取消</el-button>
        <el-button type="success" :loading="running" :disabled="!runForm.runner" @click="doRun">下发执行</el-button>
      </template>
    </el-dialog>

    <!-- 详情/结果 -->
    <el-drawer v-model="detailVisible" :title="detail?.task?.name || '任务详情'" size="72%" destroy-on-close>
      <div v-if="detail" class="detail">
        <div class="d-meta">
          <el-tag :type="TS_TYPE[detail.task.status] || 'info'" effect="plain">{{ TS_LABEL[detail.task.status] || detail.task.status }}</el-tag>
          <span v-if="detail.task.last_batch_id" class="mono">批次 {{ detail.task.last_batch_id }}</span>
          <span v-if="fmtDialogOptions(detail.task.dialog_options)" class="opts">{{ fmtDialogOptions(detail.task.dialog_options) }}</span>
          <span class="muted">{{ detail.task.description || '' }}</span>
          <div class="d-actions">
            <el-button size="small" :icon="Refresh" @click="refreshDetail">刷新</el-button>
            <el-button size="small" type="primary" :loading="batchJudging" :disabled="!judgeableRuns.length" @click="judgeAll">
              批量判定（{{ judgeableRuns.length }}）
            </el-button>
            <el-button size="small" type="warning" :loading="summarizing" :disabled="!canSummarize" @click="genSummary">
              {{ detail.task.summary_status === 'done' ? '重新生成综合评价' : '生成综合评价' }}
            </el-button>
          </div>
        </div>

        <el-table :data="groupedDetailRuns" size="small" border stripe class="d-table"
          row-key="run_id" :tree-props="{ children: 'children' }">
          <el-table-column label="#" width="80" align="center">
            <template #default="{ row }">
              <span v-if="row.isGroup" class="muted">会话组</span>
              <span v-else>{{ row.run_id }}</span>
            </template>
          </el-table-column>
          <el-table-column label="用例" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.isGroup" size="small" type="warning" effect="plain" class="turn-tag">多轮 ×{{ row.children.length }}</el-tag>
              <el-tag v-else-if="row._inGroup" size="small" effect="plain" class="turn-tag">第{{ (row.payload?.turn_index ?? 0) + 1 }}轮</el-tag>
              {{ row.payload?.title || row.payload?.prompt || `query#${row.eval_query_id}` }}
            </template>
          </el-table-column>
          <el-table-column label="维度" width="104" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.dimension" size="small" effect="plain" :type="DIM_TAG_TYPE[row.dimension] || 'info'">{{ dimLabel(row.dimension) }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="执行" width="88" align="center">
            <template #default="{ row }">
              <el-tag :type="STATUS_TYPE[row.status] || 'info'" size="small" effect="plain">{{ STATUS_LABEL[row.status] || row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="判定" width="88" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.verdict" :type="VERDICT_TYPE[row.verdict] || 'info'" size="small">{{ VERDICT_LABEL[row.verdict] || row.verdict }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="判定理由" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">{{ row.verdict_reason || '—' }}</template>
          </el-table-column>
          <el-table-column label="会话" width="70" align="center">
            <template #default="{ row }">
              <el-link v-if="safeUrl(row.share_link)" type="primary" :href="safeUrl(row.share_link)" target="_blank" rel="noopener noreferrer">打开</el-link>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="96" align="center">
            <template #default="{ row }">
              <el-popconfirm v-if="!row.isGroup && (row.status === 'running' || row.status === 'pending')"
                title="标记为执行失败？(会话未回填/执行中断时用于收口)" width="240" @confirm="markFailed(row)">
                <template #reference><el-button size="small" type="danger" text>标记失败</el-button></template>
              </el-popconfirm>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        <!-- 综合评价 -->
        <div class="summary-sec">
          <div class="summary-head">
            <span class="summary-title">AI 综合评价</span>
            <span v-if="detail.task.summary_at" class="muted">{{ (detail.task.summary_at || '').replace('T',' ').slice(0,19) }} · {{ detail.task.summary_provider }}</span>
          </div>
          <pre v-if="summarizing && summaryStream" class="summary-stream">{{ summaryStream }}</pre>
          <div v-else-if="detail.task.summary_html" class="summary-html" v-html="detail.task.summary_html"></div>
          <el-empty v-else description="尚未生成综合评价。执行 + 判定完成后点上方「生成综合评价」" :image-size="60" />
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Tickets, Plus, Refresh } from '@element-plus/icons-vue'
import {
  listEvalTasks, createEvalTask, updateEvalTask, deleteEvalTask, runEvalTask, listEvalTaskRuns,
  streamEvalTaskSummary, listEvalQueries, createEvalQueryManual, listMyDevices, listEvalDevices,
  listEvalDimensions, judgeEvalBatch, markEvalRunFailed,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { CHAT_MODES, THINKING_DEPTHS, MODEL_PLACEHOLDER, buildDialogOptions, fmtDialogOptions } from '@/utils/dialogOptions'
import { groupEvalRuns } from '@/utils/evalRunGroups'

const TS_LABEL = { draft: '草稿', running: '执行中', done: '已完成', archived: '已归档' }
const TS_TYPE = { draft: 'info', running: 'warning', done: 'success', archived: 'info' }
const STATUS_LABEL = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败' }
const STATUS_TYPE = { pending: 'info', running: 'warning', done: 'primary', judging: 'warning', judged: 'success', failed: 'danger' }
const VERDICT_LABEL = { pass: '通过', fail: '不通过', error: '判定出错' }
const VERDICT_TYPE = { pass: 'success', fail: 'danger', error: 'info' }
const DIM_TAG_TYPE = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success', safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const loading = ref(false)

// 维度注册表
const DIMENSIONS = ref([])
const dimLabel = (k) => (DIMENSIONS.value.find((d) => d.k === k)?.label) || k || '未标注'

// 编辑
const editVisible = ref(false)
const editing = ref(null)
const editForm = ref({ name: '', description: '', query_ids: [] })
const allQueries = ref([])
const qTable = ref(null)
const saving = ref(false)

// 自定义用例
const customVisible = ref(false)
const customForm = ref({ title: '', prompt: '', dimension: null, expected: '' })
const customSaving = ref(false)

// 执行
const runVisible = ref(false)
const runTask = ref(null)
const runForm = ref({ runner: '', target_device: '', chat_mode: '', model: '', thinking_depth: '' })
const devices = ref([])
const clientDevices = ref([])
const running = ref(false)

// 详情
const detailVisible = ref(false)
const detail = ref(null)
const batchJudging = ref(false)
const summarizing = ref(false)
const summaryStream = ref('')

const safeUrl = (u) => /^https?:\/\//i.test(u || '') ? u : null
// 详情表:多轮会话聚合成组行树形展开(公共逻辑见 utils/evalRunGroups),单轮原样
const groupedDetailRuns = computed(() => groupEvalRuns(detail.value?.runs || []))
const judgeableRuns = computed(() =>
  (detail.value?.runs || []).filter((r) => r.status === 'done' || r.status === 'judged'))
const canSummarize = computed(() => {
  const t = detail.value?.task
  if (!t || !t.last_batch_id || summarizing.value) return false
  const runs = detail.value?.runs || []
  // 至少有一条非 failed 的 run 才开放综合评价
  return runs.some((r) => r.status !== 'failed' && r.status !== 'pending' && r.status !== 'running')
})

onMounted(async () => {
  const [projRes, devRes, dimRes] = await Promise.allSettled([app.fetchProjects(), listMyDevices(), listEvalDimensions()])
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
  DIMENSIONS.value = dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length
    ? dimRes.value.dimensions.map((d) => ({ k: d.key, label: d.label }))
    : [{ k: 'thinking', label: '思考推理' }, { k: 'workflow', label: '工作流' }, { k: 'clarification', label: '反问澄清' }]
  if (devices.value.length) runForm.value.runner = devices.value[0].runner_id
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
})

async function onProjectChange() {
  tasks.value = []
  if (!pid.value) return
  setLastProjectId(pid.value)
  await load()
}

async function load() {
  loading.value = true
  try { tasks.value = await listEvalTasks(pid.value) || [] } finally { loading.value = false }
}

// ── 编辑 ──
async function openEdit(row) {
  editing.value = row
  editForm.value = row
    ? { name: row.name, description: row.description || '', query_ids: [...row.query_ids] }
    : { name: '', description: '', query_ids: [] }
  try { allQueries.value = await listEvalQueries(pid.value) || [] } catch { allQueries.value = [] }
  editVisible.value = true
  await nextTick()
  // 回显勾选
  const sel = new Set(editForm.value.query_ids)
  allQueries.value.forEach((q) => { if (sel.has(q.id)) qTable.value?.toggleRowSelection(q, true) })
}

async function saveTask() {
  saving.value = true
  try {
    if (editing.value?.id) {
      await updateEvalTask(editing.value.id, editForm.value)
      ElMessage.success('已保存')
    } else {
      await createEvalTask({ project_id: pid.value, ...editForm.value })
      ElMessage.success('已创建')
    }
    editVisible.value = false
    await load()
  } catch { /* 拦截器已提示 */ }
  finally { saving.value = false }
}

async function saveCustom() {
  customSaving.value = true
  try {
    const q = await createEvalQueryManual({ project_id: pid.value, ...customForm.value })
    allQueries.value = [q, ...allQueries.value]
    editForm.value.query_ids.push(q.id)
    await nextTick()
    qTable.value?.toggleRowSelection(allQueries.value[0], true)
    customVisible.value = false
    customForm.value = { title: '', prompt: '', dimension: null, expected: '' }
    ElMessage.success('用例已创建并加入任务')
  } catch { /* 拦截器已提示 */ }
  finally { customSaving.value = false }
}

async function removeTask(row) {
  try { await deleteEvalTask(row.id); ElMessage.success('已删除'); await load() } catch { /* 拦截器已提示 */ }
}

// ── 执行 ──
async function openRun(row) {
  if (!row.query_ids.length) { ElMessage.warning('任务内还没有用例，先编辑添加'); return }
  runTask.value = row
  // 回填该任务最近一次执行的对话选项(没有则清空=默认)
  const d = row.dialog_options || {}
  runForm.value.chat_mode = d.chatMode || ''
  runForm.value.model = d.model || ''
  runForm.value.thinking_depth = d.thinkingDepth || ''
  runVisible.value = true
  if (runForm.value.runner) await loadClientDevices()
}

async function loadClientDevices() {
  runForm.value.target_device = ''
  clientDevices.value = []
  if (!runForm.value.runner) return
  try { clientDevices.value = await listEvalDevices(runForm.value.runner) || [] } catch { clientDevices.value = [] }
}

async function doRun() {
  running.value = true
  try {
    const res = await runEvalTask(runTask.value.id, {
      runner: runForm.value.runner,
      target_engine: 'namiwork',
      target_device: runForm.value.target_device || null,
      dialog_options: buildDialogOptions({
        chatMode: runForm.value.chat_mode, model: runForm.value.model, thinkingDepth: runForm.value.thinking_depth,
      }),
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条（批次 ${res.batch_id}）`)
    runVisible.value = false
    await load()
  } catch { /* 拦截器已提示 */ }
  finally { running.value = false }
}

// ── 详情/判定/综合评价 ──
async function openDetail(row) {
  detailVisible.value = true
  detail.value = null
  summaryStream.value = ''
  try { detail.value = await listEvalTaskRuns(row.id) } catch { /* 拦截器已提示 */ }
}

async function refreshDetail() {
  if (!detail.value?.task) return
  try { detail.value = await listEvalTaskRuns(detail.value.task.id) } catch { /* 拦截器已提示 */ }
}

async function judgeAll() {  const runs = judgeableRuns.value
  if (!runs.length) return
  batchJudging.value = true
  try {
    // 只传 done 状态的 run_ids（judged 的也允许重判）；用任务的 project_id 而非全局 pid
    const taskProjectId = detail.value?.task?.project_id || pid.value
    if (!taskProjectId) { ElMessage.warning('缺少项目信息，请刷新页面后重试'); return }
    // 双保险过滤非法 id：任何 null/undefined 混入都会触发后端 422「参数校验失败」
    const runIds = runs.filter((r) => r.status === 'done').map((r) => r.run_id).filter((id) => Number.isInteger(id))
    if (!runIds.length) { ElMessage.warning('没有待判定的用例（done 状态）'); return }
    const res = await judgeEvalBatch({ project_id: taskProjectId, run_ids: runIds })
    // 逐条结果里可能有 error(单条异常)/skipped(未执行完)/verdict=error(未回填、引擎不可用等):
    // 区分提示,别把带失败的批次一律报成功
    const bad = (res.results || []).filter((x) => x.error || x.skipped || x.verdict === 'error').length
    if (bad) ElMessage.warning(`已处理 ${res.judged} 条，其中 ${bad} 条判定失败/跳过（未回填的会话请重跑后再判）`)
    else ElMessage.success(`已判定 ${res.judged} 条`)
    await refreshDetail()
  } catch { /* 拦截器已提示 */ }
  finally { batchJudging.value = false }
}

async function markFailed(row) {
  try {
    await markEvalRunFailed(detail.value.task.id, row.run_id)
    ElMessage.success(`run ${row.run_id} 已标记失败`)
    await refreshDetail()
  } catch { /* 拦截器已提示 */ }
}

function genSummary() {
  if (!detail.value?.task) return
  summarizing.value = true
  summaryStream.value = ''
  streamEvalTaskSummary(detail.value.task.id, {}, {
    onDelta: (t) => { summaryStream.value += t },
    onDone: async (evt) => {
      summarizing.value = false
      if (evt.status === 'done') { ElMessage.success('综合评价已生成'); await refreshDetail(); await load() }
      else ElMessage.error(evt.msg || '生成失败')
    },
    onError: (msg) => { summarizing.value = false; ElMessage.error(msg || '生成失败') },
  })
}
</script>

<style scoped>
.eval-tasks { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head-right { display: flex; gap: 10px; align-items: center; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: #00b386; }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; }
.muted { color: #c0c4cc; font-size: 12px; }
.tname { color: #00926e; cursor: pointer; }
.tname:hover { text-decoration: underline; }
.batch { line-height: 1.5; color: #5a6b7b; }
.opts { color: #5a6b7b; font-size: 12px; }
.turn-tag { margin-right: 6px; }
/* 用例选择 */
.qpick { width: 100%; }
.qpick-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: 13px; color: #5a6b7b; }
/* 详情 */
.detail { display: flex; flex-direction: column; gap: 14px; padding: 0 4px; }
.d-meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.d-actions { margin-left: auto; display: flex; gap: 8px; }
.summary-sec { border: 1px solid #e4e7ed; border-radius: 8px; padding: 14px 18px; background: #fbfdfe; }
.summary-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.summary-title { font-weight: 700; color: #1f2d3d; }
.summary-stream {
  max-height: 320px; overflow: auto; background: #0f1c2e; color: #7fe7c4;
  border-radius: 6px; padding: 12px; font-size: 12px; white-space: pre-wrap; word-break: break-all;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
}
/* AI 产出的 HTML 评价:限定样式作用域,基础排版 */
.summary-html { line-height: 1.7; color: #34495e; font-size: 13px; }
.summary-html :deep(h2) { font-size: 16px; margin: 14px 0 8px; color: #1f2d3d; border-left: 3px solid #00b386; padding-left: 8px; }
.summary-html :deep(h3) { font-size: 14px; margin: 10px 0 6px; color: #34495e; }
.summary-html :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0; }
.summary-html :deep(th), .summary-html :deep(td) { border: 1px solid #dfe6ec; padding: 6px 10px; text-align: left; font-size: 12px; }
.summary-html :deep(th) { background: #f3f8f7; color: #1f2d3d; }
.summary-html :deep(ul), .summary-html :deep(ol) { padding-left: 22px; margin: 6px 0; }
.summary-html :deep(blockquote) { border-left: 3px solid #dfe6ec; margin: 8px 0; padding: 4px 12px; color: #7d8a9b; background: #f8fafc; }
.summary-html :deep(code) { background: #eef2f6; border-radius: 3px; padding: 1px 5px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.d-table { width: 100%; }
</style>
