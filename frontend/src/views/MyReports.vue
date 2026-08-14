<template>
  <div class="my-reports">
    <el-card>
      <template #header>
        <div class="header">
          <span>我的日报</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="loading" size="small" empty-text="该日没有指派给你的任务">
        <el-table-column prop="description" label="任务名称" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column prop="developer" label="开发" width="100" />
        <el-table-column label="需求" width="80">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">需求</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="已报进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="row._progress ?? 0" :status="row._progress >= 100 ? 'success' : ''" />
          </template>
        </el-table-column>
        <el-table-column label="日报状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row._reported" type="success" size="small">已提交</el-tag>
            <el-tag v-else type="warning" size="small">未提交</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button link type="primary" @click="openReport(row)">填报</el-button>
            <el-button link type="primary" @click="openChecklist(row)">验收清单</el-button>
            <el-button link type="success" @click="openAdopted(row)">已采纳用例</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 填报日报弹窗 -->
    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="`填报日报 · ${form.title || ''}`" width="620px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="测试进度">
          <el-slider v-model="form.progress_pct" :max="100" show-input style="padding-right:8px" />
        </el-form-item>
        <el-form-item label="是否上线">
          <el-switch v-model="form.is_online" /> <span class="tip">{{ form.is_online ? '今日已上线' : '未上线' }}</span>
        </el-form-item>
        <el-form-item label="工作量(人时)">
          <el-input-number v-model="form.workload_hours" :min="0" :max="24" :step="0.5" />
        </el-form-item>
        <el-form-item label="今日小结"><el-input v-model="form.summary" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="遗留问题">
          <div v-for="(it, i) in form.issues" :key="i" class="issue-row">
            <el-input v-model="it.title" placeholder="问题标题" style="flex:1" />
            <el-select v-model="it.severity" style="width:90px">
              <el-option label="blocker" value="blocker" />
              <el-option label="major" value="major" />
              <el-option label="minor" value="minor" />
            </el-select>
            <el-button link type="danger" @click="form.issues.splice(i,1)">删</el-button>
          </div>
          <el-button size="small" @click="form.issues.push({ title: '', severity: 'minor', status: 'open' })">+ 添加遗留问题</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">提交日报</el-button>
      </template>
    </el-dialog>

    <!-- 验收清单抽屉 -->
    <el-drawer v-model="cl.visible" :title="`验收清单 · ${cl.taskTitle}`" size="640px">
      <div class="cl-toolbar">
        <span class="cl-sum">共 {{ cl.items.length }} 项 · 通过 {{ clStat.passed }} · 失败 {{ clStat.failed }} · 阻塞 {{ clStat.blocked }} · 待执行 {{ clStat.pending }}</span>
        <el-button size="small" type="primary" plain @click="openAttach">添加测试点</el-button>
      </div>
      <el-table :data="cl.items" v-loading="cl.loading" size="small" empty-text="暂无验收项，点右上角「添加测试点」补挂已采纳的测试点">
        <el-table-column prop="title" label="测试点" min-width="160" show-overflow-tooltip />
        <el-table-column prop="category" label="维度" width="70" />
        <el-table-column prop="expected" label="预期" min-width="140" show-overflow-tooltip />
        <el-table-column label="结果" width="200">
          <template #default="{ row }">
            <el-button-group>
              <el-button size="small" :type="row.exec_status==='passed' ? 'success' : ''" :plain="row.exec_status!=='passed'" @click="tick(row,'passed')">通过</el-button>
              <el-button size="small" :type="row.exec_status==='failed' ? 'danger' : ''" :plain="row.exec_status!=='failed'" @click="tick(row,'failed')">失败</el-button>
              <el-button size="small" :type="row.exec_status==='blocked' ? 'warning' : ''" :plain="row.exec_status!=='blocked'" @click="tick(row,'blocked')">阻塞</el-button>
            </el-button-group>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="row.exec_status==='failed'" link type="danger" @click="openToIssue(row)">转遗留</el-button>
            <span v-else class="cl-dim">-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 已采纳用例抽屉（只读回溯）-->
    <el-drawer v-model="adopted.visible" :title="`已采纳用例 · ${adopted.taskTitle}`" size="640px">
      <el-table :data="adopted.items" v-loading="adopted.loading" size="small" empty-text="该任务暂无已采纳用例">
        <el-table-column label="维度" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="CAT_TYPE[row.category] || 'info'" effect="plain" size="small">{{ row.category || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="76" align="center">
          <template #default="{ row }">
            <el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="150" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="160">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期" min-width="140">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
      </el-table>
    </el-drawer>
    <el-dialog v-model="attach.visible" title="添加测试点到验收清单" width="560px">
      <div v-if="!attach.options.length" class="cl-dim" style="padding:12px 0">该项目暂无「已采纳、且未加入本清单」的测试点。</div>
      <el-checkbox-group v-else v-model="attach.selected">
        <div v-for="o in attach.options" :key="o.id" class="attach-row">
          <el-checkbox :value="o.id">
            <span>{{ o.title }}</span>
            <el-tag v-if="o.category" size="small" style="margin-left:6px">{{ o.category }}</el-tag>
            <el-tag v-if="o.priority" size="small" type="info" style="margin-left:4px">{{ o.priority }}</el-tag>
          </el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="attach.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!attach.selected.length" :loading="attach.saving" @click="doAttach">
          添加 {{ attach.selected.length ? `(${attach.selected.length})` : '' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 失败转遗留弹窗 -->
    <el-dialog v-model="toIssue.visible" title="转为遗留问题" width="520px">
      <el-form :model="toIssue.form" label-width="80px">
        <el-form-item label="标题"><el-input v-model="toIssue.form.title" placeholder="缺省用测试点标题" /></el-form-item>
        <el-form-item label="严重度">
          <el-select v-model="toIssue.form.severity" style="width:140px">
            <el-option label="blocker" value="blocker" />
            <el-option label="major" value="major" />
            <el-option label="minor" value="minor" />
          </el-select>
        </el-form-item>
        <el-form-item label="外部缺陷"><el-input v-model="toIssue.form.external_ref" placeholder="Jira/Tapd 缺陷ID（可选）" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="toIssue.visible = false">取消</el-button>
        <el-button type="primary" :loading="toIssue.saving" @click="doToIssue">创建遗留问题</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listTasks, listReports, upsertReport,
  getTaskChecklist, attachChecklist, updateChecklistItem, checklistItemToIssue, listAdoptableCases, listCases,
} from '@/api'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const tasks = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, saving: false })
const form = reactive({
  task_id: null, title: '', report_date: '', progress_pct: 0, is_online: false,
  workload_hours: 0, summary: '', issues: [],
})

// 验收清单抽屉
const cl = reactive({ visible: false, loading: false, taskId: null, taskTitle: '', items: [] })
const clStat = computed(() => {
  const s = { passed: 0, failed: 0, blocked: 0, pending: 0 }
  cl.items.forEach((it) => { s[it.exec_status] = (s[it.exec_status] || 0) + 1 })
  return s
})
// 手动补挂
const attach = reactive({ visible: false, saving: false, options: [], selected: [] })
// 失败转遗留
const toIssue = reactive({ visible: false, saving: false, itemId: null, form: { title: '', severity: 'major', external_ref: '' } })
// 已采纳用例（只读回溯）
const adopted = reactive({ visible: false, loading: false, taskTitle: '', items: [] })
// 维度 / 优先级 → el-tag 配色（与 AITestGen 口径一致）
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }

onMounted(async () => {
  projects.value = await app.fetchProjects()
  if (projects.value.length) { pid.value = pickDefaultProjectId(projects.value); await load() }
})

async function load() {
  if (!pid.value) return
  setLastProjectId(pid.value)
  loading.value = true
  try {
    const [myTasks, reports] = await Promise.all([
      listTasks({ project_id: pid.value, date: date.value, mine: true }),
      listReports(pid.value, date.value),
    ])
    const repByTask = {}
    reports.forEach((r) => { repByTask[r.task_id] = r })
    tasks.value = myTasks.map((t) => {
      const r = repByTask[t.id]
      return { ...t, _reported: !!r, _progress: r?.progress_pct ?? 0, _report: r }
    })
  } finally { loading.value = false }
}

function openReport(row) {
  form.task_id = row.id
  form.title = row.title
  form.report_date = date.value
  const r = row._report
  form.progress_pct = r?.progress_pct ?? 0
  form.is_online = r?.is_online ?? false
  form.workload_hours = r?.workload_hours ?? 0
  form.summary = r?.summary ?? ''
  form.issues = (r?.issues?.map((x) => ({ title: x.title, severity: x.severity, status: x.status })) || [])
  dialog.visible = true
}

async function submit() {
  if (!form.task_id) return
  dialog.saving = true
  try {
    await upsertReport({
      task_id: form.task_id, report_date: form.report_date,
      progress_pct: form.progress_pct, is_online: form.is_online,
      workload_hours: form.workload_hours, summary: form.summary,
      issues: form.issues.filter((i) => i.title),
    })
    ElMessage.success('日报已提交')
    await load()
  } finally { dialog.saving = false; dialog.visible = false }
}

// ---- 验收清单 ----
async function openChecklist(row) {
  cl.taskId = row.id
  cl.taskTitle = row.description || row.title
  cl.visible = true
  cl.loading = true
  try {
    cl.items = await getTaskChecklist(row.id)
  } finally { cl.loading = false }
}

// ---- 已采纳用例（只读回溯）----
async function openAdopted(row) {
  adopted.taskTitle = row.description || row.title
  adopted.visible = true
  adopted.loading = true
  try {
    // 单任务的已采纳用例(只读回溯),量有限,取一页(上限 200)
    const { items } = await listCases({ project_id: pid.value, task_id: row.id, review_status: 'adopted', limit: 200 })
    adopted.items = items || []
  } finally { adopted.loading = false }
}

async function tick(row, exec_status) {
  const prev = row.exec_status
  row.exec_status = exec_status  // 乐观更新
  try {
    const data = await updateChecklistItem(row.id, exec_status)
    Object.assign(row, data)  // 用返回 data 回写（executed_by/at）
  } catch {
    row.exec_status = prev
    ElMessage.error('操作失败，请重试')
  }
}

async function openAttach() {
  attach.selected = []
  attach.options = await listAdoptableCases(cl.taskId)
  attach.visible = true
}

async function doAttach() {
  attach.saving = true
  try {
    await attachChecklist(cl.taskId, attach.selected)
    ElMessage.success('已添加到验收清单')
    attach.visible = false
    cl.items = await getTaskChecklist(cl.taskId)
  } finally { attach.saving = false }
}

function openToIssue(row) {
  toIssue.itemId = row.id
  toIssue.form = { title: row.title || '', severity: 'major', external_ref: '' }
  toIssue.visible = true
}

async function doToIssue() {
  toIssue.saving = true
  try {
    await checklistItemToIssue(toIssue.itemId, {
      title: toIssue.form.title || undefined,
      severity: toIssue.form.severity,
      external_ref: toIssue.form.external_ref || undefined,
    })
    ElMessage.success('已创建遗留问题')
    toIssue.visible = false
  } finally { toIssue.saving = false }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.tip { color: #999; font-size: 12px; margin-left: 8px; }
.issue-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.cl-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.cl-sum { font-size: 12px; color: var(--tech-muted, #6b7280); }
.cl-dim { color: var(--tech-dim, #9aa3b2); }
.attach-row { padding: 4px 0; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
</style>
