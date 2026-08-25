<template>
  <div class="feedback-regression">
    <el-card>
      <template #header>
        <div class="header">
          <span>回归用例集</span>
          <div class="actions">
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
            <el-button type="primary" size="small" @click="openCreate">新建回归集</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="intro">
        把反馈用例组成回归集，可设<b>定时自动回归</b>（到点自动下发执行）、或点<b>立即回归</b>手动触发整集。
        集内 manual 用例会自动跳过。结果去「回归结果」查看。
      </el-alert>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无回归集">
        <el-table-column prop="name" label="集名" min-width="150" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="80" align="center"><template #default="{ row }">{{ row.case_count }}</template></el-table-column>
        <el-table-column label="定时" min-width="150">
          <template #default="{ row }">
            <template v-if="row.schedule_enabled && row.schedule_cron">
              <el-tag type="success" size="small">已启用</el-tag>
              <code class="cron">{{ row.schedule_cron }}</code>
              <div class="next">下次：{{ fmt(row.next_run_at) }}</div>
            </template>
            <el-tag v-else type="info" size="small">未设定时</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="上次回归" width="150"><template #default="{ row }">{{ fmt(row.last_run_at) }}</template></el-table-column>
        <el-table-column label="操作" width="300" align="center">
          <template #default="{ row }">
            <el-button link type="success" size="small" :loading="row._run" @click="runNow(row)">立即回归</el-button>
            <el-button link type="primary" size="small" @click="openCases(row)">用例</el-button>
            <el-button link type="warning" size="small" @click="openSchedule(row)">定时</el-button>
            <el-button link size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 建/编辑集 -->
    <el-dialog v-model="editDlg" :title="editing ? '编辑回归集' : '新建回归集'" width="440px">
      <el-form label-width="80px">
        <el-form-item label="集名"><el-input v-model="form.name" size="small" placeholder="如 公测反馈回归" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" size="small" /></el-form-item>
        <el-form-item label="执行设备">
          <el-select v-model="form.runner" size="small" style="width:100%" placeholder="选择执行设备">
            <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.name" @click="saveSet">保存</el-button>
      </template>
    </el-dialog>

    <!-- 定时设置（友好选择器 → cron） -->
    <el-dialog v-model="schedDlg" title="定时回归设置" width="440px">
      <el-form label-width="90px">
        <el-form-item label="启用定时"><el-switch v-model="sched.enabled" /></el-form-item>
        <template v-if="sched.enabled">
          <el-form-item label="频率">
            <el-radio-group v-model="sched.freq" @change="syncCron">
              <el-radio value="daily">每天</el-radio>
              <el-radio value="weekly">每周</el-radio>
              <el-radio value="custom">自定义</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="sched.freq === 'weekly'" label="星期">
            <el-select v-model="sched.weekday" size="small" style="width:120px" @change="syncCron">
              <el-option v-for="(w, i) in WEEKDAYS" :key="i" :label="w" :value="i" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="sched.freq !== 'custom'" label="时间">
            <el-time-picker v-model="sched.time" format="HH:mm" value-format="HH:mm" size="small" @change="syncCron" />
          </el-form-item>
          <el-form-item label="cron">
            <el-input v-model="sched.cron" size="small" :disabled="sched.freq !== 'custom'" placeholder="分 时 日 月 周" />
          </el-form-item>
          <div class="cron-hint">当前 cron：<code>{{ sched.cron || '（待设置）' }}</code>（分 时 日 月 周，5 段）</div>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="schedDlg = false">取消</el-button>
        <el-button type="primary" :loading="savingSched" @click="saveSchedule">保存</el-button>
      </template>
    </el-dialog>

    <!-- 集内用例管理 -->
    <el-drawer v-model="casesDrawer" :title="`集「${curSet?.name || ''}」的用例`" size="52%">
      <div class="drawer-actions">
        <el-button size="small" type="primary" @click="reloadSetCases">刷新</el-button>
        <el-button size="small" type="danger" :disabled="!setSelected.length" @click="removeCases">移出选中（{{ setSelected.length }}）</el-button>
        <span class="hint">加入用例请去「反馈用例库」勾选后「加入回归集」</span>
      </div>
      <el-table :data="setCases" v-loading="setCasesLoading" size="small" border stripe empty-text="集内暂无用例"
                @selection-change="(s) => (setSelected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="case_no" label="编号" width="60" align="center" />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }"><el-tag :type="KIND_TYPE[row.exec_kind] || 'info'" size="small" effect="plain">{{ row.exec_kind }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="用例标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="script" width="72" align="center">
          <template #default="{ row }"><el-tag v-if="row.has_script" type="success" size="small" effect="plain">有</el-tag><span v-else class="none">—</span></template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  feedbackSets, createFeedbackSet, updateFeedbackSet, deleteFeedbackSet,
  feedbackSetCases, removeFeedbackSetCases, runFeedbackSet, setFeedbackSchedule, listMyDevices,
} from '@/api'

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const KIND_TYPE = { gui: 'success', api: 'warning', cli: 'info', e2e: 'primary', manual: 'info' }

const rows = ref([])
const myDevices = ref([])
const loading = ref(false)

const editDlg = ref(false)
const editing = ref(false)
const form = reactive({ id: null, name: '', description: '', runner: 'mac-01' })
const saving = ref(false)

const schedDlg = ref(false)
const sched = reactive({ id: null, enabled: false, freq: 'daily', weekday: 1, time: '02:00', cron: '0 2 * * *' })
const savingSched = ref(false)

const casesDrawer = ref(false)
const curSet = ref(null)
const setCases = ref([])
const setCasesLoading = ref(false)
const setSelected = ref([])

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '—' }

async function reload() {
  loading.value = true
  try { rows.value = await feedbackSets() } catch { /* ignore */ } finally { loading.value = false }
}

function openCreate() {
  editing.value = false
  Object.assign(form, { id: null, name: '', description: '', runner: myDevices.value[0]?.runner_id || 'mac-01' })
  editDlg.value = true
}
function openEdit(row) {
  editing.value = true
  Object.assign(form, { id: row.id, name: row.name, description: row.description, runner: row.runner })
  editDlg.value = true
}
async function saveSet() {
  saving.value = true
  try {
    if (editing.value) await updateFeedbackSet(form.id, { name: form.name, description: form.description, runner: form.runner })
    else await createFeedbackSet({ name: form.name, description: form.description, runner: form.runner })
    ElMessage.success('已保存')
    editDlg.value = false
    reload()
  } catch { /* ignore */ } finally {
    saving.value = false
  }
}

async function runNow(row) {
  try {
    await ElMessageBox.confirm(`立即回归集「${row.name}」？将下发集内可自动化用例。`, '确认', { type: 'warning' })
  } catch { return }
  row._run = true
  try {
    const res = await runFeedbackSet(row.id)
    ElMessage.success(`已下发 ${res.run_ids.length} 条（批次 ${res.batch_id}），去「回归结果」查看`)
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    row._run = false
  }
}

function openSchedule(row) {
  sched.id = row.id
  sched.enabled = row.schedule_enabled
  sched.cron = row.schedule_cron || '0 2 * * *'
  // 反推友好选择器（尽力：能解析成每天/每周就填，否则自定义）
  parseCron(sched.cron)
  schedDlg.value = true
}
function parseCron(cron) {
  const parts = (cron || '').trim().split(/\s+/)
  if (parts.length === 5 && /^\d+$/.test(parts[0]) && /^\d+$/.test(parts[1])) {
    const hh = String(parts[1]).padStart(2, '0'), mm = String(parts[0]).padStart(2, '0')
    sched.time = `${hh}:${mm}`
    if (parts[2] === '*' && parts[3] === '*' && parts[4] === '*') { sched.freq = 'daily'; return }
    if (parts[2] === '*' && parts[3] === '*' && /^\d$/.test(parts[4])) { sched.freq = 'weekly'; sched.weekday = +parts[4]; return }
  }
  sched.freq = 'custom'
}
function syncCron() {
  if (sched.freq === 'custom') return
  const [hh, mm] = (sched.time || '02:00').split(':')
  const m = +mm, h = +hh
  sched.cron = sched.freq === 'daily' ? `${m} ${h} * * *` : `${m} ${h} * * ${sched.weekday}`
}
async function saveSchedule() {
  savingSched.value = true
  try {
    await setFeedbackSchedule(sched.id, sched.enabled ? sched.cron : null, sched.enabled)
    ElMessage.success(sched.enabled ? '定时已启用' : '定时已关闭')
    schedDlg.value = false
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    savingSched.value = false
  }
}

async function openCases(row) {
  curSet.value = row
  casesDrawer.value = true
  reloadSetCases()
}
async function reloadSetCases() {
  setCasesLoading.value = true
  try { setCases.value = await feedbackSetCases(curSet.value.id) } catch { /* ignore */ } finally { setCasesLoading.value = false }
}
async function removeCases() {
  try {
    await removeFeedbackSetCases(curSet.value.id, setSelected.value.map((r) => r.id))
    ElMessage.success('已移出')
    reloadSetCases()
    reload()
  } catch { /* ignore */ }
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`确认删除回归集「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try { await deleteFeedbackSet(row.id); ElMessage.success('已删除'); reload() } catch { /* ignore */ }
}

onMounted(async () => {
  reload()
  try { myDevices.value = await listMyDevices() } catch { /* ignore */ }
})
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; }
.intro { margin-bottom: 12px; }
.cron { background: #f4f4f5; padding: 1px 6px; border-radius: 4px; font-size: 12px; margin-left: 4px; }
.next { font-size: 11px; color: #909399; margin-top: 2px; }
.cron-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.drawer-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { font-size: 12px; color: #909399; }
.none { color: #c0c4cc; }
</style>
