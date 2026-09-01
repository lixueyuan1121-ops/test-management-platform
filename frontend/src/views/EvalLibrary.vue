<template>
  <div class="eval-library">
    <el-card>
      <template #header>
        <div class="head">
          <div class="title-wrap">
            <el-icon class="title-icon"><Collection /></el-icon>
            <div>
              <div class="title">对话测评用例库</div>
              <div class="subtitle">历史生成的对话测评 query，可勾选再次下发到执行机验证</div>
            </div>
          </div>
          <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="onProjectChange">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </div>
      </template>

      <div class="filter-bar">
        <el-select v-model="filterDim" clearable placeholder="按维度筛选(全部)" size="small" style="width:180px" @change="reload">
          <el-option v-for="d in DIMENSIONS" :key="d.k" :label="d.label" :value="d.k" />
        </el-select>
        <el-select v-model="filterTaskId" clearable placeholder="按测评任务筛选(全部)" size="small" style="width:220px" @change="reload">
          <el-option v-for="t in tasks" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
        <span class="count-info" v-if="!loading">共 {{ queries.length }} 条</span>
        <div class="spacer" />
        <el-button type="primary" size="small" :icon="Upload" :disabled="!pid" @click="openImport">导入用例</el-button>
      </div>

      <div class="dispatch-bar" v-if="selected.length">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="chosenRunner" size="small" style="width:180px" placeholder="选择执行机" @change="loadClientDevices">
          <el-option v-for="d in devices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-select v-model="chosenDevice" size="small" style="width:200px" clearable
          :placeholder="clientDevices.length ? '选目标设备(可空)' : '该执行机未上报设备'">
          <el-option v-for="dev in clientDevices" :key="dev.vm_id"
            :label="`${dev.name || dev.vm_id}${(dev.status==='online'||dev.status==='active')?' 🟢':' ⚪'}`" :value="dev.vm_id" />
        </el-select>
        <el-select v-model="chosenChatMode" size="small" style="width:200px" clearable placeholder="对话模式(默认)">
          <el-option v-for="m in CHAT_MODES" :key="m.value" :label="m.label" :value="m.value" />
        </el-select>
        <el-input v-model="chosenModel" size="small" style="width:220px" clearable :placeholder="MODEL_PLACEHOLDER" />
        <el-select v-model="chosenDepth" size="small" style="width:130px" clearable placeholder="思考深度(默认)">
          <el-option v-for="d in THINKING_DEPTHS" :key="d" :label="d" :value="d" />
        </el-select>
        <el-button type="success" size="small" :loading="dispatching" :disabled="!chosenRunner" @click="dispatch">
          下发选中到执行机
        </el-button>
      </div>

      <el-table v-if="loading || queries.length" :data="sorted" size="small" border stripe @selection-change="s => selected = s" v-loading="loading">
        <el-table-column type="selection" width="42" />
        <el-table-column label="维度" width="120" align="center">
          <template #default="{ row }"><el-tag :type="DIM_TYPE[row.dimension] || 'info'" effect="plain" size="small">{{ dimLabel(row.dimension) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="标题" min-width="180"><template #default="{ row }">{{ row.title }}</template></el-table-column>
        <el-table-column label="提问 prompt" min-width="240"><template #default="{ row }"><span class="multiline">{{ row.prompt || '—' }}</span></template></el-table-column>
        <el-table-column label="预期 expected" min-width="200"><template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template></el-table-column>
        <el-table-column label="对话组" min-width="110"><template #default="{ row }"><span class="mono">{{ row.conversation_group || '—' }}</span></template></el-table-column>
        <el-table-column label="轮次" width="64" align="center"><template #default="{ row }"><span class="mono">{{ row.turn_index ?? 0 }}</span></template></el-table-column>
        <el-table-column label="生成时间" width="160"><template #default="{ row }"><span class="mono">{{ (row.created_at || '').replace('T',' ').slice(0,19) }}</span></template></el-table-column>
        <el-table-column label="评审态" width="90" align="center">
          <template #default="{ row }"><el-tag size="small" :type="row.review_status==='adopted'?'success':(row.review_status==='rejected'?'danger':'info')" effect="plain">{{ RS_LABEL[row.review_status] || row.review_status || '待评审' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="72" align="center">
          <template #default="{ row }">
            <el-tooltip :content="hasPlaceholder(row) ? '按 {{占位符}} 批量生成变体题' : '题目里写 {{变量}} 后可批量生成变体'" placement="left">
              <el-button size="small" type="primary" text :disabled="!hasPlaceholder(row)" @click="openExpand(row)">变体</el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !queries.length" description="该项目暂无生成的对话测评 query，去『对话测评生成』生成" />
    </el-card>

    <!-- 占位符变体展开:{{变量}} × 取值列表笛卡尔积批量生成 -->
    <el-dialog v-model="expandVisible" title="批量生成变体题" width="560px">
      <div class="exp-base">模板：<b>{{ expandBase?.title }}</b></div>
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
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Collection, Upload } from '@element-plus/icons-vue'
import { listEvalQueries, listMyDevices, listEvalDevices, enqueueEvalQueries, listEvalDimensions, expandEvalQuery, importEvalQueries, listEvalTasks } from '@/api'
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
const queries = ref([])
const loading = ref(false)
const selected = ref([])
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
const detectVars = (row) => [...new Set(
  `${row.title || ''}\n${row.prompt || ''}\n${row.expected || ''}`.match(VAR_RE)?.map((m) => m.replace(VAR_RE, '$1')) || []
)]
const hasPlaceholder = (row) => detectVars(row).length > 0

const expandVisible = ref(false)
const expandBase = ref(null)
const expandVars = ref([])
const expandValues = ref({})
const expanding = ref(false)

const parseVals = (s) => [...new Set(String(s || '').split(/[,，\n]/).map((v) => v.trim()).filter(Boolean))]
const expandCount = computed(() =>
  expandVars.value.reduce((n, name) => n * parseVals(expandValues.value[name]).length, expandVars.value.length ? 1 : 0))

function openExpand(row) {
  expandBase.value = row
  expandVars.value = detectVars(row)
  expandValues.value = Object.fromEntries(expandVars.value.map((n) => [n, '']))
  expandVisible.value = true
}

async function doExpand() {
  expanding.value = true
  try {
    const variables = Object.fromEntries(expandVars.value.map((n) => [n, parseVals(expandValues.value[n])]))
    const res = await expandEvalQuery({ base_query_id: expandBase.value.id, variables })
    ElMessage.success(`已生成 ${res.count} 道变体题`)
    expandVisible.value = false
    await reload()
  } catch { /* 拦截器已提示 */ }
  finally { expanding.value = false }
}
</script>

<style scoped>
.eval-library { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: #00b386; }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.filter-bar .spacer { flex: 1; }
.count-info { color: #8a94a6; font-size: 12px; }
.tpl-alert { margin-bottom: 8px; }
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
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #5a6b7b; }
.exp-base { margin-bottom: 12px; color: #5a6b7b; font-size: 13px; }
</style>
