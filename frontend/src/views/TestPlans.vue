<template>
  <div class="test-plans">
    <el-card>
      <template #header>
        <div class="header">
          <span>测试计划</span>
          <div class="actions">
            <el-select v-model="projectId" size="small" style="width:200px" placeholder="选择项目" @change="reload">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
            <el-button size="small" @click="openRuns()">执行历史</el-button>
            <el-button type="primary" size="small" :disabled="!projectId" @click="openCreate">新建计划</el-button>
          </div>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon class="intro">
        把主用例库的用例组成<b>测试计划</b>（固定集合），可<b>立即执行</b>整计划、或设<b>定时自动回归</b>（到点自动下发）。
        计划内 manual 用例执行时自动跳过；定时批次失败会推飞书告警（需配置通知通道）。
      </el-alert>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无测试计划（先选项目，再新建）">
        <el-table-column prop="name" label="计划名" min-width="150" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="76" align="center"><template #default="{ row }">{{ row.case_count }}</template></el-table-column>
        <el-table-column prop="runner" label="执行设备" width="110" show-overflow-tooltip />
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
        <el-table-column label="上次执行" width="140"><template #default="{ row }">{{ fmt(row.last_run_at) }}</template></el-table-column>
        <el-table-column label="操作" width="330" align="center">
          <template #default="{ row }">
            <el-button link type="success" size="small" :loading="row._run" @click="runNow(row)">立即执行</el-button>
            <el-button link type="primary" size="small" @click="openCases(row)">用例</el-button>
            <el-button link type="warning" size="small" @click="openSchedule(row)">定时</el-button>
            <el-button link size="small" @click="openRuns(row)">历史</el-button>
            <el-button link size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 建/编辑计划 -->
    <el-dialog v-model="editDlg" :title="editing ? '编辑测试计划' : '新建测试计划'" width="440px">
      <el-form label-width="80px">
        <el-form-item label="计划名"><el-input v-model="form.name" size="small" placeholder="如 每日冒烟回归" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" size="small" /></el-form-item>
        <el-form-item label="执行设备">
          <el-select v-model="form.runner" size="small" style="width:100%" placeholder="选择执行设备" filterable allow-create>
            <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!form.name" @click="savePlan">保存</el-button>
      </template>
    </el-dialog>

    <!-- 定时设置（友好选择器 → cron；与反馈回归集同款交互） -->
    <el-dialog v-model="schedDlg" title="定时执行设置" width="440px">
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

    <!-- 计划内用例管理（含候选用例添加） -->
    <el-drawer v-model="casesDrawer" :title="`计划「${curPlan?.name || ''}」的用例`" size="56%">
      <div class="drawer-actions">
        <el-button size="small" type="primary" @click="openAddCases">添加用例</el-button>
        <el-button size="small" type="danger" :disabled="!planSelected.length" @click="removeCasesSel">移出选中（{{ planSelected.length }}）</el-button>
        <el-button size="small" @click="reloadPlanCases">刷新</el-button>
      </div>
      <el-table :data="planCases" v-loading="planCasesLoading" size="small" border stripe empty-text="计划内暂无用例，点「添加用例」挑选"
                @selection-change="(s) => (planSelected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }"><el-tag :type="KIND_TYPE[row.exec_kind] || 'info'" size="small" effect="plain">{{ row.exec_kind }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="用例标题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="priority" label="优先级" width="70" align="center">
          <template #default="{ row }">{{ row.priority || '—' }}</template>
        </el-table-column>
        <el-table-column label="script" width="68" align="center">
          <template #default="{ row }"><el-tag v-if="row.has_script" type="success" size="small" effect="plain">有</el-tag><span v-else class="none">—</span></template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 候选用例挑选 -->
    <el-dialog v-model="addDlg" title="添加用例到计划" width="720px" top="6vh">
      <div class="add-filters">
        <el-input v-model="candKeyword" size="small" placeholder="按标题搜索" clearable style="width:220px" @keyup.enter="reloadCandidates" />
        <el-checkbox v-model="candOnlyRegression" @change="reloadCandidates">只看回归用例库</el-checkbox>
        <el-button size="small" @click="reloadCandidates">查询</el-button>
        <span class="hint">已采纳用例可入计划；manual 用例可加入但执行时跳过</span>
      </div>
      <el-table :data="candidates" v-loading="candLoading" size="small" border stripe height="380"
                empty-text="无候选用例" @selection-change="(s) => (candSelected = s)">
        <el-table-column type="selection" width="42" :selectable="(row) => !row.in_plan" />
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }"><el-tag :type="KIND_TYPE[row.exec_kind] || 'info'" size="small" effect="plain">{{ row.exec_kind }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
        <el-table-column label="回归库" width="70" align="center">
          <template #default="{ row }"><el-tag v-if="row.is_regression" type="warning" size="small" effect="plain">是</el-tag><span v-else class="none">—</span></template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }"><el-tag v-if="row.in_plan" type="info" size="small">已在计划</el-tag><span v-else class="none">可添加</span></template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="addDlg = false">取消</el-button>
        <el-button type="primary" :loading="adding" :disabled="!candSelected.length" @click="confirmAdd">
          添加选中（{{ candSelected.length }}）
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行历史 -->
    <el-drawer v-model="runsDrawer" :title="runsTitle" size="56%">
      <el-table :data="runRows" v-loading="runsLoading" size="small" border stripe empty-text="暂无执行记录">
        <el-table-column prop="plan_name" label="计划" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.plan_name || '（已删计划）' }}</template>
        </el-table-column>
        <el-table-column label="触发" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="TRIGGER_TYPE[row.trigger] || 'info'" size="small" effect="plain">{{ TRIGGER_LABEL[row.trigger] || row.trigger }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" min-width="190">
          <template #default="{ row }">
            <template v-if="row.stats">
              <span class="ok">✓{{ row.stats.passed }}</span>
              <span class="bad" v-if="row.stats.failed">✗{{ row.stats.failed }}</span>
              <span class="blk" v-if="row.stats.blocked">⊘{{ row.stats.blocked }}</span>
              <span class="pending" v-if="!row.stats.finished">（{{ row.stats.pending + row.stats.running }} 未完）</span>
              <span class="total">/ {{ row.stats.total }}</span>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="批次" width="180">
          <template #default="{ row }"><code class="batch">{{ row.batch_id }}</code></template>
        </el-table-column>
        <el-table-column label="时间" width="140"><template #default="{ row }">{{ fmt(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="gotoExecResults(row)">查看明细</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  addPlanCases, createTestPlan, deleteTestPlan, getTestPlan, listMyDevices,
  planCandidateCases, removePlanCases, runTestPlan, setTestPlanSchedule, testPlanRuns,
  testPlans, updateTestPlan,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const router = useRouter()
const app = useAppStore()

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const KIND_TYPE = { gui: 'success', api: 'warning', cli: 'info', e2e: 'primary', manual: 'info' }
const TRIGGER_TYPE = { auto: 'warning', ci: 'danger', manual: 'info' }
const TRIGGER_LABEL = { auto: '定时', ci: 'CI', manual: '手动' }

const projects = ref([])
const projectId = ref(null)
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
const curPlan = ref(null)
const planCases = ref([])
const planCasesLoading = ref(false)
const planSelected = ref([])

const addDlg = ref(false)
const candidates = ref([])
const candLoading = ref(false)
const candSelected = ref([])
const candKeyword = ref('')
const candOnlyRegression = ref(false)
const adding = ref(false)

const runsDrawer = ref(false)
const runRows = ref([])
const runsLoading = ref(false)
const runsPlan = ref(null)
const runsTitle = computed(() => (runsPlan.value ? `计划「${runsPlan.value.name}」执行历史` : '本项目计划执行历史'))

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '—' }

async function reload() {
  if (!projectId.value) return
  setLastProjectId(projectId.value)
  loading.value = true
  try { rows.value = await testPlans(projectId.value) } catch { /* ignore */ } finally { loading.value = false }
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
async function savePlan() {
  saving.value = true
  try {
    if (editing.value) await updateTestPlan(form.id, { name: form.name, description: form.description, runner: form.runner })
    else await createTestPlan({ project_id: projectId.value, name: form.name, description: form.description, runner: form.runner })
    ElMessage.success('已保存')
    editDlg.value = false
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    saving.value = false
  }
}

async function runNow(row) {
  try {
    await ElMessageBox.confirm(`立即执行计划「${row.name}」？将下发计划内可自动化用例到 ${row.runner}。`, '确认', { type: 'warning' })
  } catch { return }
  row._run = true
  try {
    const res = await runTestPlan(row.id, null)
    ElMessage.success(`已下发 ${res.run_ids.length} 条（批次 ${res.batch_id}），去「执行结果」查看`)
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    row._run = false
  }
}

function openSchedule(row) {
  sched.id = row.id
  sched.enabled = row.schedule_enabled
  sched.cron = row.schedule_cron || '0 2 * * *'
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
  sched.cron = sched.freq === 'daily' ? `${+mm} ${+hh} * * *` : `${+mm} ${+hh} * * ${sched.weekday}`
}
async function saveSchedule() {
  savingSched.value = true
  try {
    await setTestPlanSchedule(sched.id, sched.enabled ? sched.cron : null, sched.enabled)
    ElMessage.success(sched.enabled ? '定时已启用' : '定时已关闭')
    schedDlg.value = false
    reload()
  } catch { /* 拦截器已提示 */ } finally {
    savingSched.value = false
  }
}

async function openCases(row) {
  curPlan.value = row
  casesDrawer.value = true
  reloadPlanCases()
}
async function reloadPlanCases() {
  planCasesLoading.value = true
  try {
    const detail = await getTestPlan(curPlan.value.id)
    planCases.value = detail.cases || []
  } catch { /* ignore */ } finally { planCasesLoading.value = false }
}
async function removeCasesSel() {
  try {
    await removePlanCases(curPlan.value.id, planSelected.value.map((r) => r.id))
    ElMessage.success('已移出')
    reloadPlanCases()
    reload()
  } catch { /* ignore */ }
}

function openAddCases() {
  candKeyword.value = ''
  candOnlyRegression.value = false
  addDlg.value = true
  reloadCandidates()
}
async function reloadCandidates() {
  candLoading.value = true
  try {
    candidates.value = await planCandidateCases(curPlan.value.id, {
      keyword: candKeyword.value || undefined,
      only_regression: candOnlyRegression.value || undefined,
    })
  } catch { /* ignore */ } finally { candLoading.value = false }
}
async function confirmAdd() {
  adding.value = true
  try {
    const res = await addPlanCases(curPlan.value.id, candSelected.value.map((r) => r.id))
    ElMessage.success(`已添加 ${res.added} 条`)
    addDlg.value = false
    reloadPlanCases()
    reload()
  } catch { /* ignore */ } finally { adding.value = false }
}

async function openRuns(row) {
  runsPlan.value = row || null
  runsDrawer.value = true
  runsLoading.value = true
  try {
    runRows.value = await testPlanRuns({ project_id: projectId.value, plan_id: row?.id })
  } catch { /* ignore */ } finally { runsLoading.value = false }
}
function gotoExecResults(row) {
  router.push({ path: '/exec-results', query: { project_id: projectId.value, batch_id: row.batch_id } })
}

async function del(row) {
  try {
    await ElMessageBox.confirm(`确认删除测试计划「${row.name}」？（执行历史保留）`, '删除确认', { type: 'warning' })
  } catch { return }
  try { await deleteTestPlan(row.id); ElMessage.success('已删除'); reload() } catch { /* ignore */ }
}

onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    if (projects.value.length && !projectId.value) projectId.value = pickDefaultProjectId(projects.value)
  } catch { /* ignore */ }
  try { myDevices.value = await listMyDevices() } catch { /* ignore */ }
  reload()
})
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.actions { display: flex; gap: 8px; align-items: center; }
.intro { margin-bottom: 12px; }
.cron { background: #f4f4f5; padding: 1px 6px; border-radius: 4px; font-size: 12px; margin-left: 4px; }
.next { font-size: 11px; color: #909399; margin-top: 2px; }
.cron-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.drawer-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.add-filters { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.hint { font-size: 12px; color: #909399; }
.none { color: #c0c4cc; }
.ok { color: #00926e; font-weight: 600; margin-right: 6px; }
.bad { color: #e5565f; font-weight: 600; margin-right: 6px; }
.blk { color: #e8a23d; font-weight: 600; margin-right: 6px; }
.pending { color: #909399; font-size: 12px; }
.total { color: #909399; font-size: 12px; }
.batch { background: #f4f4f5; padding: 1px 6px; border-radius: 4px; font-size: 12px; }
</style>
