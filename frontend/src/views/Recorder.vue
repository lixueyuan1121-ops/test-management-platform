<template>
  <div class="recorder">
    <el-card>
      <template #header>
        <div class="header">
          <span>录制生成 e2e 脚本</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" :disabled="recording || starting || saving">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="subProduct" placeholder="作用域" size="small" style="width:150px" :disabled="recording || starting || saving">
              <el-option label="项目级共享" :value="''" />
              <el-option v-for="sp in SUB_PRODUCTS" :key="sp" :label="sp" :value="sp" />
            </el-select>
            <el-select v-model="runner" placeholder="在线设备" size="small" style="width:200px" :disabled="recording || starting || saving"
                       no-data-text="去「我的设备」登记">
              <el-option v-for="d in devices" :key="d.runner_id" :value="d.runner_id"
                         :label="`${d.name || d.runner_id}（${d.runner_id}）`" />
            </el-select>
            <el-button v-if="!recording" type="primary" size="small" :loading="starting" :disabled="!pid || !runner || saving || stopping" @click="onStart">开始录制</el-button>
            <el-button v-else type="danger" size="small" :loading="stopping" @click="onStop">停止录制</el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="recording" type="success" :closable="false" show-icon class="rec-tip">
        录制中：请在被测客户端里正常操作（点击 / 输入）。<b>Alt+点击</b>任意元素 = 标为断言（可见/文本）。操作会实时出现在下方。
      </el-alert>
      <el-alert v-else-if="!steps.length" type="info" :closable="false" :closable-icon="false" class="rec-tip">
        选项目 + 在线设备 → 开始录制 → 在客户端操作 → 停止 → 复审 → 保存为 e2e 用例（自动回填用到的选择器）。
      </el-alert>

      <el-table :data="steps" size="small" border empty-text="尚无录制步骤" style="margin-top:10px">
        <el-table-column type="index" label="#" width="48" />
        <el-table-column label="动作" width="110">
          <template #default="{ row }">
            <el-tag :type="ACT_TYPE[row.action] || 'info'" size="small" effect="plain">{{ ACT_LABEL[row.action] || row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标元素" min-width="220">
          <template #default="{ row }">
            <span class="el-text">{{ row.text || `<${row.tag || 'el'}>` }}</span>
            <code v-if="bestCand(row)" class="cand">{{ candLabel(bestCand(row)) }}</code>
          </template>
        </el-table-column>
        <el-table-column label="断言 / 输入" min-width="320">
          <template #default="{ row }">
            <!-- 复审态(停止后)可编辑:把某步标为断言 + 填预期界面文案;录制中只读展示 -->
            <template v-if="!recording">
              <el-select :model-value="row.action" size="small" style="width:90px" @change="(v) => onActionChange(row, v)">
                <el-option label="点击" value="click" />
                <el-option label="输入" value="fill" />
                <el-option label="断言" value="assert" />
                <el-option v-if="row.type === 'checkbox' || row.type === 'radio'" label="勾选状态" value="set_checked" />
                <el-option v-if="row.tag === 'select'" label="选择选项" value="select_option" />
                <el-option label="按键" value="press" />
              </el-select>
              <template v-if="row.action === 'assert'">
                <el-select :model-value="row.assert?.kind || 'visible'" size="small" style="width:96px;margin:0 6px"
                           @change="(v) => setAssertKind(row, v)">
                  <el-option label="可见" value="visible" />
                  <el-option label="含文本" value="text" />
                </el-select>
                <el-input v-if="(row.assert?.kind) === 'text'" :model-value="row.assert?.expected || ''" size="small"
                          style="width:150px" placeholder="预期界面文案" @input="(v) => setAssertExpected(row, v)" />
              </template>
              <el-input v-else-if="row.action === 'fill'" :model-value="row.value || ''" size="small" style="width:150px;margin-left:6px"
                        placeholder="输入内容" @input="(v) => (row.value = v)" />
              <el-switch v-else-if="row.action === 'set_checked'" v-model="row.checked" style="margin-left:8px" active-text="勾选" inactive-text="取消" />
              <el-select v-else-if="row.action === 'select_option'" v-model="row.values" multiple filterable allow-create default-first-option size="small" style="width:200px;margin-left:8px" placeholder="选项值，回车添加">
                <el-option v-for="value in row.values || []" :key="value" :label="value" :value="value" />
              </el-select>
              <el-select v-else-if="row.action === 'press'" v-model="row.key_name" size="small" style="width:120px;margin-left:8px">
                <el-option v-for="key in ['Enter','Tab','Escape']" :key="key" :label="key" :value="key" />
              </el-select>
            </template>
            <template v-else>
              <span v-if="row.action === 'assert' && row.assert">{{ row.assert.kind === 'text' ? `文本含「${row.assert.expected}」` : '可见' }}</span>
              <span v-else-if="row.action === 'fill'">输入：{{ row.value }}</span>
              <span v-else-if="row.action === 'set_checked'">{{ row.checked ? '勾选' : '取消勾选' }}</span>
              <span v-else-if="row.action === 'select_option'">{{ (row.values || []).join(', ') }}</span>
              <span v-else-if="row.action === 'press'">{{ row.key_name }}</span>
              <span v-else class="muted">—</span>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" align="center">
          <template #default="{ $index }">
            <el-button v-if="!recording" link type="danger" size="small" @click="steps.splice($index, 1)">删</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert v-if="serverStatus === 'failed'" title="录制未完整结束。已收到的步骤保留供核对，请重新录制后保存。" type="error" :closable="false" style="margin-top:12px" />
      <div v-if="!recording && steps.length && serverStatus === 'stopped'" class="save-bar">
        <el-input v-model="title" size="small" style="width:240px" placeholder="用例标题" />
        <TaskPicker v-model="taskId" :tasks="tasks" placeholder="关联任务(必填)" style="width:220px" />
        <el-input v-model="precondition" size="small" style="width:320px" placeholder="前置条件(可选:起始位置/前置步骤)" />
        <el-button type="primary" size="small" :loading="saving" @click="onSave">保存为 e2e 用例</el-button>
        <span class="muted">保存时:命中已注册的复用,未命中的自动新建 key 并回填(多候选)</span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/store/app'
import { listMyDevices, listTasks, startRecord, getRecord, stopRecord, saveRecordAsCase } from '@/api'
import { pickDefaultProjectId } from '@/utils/lastProject'
import TaskPicker from '@/components/TaskPicker.vue'

const SUB_PRODUCTS = ['纳米Work云端版', '纳米Work桌面版', '360安全龙虾云端版', '360安全龙虾WSL']
const ACT_TYPE = { click: 'primary', fill: 'warning', assert: 'success' }
const ACT_LABEL = { click: '点击', fill: '输入', assert: '断言', set_checked: '勾选状态', select_option: '选择选项', press: '按键' }

const app = useAppStore()
const projects = ref([])
const devices = ref([])
const tasks = ref([])
const taskId = ref(null)
const pid = ref(null)
const subProduct = ref('')
const runner = ref('')
const recording = ref(false)
const starting = ref(false)
const stopping = ref(false)
const saving = ref(false)
const sessionId = ref(null)
const serverStatus = ref('')
const steps = ref([])
const title = ref('')
const precondition = ref('')
let pollTimer = null
let sessionVersion = 0

function candLabel(c) {
  if (!c || !c.by) return ''
  return `${c.by === 'testid' ? 'data-testid' : c.by}=${c.value}`
}
function bestCand(row) { return (row.candidates || [])[0] || null }

// ---- 复审态编辑:把某步改成 点击/输入/断言,断言可选「可见 / 含文本」并填预期界面文案 ----
// 解决:① 录制脚本无断言步会校验失败;② 预期太宽泛——手动标记预期界面即精确。
function onActionChange(row, action) {
  row.action = action
  if (action === 'assert') {
    // 默认按有无短文本给个初值:有文本→含文本(预填元素文本),否则→可见
    const txt = (row.text || '').trim()
    row.assert = (txt.length >= 2 && txt.length <= 20) ? { kind: 'text', expected: txt } : { kind: 'visible' }
  } else {
    delete row.assert
  }
}
function setAssertKind(row, kind) {
  row.assert = row.assert || {}
  row.assert.kind = kind
  if (kind === 'text' && !row.assert.expected) row.assert.expected = (row.text || '').trim()
}
function setAssertExpected(row, v) {
  row.assert = row.assert || { kind: 'text' }
  row.assert.expected = v
}

onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { projects.value = [] }
  try { devices.value = await listMyDevices() } catch { devices.value = [] }
  pid.value = pickDefaultProjectId(projects.value)
  if (devices.value.length) runner.value = devices.value[0].runner_id
  await loadTasks()
})
onUnmounted(stopPoll)

// 关联任务候选随项目变化加载(录制保存必填)。
async function loadTasks() {
  taskId.value = null
  if (!pid.value) { tasks.value = []; return }
  try { tasks.value = await listTasks({ project_id: pid.value }) } catch { tasks.value = [] }
}
watch(pid, loadTasks)

function stopPoll() {
  sessionVersion++ // 使已发出的旧请求失效，避免覆盖新录制或停止后的编辑。
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null }
}

function startPoll(id) {
  const version = sessionVersion
  const isCurrent = () => version === sessionVersion && sessionId.value === id && recording.value
  const poll = async () => {
    if (!isCurrent()) return
    try {
      const r = await getRecord(id)
      if (!isCurrent()) return
      serverStatus.value = r.status
      steps.value = r.events || []
      if (r.status === 'stopped' || r.status === 'done' || r.status === 'failed') {
        stopping.value = false;
        if (r.status === 'failed') ElMessage.error(r.error || '录制失败');
        if (!title.value) title.value = `录制_${new Date().toISOString().slice(5, 16).replace('T', '_')}`;
        recording.value = false
        stopPoll()
        return
      }
    } catch { /* 单次失败忽略 */ }
    if (isCurrent()) pollTimer = setTimeout(poll, 1500)
  }
  pollTimer = setTimeout(poll, 1500)
}

async function onStart() {
  if (starting.value || recording.value || stopping.value || saving.value) return
  stopPoll()
  const version = sessionVersion
  sessionId.value = null
  serverStatus.value = ''
  steps.value = []
  title.value = ''
  precondition.value = ''
  taskId.value = null
  starting.value = true
  try {
    const res = await startRecord({ project_id: pid.value, sub_product: subProduct.value, runner: runner.value, runner_device_id: devices.value.find(d => d.runner_id === runner.value)?.id })
    if (version !== sessionVersion) return
    sessionId.value = res.id
    recording.value = true
    ElMessage.success('已开始录制,去客户端操作吧(Alt+点击=断言)')
    startPoll(res.id)
  } catch { /* 拦截器已提示 */ } finally { starting.value = false }
}

async function onStop() {
  if (!recording.value || stopping.value) return
  stopPoll()
  const version = sessionVersion
  const id = sessionId.value
  stopping.value = true
  try {
    const r = await stopRecord(id)
    if (version !== sessionVersion || id !== sessionId.value) return
    serverStatus.value = r.status
    steps.value = r.events || steps.value
    if (r.status === 'stopping') {
      ElMessage.info('正在等待执行机上传最后的步骤，完成后即可编辑保存')
      startPoll(id)
    } else {
      recording.value = false
      stopPoll()
      if (!title.value) title.value = `录制_${new Date().toISOString().slice(5, 16).replace('T', '_')}`
    }
  } catch {
    if (version === sessionVersion && recording.value) startPoll(id)
  } finally { if (serverStatus.value !== 'stopping') stopping.value = false }
}

async function onSave() {
  if (saving.value || starting.value || recording.value || stopping.value) return
  if (serverStatus.value !== 'stopped') { ElMessage.warning('录制未完整结束，请重新录制'); return }
  if (!title.value.trim()) { ElMessage.warning('请填用例标题'); return }
  if (!taskId.value) { ElMessage.warning('请选择关联任务(必填)'); return }
  if (!steps.value.length) { ElMessage.warning('没有可保存的步骤'); return }
  if (!steps.value.some(s => s.action === 'assert' && (s.assert?.kind === 'visible' || (s.assert?.kind === 'text' && s.assert?.expected?.trim())))) { ElMessage.warning('请至少添加一个有效断言'); return }
  saving.value = true
  try {
    // 传回(可能已删步的)编辑版 events
    const c = await saveRecordAsCase(sessionId.value, {
      title: title.value.trim(), task_id: taskId.value,
      precondition: precondition.value.trim() || undefined, events: steps.value,
    })
    ElMessage.success(`已生成 e2e 用例「${c.title}」,可去用例库查看/执行`)
    stopPoll()
    steps.value = []; sessionId.value = null; title.value = ''; precondition.value = ''; taskId.value = null
  } catch { /* 已提示 */ } finally { saving.value = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rec-tip { margin-bottom: 4px; }
.el-text { margin-right: 8px; }
.cand { background: #f0f2f5; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.muted { color: #90a4ae; font-size: 12px; }
.save-bar { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
</style>
