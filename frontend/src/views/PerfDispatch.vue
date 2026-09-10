<template>
  <div class="perf-dispatch functional-workspace">
    <WorkspacePage title="性能任务下发">
      <template #actions>
        <el-button size="small" :icon="Refresh" title="刷新采集记录" aria-label="刷新采集记录" :loading="loading" @click="loadRuns" />
        <el-button type="primary" @click="dispatchVisible = true">下发性能任务</el-button>
      </template>
      <template #filters>
    <!-- 报告集 -->
      <div class="set-row">
        <span class="lbl">报告集</span>
        <el-select v-model="currentSet" style="width:240px" @change="loadRuns">
          <el-option label="（全部 / 不归集）" :value="0" />
          <el-option v-for="s in sets" :key="s.id" :label="`${s.name}（${s.run_count}）`" :value="s.id" />
        </el-select>
        <el-button size="small" @click="onNewSet">新建报告集</el-button>
        <el-button size="small" :disabled="!currentSet" @click="onRenameSet">重命名</el-button>
        <el-button size="small" type="danger" plain :disabled="!currentSet" @click="onDeleteSet">删除</el-button>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable size="small" style="width:130px" @change="loadRuns">
          <el-option v-for="s in statuses" :key="s" :label="s" :value="s" />
        </el-select>
      </div>
      </template>

    <!-- 下发表单 -->
    <el-dialog v-model="dispatchVisible" title="下发性能任务" width="560px" :close-on-click-modal="!submitting">
      <el-form :model="form" label-width="80px">
        <el-form-item label="报告集">{{ currentSetName }}</el-form-item>
        <el-form-item label="场景" required>
          <el-select v-model="form.scenario" style="width:140px"><el-option v-for="s in scenarios" :key="s" :label="s" :value="s" /></el-select>
        </el-form-item>
        <el-form-item label="对象标签" required><el-input v-model="form.variant" placeholder="如 2.4.0 / 竞品豆包" style="width:150px" /></el-form-item>
        <el-form-item label="执行机">
          <el-select v-model="form.runner" filterable allow-create default-first-option style="width:160px" placeholder="选/填 runner">
            <el-option v-for="d in devices" :key="d.id" :label="`${d.name}（${d.runner_id}）`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.scenario === '长监控'" label="时长"><el-input v-model="form.duration" placeholder="40s / 30m / 12h" style="width:120px" /></el-form-item>
        <el-form-item label="竞品进程"><el-input v-model="form.proc" placeholder="可选 Doubao.exe" style="width:140px" /></el-form-item>
      </el-form>
      <div v-if="form.scenario !== '长监控'" class="warn">
        提示：「{{ form.scenario }}」需人工操作应用。下发后请到<b>执行机的 agent 窗口</b>，按 perfdog 提示操作（启动应用/发消息/切窗口）并回车，采完自动回传，回「性能报告」刷新查看。
      </div>
      <template #footer><el-button :disabled="submitting" @click="dispatchVisible = false">取消</el-button><el-button type="primary" :loading="submitting" @click="submit">确认下发</el-button></template>
    </el-dialog>

    <!-- 记录表 -->
      <el-table :data="runs" v-loading="loading" size="small">
        <el-table-column prop="id" label="#" width="52" />
        <el-table-column prop="scenario" label="场景" width="88" />
        <el-table-column prop="variant" label="对象" width="100" show-overflow-tooltip />
        <el-table-column prop="runner" label="执行机" width="88" />
        <el-table-column label="来源" width="72">
          <template #default="{ row }"><el-tag size="small" :type="row.source === 'dispatch' ? 'primary' : 'success'">{{ row.source === 'dispatch' ? '下发' : '直传' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="86">
          <template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="总耗时" width="82"><template #default="{ row }">{{ row.duration_ms != null ? Math.round(row.duration_ms) + 'ms' : '—' }}</template></el-table-column>
        <el-table-column label="CPU峰" width="72"><template #default="{ row }">{{ row.summary?.cpu?.peak != null ? row.summary.cpu.peak + '%' : '—' }}</template></el-table-column>
        <el-table-column label="内存增量" width="92"><template #default="{ row }">{{ row.summary?.mem?.delta != null ? '+' + row.summary.mem.delta + 'MB' : '—' }}</template></el-table-column>
        <el-table-column label="采集时间" min-width="140"><template #default="{ row }">{{ fmtTime(row.started_at || row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button v-if="(row.status === 'running' || row.status === 'pending') && row.source === 'dispatch' && row.scenario !== '长监控'" link type="warning" @click="router.push(`/perf-collect/${row.id}`)">控制</el-button>
            <el-button link type="danger" @click="onDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'
import '@/styles/workspace-overlays.css'
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { dispatchPerfJob, listPerfRuns, deletePerfRun, listMyDevices, listPerfSets, createPerfSet, renamePerfSet, deletePerfSet } from '@/api'

const router = useRouter()
const scenarios = ['对话', '切换对话', '冷启动', '热启动', '杀进程', '首次安装', '长监控', '自定义']
const statuses = ['pending', 'running', 'completed', 'failed', 'canceled']
const form = reactive({ scenario: '长监控', variant: '', runner: 'win-01', duration: '', proc: '' })
const devices = ref([])
const sets = ref([])
const currentSet = ref(0)   // 0 = 全部/不归集
const runs = ref([])
const loading = ref(false)
const submitting = ref(false)
const dispatchVisible = ref(false)
const statusFilter = ref('')

const currentSetName = computed(() => (currentSet.value ? (sets.value.find((s) => s.id === currentSet.value)?.name || '未命名') : '不归集'))
const statusType = (s) => ({ pending: 'info', running: 'warning', completed: 'success', failed: 'danger', canceled: 'info' }[s] || 'info')
function fmtTime(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false }) }
function tsName() { const d = new Date(); const p = (n) => String(n).padStart(2, '0'); return `报告-${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}` }

async function loadDevices() { try { devices.value = await listMyDevices() } catch { devices.value = [] } }
async function loadSets() { try { sets.value = await listPerfSets() } catch { sets.value = [] } }
async function loadRuns() {
  loading.value = true
  try {
    const params = {}
    if (statusFilter.value) params.status = statusFilter.value
    if (currentSet.value) params.report_set_id = currentSet.value
    runs.value = await listPerfRuns(params)
  } finally { loading.value = false }
}

async function onNewSet() {
  try {
    const { value } = await ElMessageBox.prompt('报告集名称', '新建报告集', { inputValue: tsName(), confirmButtonText: '创建', cancelButtonText: '取消' })
    const s = await createPerfSet((value || tsName()).trim())
    await loadSets(); currentSet.value = s.id; await loadRuns()
    ElMessage.success('已创建：' + s.name)
  } catch { /* 取消 */ }
}
async function onRenameSet() {
  const cur = sets.value.find((s) => s.id === currentSet.value)
  try {
    const { value } = await ElMessageBox.prompt('新名称', '重命名报告集', { inputValue: cur?.name || '', confirmButtonText: '保存', cancelButtonText: '取消' })
    await renamePerfSet(currentSet.value, (value || cur.name).trim())
    await loadSets(); ElMessage.success('已重命名')
  } catch { /* 取消 */ }
}
async function onDeleteSet() {
  await ElMessageBox.confirm(`删除报告集「${currentSetName.value}」？其下采集记录会保留但脱离该集。`, '确认', { type: 'warning' })
  await deletePerfSet(currentSet.value); currentSet.value = 0; await loadSets(); await loadRuns()
  ElMessage.success('已删除')
}

async function submit() {
  if (!form.variant) { ElMessage.warning('请填写对象标签'); return }
  submitting.value = true
  try {
    const data = { scenario: form.scenario, variant: form.variant, runner: form.runner || 'win-01' }
    if (currentSet.value) data.report_set_id = currentSet.value
    if (form.scenario === '长监控' && form.duration) {
      let dur = String(form.duration).trim()
      if (/^\d+$/.test(dur)) dur += 's'   // 纯数字默认按秒（perfdog 对无单位数字会解析成 0s）
      data.duration = dur
    }
    if (form.proc) data.proc = form.proc
    const run = await dispatchPerfJob(data)
    dispatchVisible.value = false
    ElMessage.success('已下发')
    await loadRuns(); await loadSets()
    // 交互场景需人工按提示推进 → 直接跳采集控制页(用下发返回的 run.id)
    if (form.scenario !== '长监控' && run?.id) router.push(`/perf-collect/${run.id}`)
  } finally { submitting.value = false }
}
async function onDel(row) {
  await ElMessageBox.confirm(`删除记录 #${row.id}（${row.scenario}/${row.variant}）？`, '确认', { type: 'warning' })
  await deletePerfRun(row.id); ElMessage.success('已删除'); await loadRuns(); await loadSets()
}
onMounted(() => { loadDevices(); loadSets(); loadRuns() })
</script>

<style scoped>
.tip { margin-bottom: 14px; }
.tip code { background: rgba(0, 0, 0, .06); padding: 0 4px; border-radius: 3px; }
.set-card { margin-bottom: 14px; }
.set-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; min-width: 0; }
.set-row .el-select { max-width: 100%; }
.perf-dispatch :deep(.el-dialog .el-form-item__content > .el-input), .perf-dispatch :deep(.el-dialog .el-form-item__content > .el-select) { width: 100% !important; }
.set-row .lbl { font-weight: 600; }
.set-row .hint { font-size: 12px; color: #909399; margin-left: 6px; }
.form-card { margin-bottom: 16px; }
.warn { font-size: 12px; color: #e6a23c; margin-top: 4px; }
.head { display: flex; justify-content: space-between; align-items: center; }
</style>
