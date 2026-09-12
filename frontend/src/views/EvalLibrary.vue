<template>
  <div class="eval-library">
    <section class="library-workspace">
        <div class="head">
          <div class="title-wrap">
            <el-icon class="title-icon"><Collection /></el-icon>
            <div>
              <div class="title">对话测评用例库</div>
            </div>
          </div>
          <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </div>

      <div class="filter-bar">
        <el-select v-model="filterDim" clearable placeholder="按维度筛选(全部)" size="small" style="width:180px" @change="reload">
          <el-option v-for="d in DIMENSIONS" :key="d.k" :label="d.label" :value="d.k" />
        </el-select>
        <el-select v-model="filterTaskId" clearable placeholder="按测评任务筛选(全部)" size="small" style="width:220px" @change="reload">
          <el-option v-for="t in tasks" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
        <span class="count-info" v-if="!loading">共 {{ queries.length }} 条</span>
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <div class="spacer" />
        <el-button v-if="canDelete" type="danger" plain size="small" :icon="Delete"
          :disabled="!selected.length || deletingId !== null || loading" :loading="batchDeleting"
          @click="removeSelectedQueries">批量删除</el-button>
        <el-button type="primary" size="small" :icon="Upload" :disabled="!pid" @click="openImport">导入用例</el-button>
        <el-button type="primary" size="small" :disabled="!selected.length" @click="dispatchVisible = true">配置并下发</el-button>
      </div>

      <el-dialog v-model="dispatchVisible" title="下发测评用例" width="560px" :close-on-click-modal="!dispatching" :show-close="!dispatching">
      <div class="dispatch-fields">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <label>执行机</label>
        <el-select v-model="chosenRunner" aria-label="执行机" placeholder="选择执行机" @change="loadClientDevices">
          <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <label>目标设备</label>
        <el-select v-model="chosenDevice" aria-label="目标设备" clearable
          :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'">
          <el-option v-for="dev in clientDevices" :key="dev.vm_id"
            :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`" :value="dev.vm_id" />
        </el-select>
        <label>对话模式</label>
        <el-select v-model="chosenChatMode" aria-label="对话模式" clearable placeholder="对话模式(默认)">
          <el-option v-for="m in CHAT_MODES" :key="m.value" :label="m.label" :value="m.value" />
        </el-select>
        <label>模型</label>
        <el-input v-model="chosenModel" aria-label="模型" clearable :placeholder="MODEL_PLACEHOLDER" />
        <label>思考深度</label>
        <el-select v-model="chosenDepth" aria-label="思考深度" clearable placeholder="思考深度(默认)">
          <el-option v-for="d in THINKING_DEPTHS" :key="d" :label="d" :value="d" />
        </el-select>
      </div>
      <template #footer>
        <el-button :disabled="dispatching" @click="dispatchVisible = false">取消</el-button>
        <el-button type="primary" :loading="dispatching" :disabled="!chosenRunner || !selected.length" @click="dispatch">
          确认下发
        </el-button>
      </template>
      </el-dialog>

      <el-table v-if="loading || queries.length" :data="sorted" size="small" border stripe @selection-change="s => selected = s" v-loading="loading">
        <el-table-column type="selection" width="42" />
        <el-table-column label="维度" width="120" align="center">
          <template #default="{ row }"><el-tag :type="DIM_TYPE[row.dimension] || 'info'" effect="plain" size="small">{{ dimLabel(row.dimension) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="标题" min-width="180"><template #default="{ row }"><button class="case-title" @click="inspectedCase = row; caseVisible = true">{{ row.title }}</button></template></el-table-column>
        <el-table-column label="提问 prompt" min-width="240"><template #default="{ row }"><span class="multiline">{{ row.prompt || '—' }}</span></template></el-table-column>
        <el-table-column label="预期 expected" min-width="200"><template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template></el-table-column>
        <el-table-column label="对话组" min-width="110"><template #default="{ row }"><span class="mono">{{ row.conversation_group || '—' }}</span></template></el-table-column>
        <el-table-column label="轮次" width="64" align="center"><template #default="{ row }"><span class="mono">{{ row.turn_index ?? 0 }}</span></template></el-table-column>
        <el-table-column label="生成时间" width="160"><template #default="{ row }"><span class="mono">{{ (row.created_at || '').replace('T',' ').slice(0,19) }}</span></template></el-table-column>
        <el-table-column label="评审态" width="90" align="center">
          <template #default="{ row }"><el-tag size="small" :type="row.review_status==='adopted'?'success':(row.review_status==='rejected'?'danger':'info')" effect="plain">{{ RS_LABEL[row.review_status] || row.review_status || '待评审' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="118" align="center">
          <template #default="{ row }">
            <el-tooltip :content="hasPlaceholder(row) ? '按 {{占位符}} 批量生成变体题' : 'AI 帮你把这题挖成 {{变量}} 模板，再批量生成变体'" placement="left">
              <el-button size="small" type="primary" text :loading="paramLoadingId === row.id"
                @click="hasPlaceholder(row) ? openExpand(row) : openParameterize(row)">变体</el-button>
            </el-tooltip>
            <el-tooltip v-if="canDelete" content="删除用例" placement="top">
              <el-button text type="danger" size="small" :icon="Delete" :aria-label="`删除用例 ${row.title}`"
                :loading="deletingId === row.id" :disabled="deletingId !== null || batchDeleting" @click="removeQuery(row)" />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !queries.length" description="该项目暂无生成的对话测评 query，去『对话测评生成』生成" />
    </section>
    <el-drawer v-model="caseVisible" :title="inspectedCase?.title || '用例详情'" size="min(720px, 100vw)">
      <template v-if="inspectedCase">
        <el-tag effect="plain">{{ dimLabel(inspectedCase.dimension) }}</el-tag>
        <section class="case-section"><h3>提问 Prompt</h3><div>{{ inspectedCase.prompt || '未填写' }}</div></section>
        <section class="case-section"><h3>预期 Expected</h3><div>{{ inspectedCase.expected || '未填写' }}</div></section>
        <section class="case-section"><h3>产物检查</h3>
          <EvalArtifactRules v-model="artifactRules" :disabled="!canDelete" />
          <el-button v-if="canDelete" type="primary" size="small" :loading="savingRules" @click="saveArtifactRules">保存产物检查</el-button>
          <p class="hint">仅用于后续下发的测评，已有运行保留原检查规则。</p>
        </section>
        <section class="case-section"><h3>对话信息</h3><div>{{ inspectedCase.conversation_group || '单轮对话' }} · 第 {{ (inspectedCase.turn_index ?? 0) + 1 }} 轮</div></section>
      </template>
    </el-drawer>

    <!-- 占位符变体展开:{{变量}} × 取值列表笛卡尔积批量生成 -->
    <el-dialog v-model="expandVisible" title="批量生成变体题" width="560px">
      <div class="exp-base">模板：<b>{{ expandBase?.title }}</b></div>
      <!-- AI 参数化模式:展示可编辑的模板文本(base 具体题不动);增删 {{变量}} 会实时刷新下方取值输入 -->
      <template v-if="templateMode">
        <el-alert type="success" :closable="false" show-icon class="tpl-alert"
          title="AI 已挖好占位符 + 建议取值，可直接改模板文本或取值，确认无误再生成（base 原题不会被改动）" />
        <el-form label-position="top">
          <el-form-item label="标题模板"><el-input v-model="expandTemplate.title" /></el-form-item>
          <el-form-item label="提问模板（{{变量}} 处会被取值替换）"><el-input v-model="expandTemplate.prompt" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="期望模板（可空）"><el-input v-model="expandTemplate.expected" type="textarea" :rows="2" /></el-form-item>
        </el-form>
        <div v-if="!expandVars.length" class="exp-hint">模板里还没有 {{ VAR_SAMPLE }}，在上面文本里写一个再填取值。</div>
      </template>
      <el-form label-position="top">
        <el-form-item v-for="name in expandVars" :key="name" :label="`{{${name}}} 的取值（逗号或换行分隔）`">
          <el-input v-model="expandValues[name]" type="textarea" :rows="2" :placeholder="`如：北京, 上海, 广州`" />
        </el-form-item>
      </el-form>
      <el-alert :type="expandCount > 50 ? 'error' : 'info'" :closable="false" show-icon
        :title="`将生成 ${expandCount} 道变体题（占位符取值的全组合，上限 50）`" />
      <template #footer>
        <el-button @click="expandVisible = false">取消</el-button>
        <el-button type="primary" :loading="expanding" :disabled="!expandCount || expandCount > 50" @click="doExpand">生成 {{ expandCount }} 道</el-button>
      </template>
    </el-dialog>

    <!-- 模板导入:本地 CSV/TSV(粘贴/文件) 或 飞书文档链接 -->
    <el-dialog v-model="importVisible" title="导入对话测评用例" width="680px" @closed="resetImport">
      <el-alert type="info" :closable="false" show-icon class="tpl-alert">
        <template #title>
          模板为 CSV/TSV，首行表头：<b>标题, 维度, 提问prompt, 预期expected, 对话组, 轮次</b>。
          维度可填 key（如 tool_use）或中文（如 工具·MCP调用），留空亦可；对话组/轮次留空=单轮独立题，多轮同组名、轮次从 0 递增。
        </template>
      </el-alert>
      <div class="tpl-actions">
        <el-button size="small" text type="primary" @click="copyExample">复制示例</el-button>
        <el-button size="small" text type="primary" @click="downloadTemplate">下载模板.csv</el-button>
      </div>

      <el-radio-group v-model="importMode" size="small" class="mode-radio">
        <el-radio-button value="local">本地（粘贴 / 文件）</el-radio-button>
        <el-radio-button value="feishu">飞书文档</el-radio-button>
      </el-radio-group>

      <template v-if="importMode === 'local'">
        <el-input v-model="importText" type="textarea" :rows="8" :placeholder="TEMPLATE_EXAMPLE" />
        <div class="upload-row">
          <el-upload :auto-upload="false" :show-file-list="false" accept=".csv,.tsv,.md,.txt" :on-change="onFilePick">
            <el-button size="small" :icon="Upload">从文件读取</el-button>
          </el-upload>
          <span class="hint">支持 .csv / .tsv / .md / .txt，请存为 UTF-8；内容将填入上方文本框</span>
        </div>
      </template>
      <template v-else>
        <el-input v-model="importUrl" placeholder="粘贴飞书电子表格/文档链接（需已共享给应用）">
          <template #prepend>飞书链接</template>
        </el-input>
        <div class="hint" style="margin-top:6px">读取飞书『电子表格』最稳：一张表，首行表头，列与模板一致。</div>
      </template>

      <div class="attach-row">
        <span class="lbl">同时加入测评任务（可选）</span>
        <el-select v-model="importTaskId" clearable placeholder="不加入" size="small" style="width:240px">
          <el-option v-for="t in tasks" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
      </div>

      <div v-if="importPreview" class="preview-box">
        <el-alert :type="importPreview.count ? 'success' : 'warning'" :closable="false" show-icon
          :title="`解析成功：将导入 ${importPreview.count} 条${importPreview.skipped.length ? `，跳过 ${importPreview.skipped.length} 行` : ''}`" />
        <ul v-if="importPreview.skipped.length" class="skip-list">
          <li v-for="(s, i) in importPreview.skipped" :key="i">第 {{ s.line }} 行：{{ s.reason }}</li>
        </ul>
        <el-table v-if="importPreview.preview && importPreview.preview.length"
          :data="importPreview.preview.slice(0, 20)" size="small" border max-height="240" style="margin-top:8px">
          <el-table-column label="标题" prop="title" min-width="140" show-overflow-tooltip />
          <el-table-column label="维度" width="120"><template #default="{ row }">{{ dimLabel(row.dimension) }}</template></el-table-column>
          <el-table-column label="提问 prompt" prop="prompt" min-width="220" show-overflow-tooltip />
        </el-table>
        <div v-if="importPreview.preview && importPreview.preview.length > 20" class="hint">仅预览前 20 条…</div>
      </div>

      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button :loading="importing" :disabled="!canImport" @click="doImport(true)">预览解析</el-button>
        <el-button type="primary" :loading="importing" :disabled="!canImport" @click="doImport(false)">
          确认导入{{ importPreview && importPreview.count ? ` ${importPreview.count} 条` : '' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import EvalArtifactRules from '@/components/EvalArtifactRules.vue'
import { updateEvalQuery } from '@/api'
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Upload, Delete } from '@element-plus/icons-vue'
import { listEvalQueries, listMyDevices, listEvalDevices, enqueueEvalQueries, listEvalDimensions, expandEvalQuery, parameterizeEvalQuery, importEvalQueries, listEvalTasks, deleteEvalQuery, batchDeleteEvalQueries } from '@/api'
import { useAuthStore } from '@/store/auth'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { CHAT_MODES, THINKING_DEPTHS, MODEL_PLACEHOLDER, buildDialogOptions } from '@/utils/dialogOptions'

// 维度:服务端注册表为准(onMounted 拉取),失败用内置兜底
const DIMENSIONS = ref([
  { k: 'thinking', label: '思考推理' }, { k: 'tool_use', label: '工具·MCP调用' },
  { k: 'artifact', label: '产物生成' }, { k: 'multi_turn', label: '多轮追问' }, { k: 'instruction', label: '指令遵循' },
  { k: 'workflow', label: '工作流' }, { k: 'clarification', label: '反问澄清' }, { k: 'context', label: '上下文记忆' },
  { k: 'safety', label: '安全合规' }, { k: 'refusal', label: '拒答质量' },
  { k: 'hallucination', label: '事实可靠' }, { k: 'creativity', label: '创意生成' }, { k: 'consistency', label: '一致性' },
])
const DIM_LABEL = computed(() => Object.fromEntries(DIMENSIONS.value.map(d => [d.k, d.label])))
const dimLabel = (k) => DIM_LABEL.value[k] || k || '—'
const DIM_TYPE = {
  thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info',
  workflow: 'warning', clarification: 'primary', context: 'success', safety: 'danger', refusal: 'info',
  hallucination: 'warning', creativity: 'primary', consistency: 'success',
}
const RS_LABEL = { pending: '待评审', adopted: '已采纳', rejected: '已拒绝' }

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const auth = useAuthStore()
const canDelete = computed(() => ['admin', 'member'].includes(auth.roleIn(pid.value)))
const deletingId = ref(null)
const batchDeleting = ref(false)
async function removeSelectedQueries() {
  if (!canDelete.value || !selected.value.length || batchDeleting.value || deletingId.value !== null) return
  const projectId = pid.value
  const ids = selected.value.map(q => q.id)
  batchDeleting.value = true
  try {
    await ElMessageBox.confirm(`确定删除勾选的 ${ids.length} 条用例？将同步从关联任务中移除，历史执行结果保留。多轮对话仅删除勾选的轮次，删除后不可恢复。任一用例仍在排队、执行或判定中，整批不删除。`, '批量删除测评用例', {
      type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消',
    })
    const result = await batchDeleteEvalQueries({ project_id: projectId, query_ids: ids })
    const removed = new Set(result.deleted)
    queries.value = queries.value.filter(q => !removed.has(q.id))
    selected.value = selected.value.filter(q => !removed.has(q.id))
    if (removed.has(inspectedCase.value?.id)) caseVisible.value = false
    ElMessage.success(`已删除 ${result.count} 条用例`)
  } catch { /* 取消或失败保留用例与选择，接口错误由拦截器提示。 */ }
  finally { batchDeleting.value = false }
}
async function removeQuery(row) {
  if (!canDelete.value || deletingId.value !== null || batchDeleting.value) return
  deletingId.value = row.id
  try {
    await ElMessageBox.confirm(`确定删除“${row.title}”？将从关联测评任务中移除，历史执行结果保留。多轮对话仅删除当前这一条，删除后不可恢复。`, '删除测评用例', {
      type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消',
    })
    await deleteEvalQuery(row.id)
    queries.value = queries.value.filter(q => q.id !== row.id)
    selected.value = selected.value.filter(q => q.id !== row.id)
    if (inspectedCase.value?.id === row.id) caseVisible.value = false
    ElMessage.success('用例已删除')
  } catch { /* 取消不执行；接口失败由拦截器提示，保留列表。 */ }
  finally { deletingId.value = null }
}
const queries = ref([])
const loading = ref(false)
const selected = ref([])
const dispatchVisible = ref(false)
const caseVisible = ref(false)
const inspectedCase = ref(null)
const artifactRules = ref([])
const savingRules = ref(false)
watch(inspectedCase, row => { artifactRules.value = JSON.parse(JSON.stringify(row?.verification_rules || [])) })
async function saveArtifactRules() {
  if (!inspectedCase.value || !canDelete.value) return
  const row = inspectedCase.value
  savingRules.value = true
  try {
    const updated = await updateEvalQuery(row.id, { verification_rules: artifactRules.value })
    Object.assign(row, updated)
    ElMessage.success('产物检查已保存，后续执行生效')
  } catch { /* API interceptor displays the validation error. */ }
  finally { savingRules.value = false }
}
const devices = ref([])
const chosenRunner = ref('')
const clientDevices = ref([])
const chosenDevice = ref('')
const dispatching = ref(false)
// 对话选项(三项全空=不指定,客户端保持页面默认)
const chosenChatMode = ref('')
const chosenModel = ref('')
const chosenDepth = ref('')

// 筛选(按维度 / 按测评任务)
const tasks = ref([])
const filterDim = ref('')
const filterTaskId = ref(null)

// 模板导入
const importVisible = ref(false)
const importMode = ref('local')     // local=粘贴/文件 · feishu=飞书文档链接
const importText = ref('')
const importUrl = ref('')
const importTaskId = ref(null)
const importPreview = ref(null)     // dry_run 结果 {count, skipped, preview}
const importing = ref(false)
const canImport = computed(() => importMode.value === 'feishu' ? !!importUrl.value.trim() : !!importText.value.trim())

const TEMPLATE_EXAMPLE = [
  '标题,维度,提问prompt,预期expected,对话组,轮次',
  '查北京天气,tool_use,帮我查北京今天天气,应联网搜索给出温度,,0',
  '推荐电影,multi_turn,推荐一部电影,应给出一部推荐,g1,0',
  '推荐电影,multi_turn,换成喜剧的,基于上轮改推喜剧,g1,1',
].join('\n')

const sorted = computed(() => [...queries.value].sort((a, b) =>
  String(a.conversation_group || '').localeCompare(String(b.conversation_group || '')) || (a.turn_index ?? 0) - (b.turn_index ?? 0)))

onMounted(async () => {
  const [projRes, devRes, dimRes] = await Promise.allSettled([app.fetchProjects(), listMyDevices(), listEvalDimensions()])
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
  if (dimRes.status === 'fulfilled' && dimRes.value?.dimensions?.length) {
    DIMENSIONS.value = dimRes.value.dimensions.map((d) => ({ k: d.key, label: d.label }))
  }
  if (devices.value.length) { chosenRunner.value = devices.value[0].runner_id; await loadClientDevices() }
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await onProjectChange() }
})

async function onProjectChange() {
  selected.value = []
  filterDim.value = ''; filterTaskId.value = null
  if (!pid.value) { queries.value = []; tasks.value = []; return }
  setLastProjectId(pid.value)
  await Promise.allSettled([reload(), loadTasks()])
}

async function loadTasks() {
  if (!pid.value) { tasks.value = []; return }
  try { tasks.value = await listEvalTasks(pid.value) || [] } catch { tasks.value = [] }
}

async function reload() {
  if (!pid.value) { queries.value = []; return }
  selected.value = []
  loading.value = true
  try {
    const filters = {}
    if (filterDim.value) filters.dimension = filterDim.value
    if (filterTaskId.value) filters.eval_task_id = filterTaskId.value
    queries.value = await listEvalQueries(pid.value, filters) || []
  } catch { queries.value = [] }
  finally { loading.value = false }
}

async function loadClientDevices() {
  chosenDevice.value = ''; clientDevices.value = []
  if (!chosenRunner.value) return
  try { clientDevices.value = await listEvalDevices(chosenRunner.value) || [] } catch { clientDevices.value = [] }
}

async function dispatch() {
  if (!selected.value.length || !chosenRunner.value) return
  dispatching.value = true
  try {
    const res = await enqueueEvalQueries({
      project_id: pid.value, runner: chosenRunner.value, target_engine: 'namiwork',
      target_device: chosenDevice.value || null, eval_query_ids: selected.value.map(q => q.id),
      dialog_options: buildDialogOptions({
        chatMode: chosenChatMode.value, model: chosenModel.value, thinkingDepth: chosenDepth.value,
      }),
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条到 ${chosenRunner.value}(批次 ${res.batch_id})`)
    dispatchVisible.value = false
  } catch { /* 拦截器已提示 */ }
  finally { dispatching.value = false }
}

// ── 模板导入(本地 CSV/TSV 粘贴/文件 · 飞书文档链接) ──
function openImport() {
  importPreview.value = null
  importVisible.value = true
}

function resetImport() {
  importMode.value = 'local'
  importText.value = ''
  importUrl.value = ''
  importTaskId.value = null
  importPreview.value = null
}

// 文本/链接/模式变了 → 作废上次预览,避免拿旧预览对新内容"确认导入"
watch([importText, importUrl, importMode], () => { importPreview.value = null })

function onFilePick(uploadFile) {
  const raw = uploadFile?.raw || uploadFile
  if (!raw) return
  const reader = new FileReader()
  reader.onload = () => { importText.value = String(reader.result || ''); importMode.value = 'local' }
  reader.onerror = () => ElMessage.error('文件读取失败')
  reader.readAsText(raw, 'UTF-8')
}

async function copyExample() {
  try { await navigator.clipboard.writeText(TEMPLATE_EXAMPLE); ElMessage.success('示例已复制,粘贴到文本框即可') }
  catch { ElMessage.warning('复制失败,请手动选择示例文本') }
}

function downloadTemplate() {
  const blob = new Blob(['﻿' + TEMPLATE_EXAMPLE], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '对话测评导入模板.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}

async function doImport(dryRun) {
  if (!canImport.value) return
  importing.value = true
  try {
    const payload = { project_id: pid.value, dry_run: dryRun }
    if (importTaskId.value) payload.eval_task_id = importTaskId.value
    if (importMode.value === 'feishu') payload.feishu_url = importUrl.value.trim()
    else payload.text = importText.value
    const res = await importEvalQueries(payload)
    if (dryRun) {
      importPreview.value = res
    } else {
      const parts = [`已导入 ${res.count} 条`]
      if (res.attached) parts.push(`加入任务 ${res.attached} 条`)
      if (res.skipped?.length) parts.push(`跳过 ${res.skipped.length} 行`)
      ElMessage.success(parts.join('，'))
      importVisible.value = false
      await reload()
      if (importTaskId.value) await loadTasks()  // 任务用例数已变,刷新下拉计数
    }
  } catch { /* 拦截器已提示 */ }
  finally { importing.value = false }
}

// ── 占位符变体展开(与后端 _VAR_RE 同款正则:{{变量}},变量名支持中英文/数字/下划线) ──
const VAR_RE = /\{\{\s*([A-Za-z0-9_一-鿿]+)\s*\}\}/g
const VAR_SAMPLE = '{{变量}}'   // 模板里给用户看的字面示例(mustache 内不能直接写 {{}},故走常量)
const detectVarsText = (text) => [...new Set(
  String(text || '').match(VAR_RE)?.map((m) => m.replace(VAR_RE, '$1')) || []
)]
const detectVars = (row) => detectVarsText(`${row.title || ''}\n${row.prompt || ''}\n${row.expected || ''}`)
const hasPlaceholder = (row) => detectVars(row).length > 0

const expandVisible = ref(false)
const expandBase = ref(null)
const expandValues = ref({})
const expanding = ref(false)
// AI 参数化:把具体题挖成模板。templateMode=true 时展开可编辑的 template 文本(base 原题不动),
// 占位符从(可编辑的)模板文本实时重识别;否则读 base 自身原文(老直接展开路径)。
const templateMode = ref(false)
const expandTemplate = ref({ title: '', prompt: '', expected: '' })
const paramLoadingId = ref(null)

const expandVars = computed(() => {
  if (templateMode.value) {
    const t = expandTemplate.value
    return detectVarsText(`${t.title || ''}\n${t.prompt || ''}\n${t.expected || ''}`)
  }
  return expandBase.value ? detectVars(expandBase.value) : []
})

const parseVals = (s) => [...new Set(String(s || '').split(/[,，\n]/).map((v) => v.trim()).filter(Boolean))]
const expandCount = computed(() =>
  expandVars.value.reduce((n, name) => n * parseVals(expandValues.value[name]).length, expandVars.value.length ? 1 : 0))

// base 已含 {{}}:直接展开 base 原文(老路径)
function openExpand(row) {
  expandBase.value = row
  templateMode.value = false
  expandTemplate.value = { title: '', prompt: '', expected: '' }
  expandValues.value = Object.fromEntries(detectVars(row).map((n) => [n, '']))
  expandVisible.value = true
}

// base 无 {{}}:先让 AI 挖成模板 + 建议取值,预填进变体框供人确认微调
async function openParameterize(row) {
  paramLoadingId.value = row.id
  try {
    const res = await parameterizeEvalQuery({ base_query_id: row.id })
    expandBase.value = row
    templateMode.value = true
    expandTemplate.value = { title: res.title || '', prompt: res.prompt || '', expected: res.expected || '' }
    expandValues.value = Object.fromEntries(
      Object.entries(res.variables || {}).map(([k, v]) => [k, (v || []).join(', ')]))
    expandVisible.value = true
  } catch { /* 拦截器已提示(含 AI 未挖出占位符) */ }
  finally { paramLoadingId.value = null }
}

async function doExpand() {
  expanding.value = true
  try {
    const variables = Object.fromEntries(expandVars.value.map((n) => [n, parseVals(expandValues.value[n])]))
    const payload = { base_query_id: expandBase.value.id, variables }
    if (templateMode.value) payload.template = { ...expandTemplate.value }  // AI 模板文本(base 原题不动)
    const res = await expandEvalQuery(payload)
    ElMessage.success(`已生成 ${res.count} 道变体题`)
    expandVisible.value = false
    await reload()
  } catch { /* 拦截器已提示 */ }
  finally { expanding.value = false }
}
</script>

<style scoped>
.library-workspace { min-width: 0; }
.filter-bar { position: sticky; top: -20px; z-index: 20; padding: 12px 0; background: var(--tech-bg); }
.head { flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.dispatch-fields { display: grid; gap: 8px; }
.dispatch-fields label { font-size: 13px; margin-top: 8px; color: var(--el-text-color-regular); }
.case-title { border: 0; background: none; padding: 0; font: inherit; color: var(--el-color-primary); cursor: pointer; text-align: left; }
.case-title:hover { text-decoration: underline; }
.case-section { padding: 20px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.case-section h3 { font-size: 14px; margin: 0 0 12px; }
.case-section div { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.eval-library :deep(.el-dialog) { max-width: calc(100vw - 24px); }
.eval-library :deep(.el-dialog__body) { max-height: 68dvh; overflow: auto; }
.eval-library :deep(.el-dialog__footer) { border-top: 1px solid var(--el-border-color-lighter); padding-top: 16px; }
.eval-library { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: var(--el-color-primary); }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-bar .spacer { flex: 1; }
.count-info { color: #8a94a6; font-size: 12px; }
.tpl-alert { margin-bottom: 8px; }
.exp-hint { color: #8a94a6; font-size: 12px; margin: 4px 0 8px; }
.tpl-alert :deep(.el-alert__title) { line-height: 1.7; font-weight: 400; }
.tpl-actions { margin: 2px 0 10px; }
.mode-radio { margin-bottom: 10px; }
.upload-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.attach-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.attach-row .lbl { color: #5a6b7b; font-size: 13px; }
.preview-box { margin-top: 14px; }
.skip-list { margin: 8px 0 0; padding-left: 18px; color: #b88230; font-size: 12px; line-height: 1.6; }
.hint { color: #8a94a6; font-size: 12px; }
.sel-info { font-weight: 600; color: #00926e; font-size: 13px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #5a6b7b; }
.exp-base { margin-bottom: 12px; color: #5a6b7b; font-size: 13px; }
</style>
