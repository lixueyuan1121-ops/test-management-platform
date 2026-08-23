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
        <el-button type="success" size="small" :loading="dispatching" :disabled="!chosenRunner" @click="dispatch">
          下发选中到执行机
        </el-button>
      </div>

      <el-table :data="sorted" size="small" border stripe @selection-change="s => selected = s" v-loading="loading">
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
      </el-table>
      <el-empty v-if="!loading && !queries.length" description="该项目暂无生成的对话测评 query，去『对话测评生成』生成" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Collection } from '@element-plus/icons-vue'
import { listEvalQueries, listMyDevices, listEvalDevices, enqueueEvalQueries } from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const DIMENSIONS = [
  { k: 'thinking', label: '思考推理' }, { k: 'tool_use', label: '工具·MCP调用' },
  { k: 'artifact', label: '产物生成' }, { k: 'multi_turn', label: '多轮追问' }, { k: 'instruction', label: '指令遵循' },
]
const DIM_LABEL = Object.fromEntries(DIMENSIONS.map(d => [d.k, d.label]))
const dimLabel = (k) => DIM_LABEL[k] || k || '—'
const DIM_TYPE = { thinking: 'primary', tool_use: 'success', artifact: 'warning', multi_turn: 'danger', instruction: 'info' }

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

const sorted = computed(() => [...queries.value].sort((a, b) =>
  String(a.conversation_group || '').localeCompare(String(b.conversation_group || '')) || (a.turn_index ?? 0) - (b.turn_index ?? 0)))

onMounted(async () => {
  const [projRes, devRes] = await Promise.allSettled([app.fetchProjects(), listMyDevices()])
  projects.value = projRes.status === 'fulfilled' ? (projRes.value || []) : []
  devices.value = devRes.status === 'fulfilled' ? (devRes.value || []) : []
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
    })
    ElMessage.success(`已下发 ${res.run_ids.length} 条到 ${chosenRunner.value}(批次 ${res.batch_id})`)
  } catch { /* 拦截器已提示 */ }
  finally { dispatching.value = false }
}
</script>

<style scoped>
.eval-library { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; align-items: center; justify-content: space-between; }
.title-wrap { display: flex; align-items: center; gap: 12px; }
.title-icon { font-size: 24px; color: #00b386; }
.title { font-size: 16px; font-weight: 600; color: #1f2d3d; }
.subtitle { font-size: 12px; color: #8a94a6; margin-top: 2px; }
.dispatch-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.sel-info { font-weight: 600; color: #00926e; font-size: 13px; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; color: #5a6b7b; }
</style>
