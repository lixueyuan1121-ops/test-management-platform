<template>
  <div class="perf-dispatch">
    <el-alert type="info" :closable="false" class="tip">
      <template #title>
        性能采集说明：所有场景都在这里下发。<b>长监控</b> 执行机无人值守自动跑；<b>冷启动 / 对话 / 热启动</b> 等交互场景，下发后到<b>执行机 agent 窗口</b>按提示操作应用并回车，采完自动回传。采集都归入下面选中的报告集，结果在「性能报告」看。
      </template>
    </el-alert>

    <!-- 报告集 -->
    <el-card shadow="never" class="set-card">
      <div class="set-row">
        <span class="lbl">报告集</span>
        <el-select v-model="currentSet" style="width:240px" @change="loadRuns">
          <el-option label="（全部 / 不归集）" :value="0" />
          <el-option v-for="s in sets" :key="s.id" :label="`${s.name}（${s.run_count}）`" :value="s.id" />
        </el-select>
        <el-button size="small" @click="onNewSet">新建报告集</el-button>
        <el-button size="small" :disabled="!currentSet" @click="onRenameSet">重命名</el-button>
        <el-button size="small" type="danger" plain :disabled="!currentSet" @click="onDeleteSet">删除</el-button>
        <span class="hint">下发/采集将归入选中的报告集；报告页按集独立展示。</span>
      </div>
    </el-card>

    <!-- 下发表单 -->
    <el-card shadow="never" class="form-card">
      <template #header><span>下发性能任务（source=dispatch）→ 归入「{{ currentSetName }}」</span></template>
      <el-form :model="form" label-width="80px" inline>
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
        <el-form-item><el-button type="primary" :loading="submitting" @click="submit">下发</el-button></el-form-item>
      </el-form>
      <div v-if="form.scenario !== '长监控'" class="warn">
        提示：「{{ form.scenario }}」需人工操作应用。下发后请到<b>执行机的 agent 窗口</b>，按 perfdog 提示操作（启动应用/发消息/切窗口）并回车，采完自动回传，回「性能报告」刷新查看。
      </div>
    </el-card>

    <!-- 记录表 -->
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>任务 / 采集记录{{ currentSet ? `（${currentSetName}）` : '' }}</span>
          <div>
            <el-select v-model="statusFilter" placeholder="全部状态" clearable size="small" style="width:130px;margin-right:8px" @change="loadRuns">
              <el-option v-for="s in statuses" :key="s" :label="s" :value="s" />
            </el-select>
            <el-button size="small" :loading="loading" @click="loadRuns">刷新</el-button>
          </div>
        </div>
      </template>
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
    </el-card>
  </div>
</template>

<script setup>
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
.set-row { display: flex; align-items: center; gap: 10px; }
.set-row .lbl { font-weight: 600; }
.set-row .hint { font-size: 12px; color: #909399; margin-left: 6px; }
.form-card { margin-bottom: 16px; }
.warn { font-size: 12px; color: #e6a23c; margin-top: 4px; }
.head { display: flex; justify-content: space-between; align-items: center; }
</style>
