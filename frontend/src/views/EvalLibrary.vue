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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Collection } from '@element-plus/icons-vue'
import { listEvalQueries, listMyDevices, listEvalDevices, enqueueEvalQueries, listEvalDimensions, expandEvalQuery } from '@/api'
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
  queries.value = []; selected.value = []
  if (!pid.value) return
  setLastProjectId(pid.value)
  loading.value = true
  try { queries.value = await listEvalQueries(pid.value) || [] } catch { queries.value = [] }
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
    await onProjectChange()
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
.sel-info { font-weight: 600; color: #00926e; font-size: 13px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #5a6b7b; }
.exp-base { margin-bottom: 12px; color: #5a6b7b; font-size: 13px; }
</style>
