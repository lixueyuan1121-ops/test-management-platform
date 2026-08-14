<template>
  <div class="tasks">
    <el-card>
      <template #header>
        <div class="header">
          <span>任务分配</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:180px" @change="load">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" size="small" style="width:150px" @change="load" />
            <el-button size="small" @click="onCopy" :disabled="!pid">复制昨日</el-button>
            <el-button type="primary" size="small" @click="openCreate" :disabled="!pid">新建任务</el-button>
          </div>
        </div>
      </template>
      <el-table :data="tasks" v-loading="loading" size="small" empty-text="该日无任务" row-key="id" @expand-change="onExpandChange">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="cl-expand">
              <div v-if="!row._summary && !row._summaryFailed" class="cl-empty">该任务暂无验收清单</div>
              <template v-else>
                <div class="cl-toolbar">
                  <el-select v-model="row._runner" size="small" style="width:160px"
                             :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
                    <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
                  </el-select>
                  <el-button type="primary" size="small" plain
                             :loading="row._enqueuing"
                             :disabled="!(row._checked && row._checked.length)"
                             @click="sendToRunner(row)">
                    发送到本地执行{{ row._checked && row._checked.length ? `（${row._checked.length}）` : '' }}
                  </el-button>
                  <span class="cl-hint">勾选后下发；执行机跑完自动回写通过/失败</span>
                </div>
                <el-table :data="row._items || []" v-loading="row._itemsLoading" size="small"
                          empty-text="无验收项"
                          @selection-change="(sel) => (row._checked = sel)">
                  <el-table-column type="selection" width="40" />
                  <el-table-column prop="title" label="测试点" min-width="180" show-overflow-tooltip />
                  <el-table-column prop="category" label="维度" width="80" />
                  <el-table-column label="类型" width="70" align="center">
                    <template #default="{ row: it }">
                      <el-tag :type="KIND_TYPE[it.exec_kind || 'gui'] || 'info'" size="small" effect="plain">
                        {{ KIND_LABEL[it.exec_kind || 'gui'] || it.exec_kind }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="结果" width="90">
                    <template #default="{ row: it }">
                      <el-popover v-if="it.exec_reason" placement="left" :width="360" trigger="click">
                        <template #reference>
                          <el-tag :type="EXEC_META[it.exec_status]?.type || 'info'" size="small" effect="light"
                                  style="cursor:pointer" title="点击查看执行原因">
                            {{ EXEC_META[it.exec_status]?.label || it.exec_status }}
                            <el-icon style="margin-left:2px"><InfoFilled /></el-icon>
                          </el-tag>
                        </template>
                        <div class="exec-detail">
                          <div class="exec-detail-head">
                            <el-tag :type="it.exec_verdict === 'pass' ? 'success' : 'danger'" size="small">
                              {{ it.exec_verdict === 'pass' ? '通过' : '失败' }}
                            </el-tag>
                            <span class="exec-meta">{{ it.exec_runner || '' }} · {{ fmtExecAt(it.exec_run_at) }}</span>
                          </div>
                          <div class="exec-reason">{{ it.exec_reason }}</div>
                        </div>
                      </el-popover>
                      <el-tag v-else :type="EXEC_META[it.exec_status]?.type || 'info'" size="small" effect="light">
                        {{ EXEC_META[it.exec_status]?.label || it.exec_status }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="任务名称" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.is_carried" size="small" type="warning" effect="light" style="margin-right:6px">
              顺延自 {{ (row.assigned_date || '').slice(5) }}
            </el-tag>
            {{ row.description || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="module" label="模块" width="110" />
        <el-table-column prop="developer" label="开发" width="100" />
        <el-table-column label="需求地址" width="120">
          <template #default="{ row }">
            <el-link v-if="row.requirement_url" :href="row.requirement_url" target="_blank" type="primary" :underline="false">查看</el-link>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column prop="assigned_to_name" label="指派给" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-dropdown trigger="click" @command="(s) => changeStatus(row, s)">
              <el-tag :type="STATUS_META[row.status]?.type || 'info'" effect="light" size="small" class="status-trigger">
                {{ STATUS_META[row.status]?.label || row.status }}<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-tag>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-for="(m, k) in STATUS_META" :key="k" :command="k" :disabled="k === row.status">
                    {{ m.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
        <el-table-column label="验收进度" width="190">
          <template #default="{ row }">
            <div v-if="row._summary" class="cl-prog">
              <el-progress
                :percentage="row._summary.total ? Math.round(row._summary.passed / row._summary.total * 100) : 0"
                :stroke-width="8" :show-text="false" color="var(--tech-signal)" style="width:70px"
              />
              <span class="cl-nums">{{ row._summary.passed }}/{{ row._summary.total }}</span>
              <el-tag v-if="row._summary.failed" type="danger" size="small" effect="light">失败{{ row._summary.failed }}</el-tag>
            </div>
            <span v-else class="cl-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="onDel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-if="dialog.visible" v-model="dialog.visible" :title="dialog.id ? '编辑任务' : '新建任务'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="任务名称" required><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="需求地址"><el-input v-model="form.requirement_url" placeholder="http://..." /></el-form-item>
        <el-form-item label="开发"><el-input v-model="form.developer" placeholder="开发人员姓名" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" /></el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="form.priority" style="width:100%">
            <el-option label="P0" value="p0" /><el-option label="P1" value="p1" />
            <el-option label="P2" value="p2" /><el-option label="P3" value="p3" />
          </el-select>
        </el-form-item>
        <el-form-item label="指派给" required>
          <el-select v-model="form.assigned_to" filterable placeholder="选择项目成员" style="width:100%">
            <el-option v-for="m in members" :key="m.user_id" :label="`${m.name} (${m.username})`" :value="m.user_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="分配日期" required>
          <el-date-picker v-model="form.assigned_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item v-if="dialog.id" label="状态">
          <el-select v-model="form.status" style="width:100%">
            <el-option v-for="(m, k) in STATUS_META" :key="k" :label="m.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { listProjects, listMembers, listTasks, createTask, updateTask, deleteTask, copyYesterday, getChecklistSummary, getTaskChecklist, enqueueExec, listMyDevices } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { ArrowDown, InfoFilled } from '@element-plus/icons-vue'

// 任务状态四态：待测 → 测试中 →(阻塞)→ 已上线；标签配色与首页 KPI 一致
const STATUS_META = {
  pending: { label: '待测', type: 'info' },
  testing: { label: '测试中', type: 'warning' },
  blocked: { label: '阻塞', type: 'danger' },
  online: { label: '已上线', type: 'success' },
  closed: { label: '已关闭', type: 'info' },
}

// 验收清单项三态结果配色（沿用全局口径：通过青绿/失败红/阻塞warn/待测灰）
const EXEC_META = {
  passed: { label: '通过', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  blocked: { label: '阻塞', type: 'warning' },
  pending: { label: '待测', type: 'info' },
}
// 执行类型 → 标签配色/文案 + 排序权重(清单项按类型聚拢,方便按类勾选)
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const KIND_ORDER = { gui: 0, e2e: 1, api: 2, cli: 3, manual: 4 }

// 可下发的执行机 = 当前成员登记的"我的设备"(下发到自己的机器执行)。runner_id 为下发标识。
const myDevices = ref([])

const auth = useAuthStore()
const projects = ref([])
const pid = ref(null)
const date = ref(new Date().toISOString().slice(0, 10))
const tasks = ref([])
const members = ref([])
const loading = ref(false)
const dialog = reactive({ visible: false, id: null, saving: false })
const form = reactive({ title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to: null, assigned_date: '', description: '', status: 'pending' })

onMounted(async () => {
  try { myDevices.value = await listMyDevices() } catch { myDevices.value = [] }
  projects.value = await listProjects()
  pid.value = pickDefaultProjectId(projects.value)
  if (pid.value) await load()
})

async function loadMembers() {
  if (!pid.value) { members.value = []; return }
  members.value = await listMembers(pid.value)
}
watch(pid, loadMembers)

async function load() {
  if (!pid.value) return
  setLastProjectId(pid.value)   // 记住当前项目，供其他页面默认选中
  await loadMembers()
  loading.value = true
  try {
    // 任务与验收汇总并行拉取；汇总失败不阻断表格，记 _summaryFailed 供展开区区分「无清单」与「汇总不可用」
    const [rows, summaryResult] = await Promise.all([
      listTasks({ project_id: pid.value, date: date.value }),
      getChecklistSummary(pid.value, date.value).catch(() => null),
    ])
    const summary = summaryResult || {}
    const summaryFailed = summaryResult === null
    tasks.value = rows.map((t) => ({
      ...t,
      _summary: summary[String(t.id)] || null,
      _summaryFailed: summaryFailed,
      _items: null,
      _itemsLoading: false,
      _runner: myDevices.value[0]?.runner_id || '',   // 默认下发到我的第一台设备
      _checked: [],          // 展开区勾选的清单项
      _enqueuing: false,
    }))
  } finally { loading.value = false }
}

async function onExpandChange(row, expandedRows) {
  const isExpanded = expandedRows.some((r) => r.id === row.id)
  if (!isExpanded) return
  if (!row._summary && !row._summaryFailed) return  // 确定无清单：不请求；汇总失败则仍尝试拉明细
  if (row._items !== null) return    // 已缓存：不重复请求
  row._itemsLoading = true
  try {
    const items = await getTaskChecklist(row.id)
    // 按执行类型聚拢,方便按类勾选
    row._items = items.sort((a, b) => (KIND_ORDER[a.exec_kind || 'gui'] ?? 9) - (KIND_ORDER[b.exec_kind || 'gui'] ?? 9))
  }
  catch { row._items = [] }
  finally { row._itemsLoading = false }
}

async function sendToRunner(row) {
  const items = row._checked || []
  if (!items.length) return
  if (!row._runner) { ElMessage.warning('请选择执行机'); return }
  // 人工(manual)用例不可自动化执行,不下发(后端也会 400 兜底;这里前置拦截更友好)
  const manualItem = items.find((it) => (it.exec_kind || 'gui') === 'manual')
  if (manualItem) { ElMessage.warning(`含人工(manual)用例「${manualItem.title || ''}」,不能下发到执行机;请取消勾选后重试`); return }
  row._enqueuing = true
  try {
    const res = await enqueueExec(pid.value, row._runner, items.map((it) => it.id))
    const n = res?.run_ids?.length || items.length
    ElMessage.success(`已下发 ${n} 条到 ${row._runner}，执行机跑完会自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { row._enqueuing = false }
}

function fmtExecAt(s) {
  if (!s) return ''
  return String(s).replace('T', ' ').slice(0, 16)
}

function openCreate() {
  dialog.id = null
  Object.assign(form, { title: '', requirement_url: '', developer: '', module: '', priority: 'p2', assigned_to: null, assigned_date: date.value, description: '', status: 'pending' })
  dialog.visible = true
}
function openEdit(row) {
  dialog.id = row.id
  Object.assign(form, {
    title: row.title, requirement_url: row.requirement_url || '', developer: row.developer || '',
    module: row.module || '', priority: row.priority, assigned_to: row.assigned_to,
    assigned_date: row.assigned_date, description: row.description || '', status: row.status,
  })
  dialog.visible = true
}
async function submit() {
  if (!form.title || !form.assigned_to || !form.assigned_date) { ElMessage.warning('任务名称/指派/日期必填'); return }
  dialog.saving = true
  try {
    const payload = { ...form, project_id: pid.value }
    if (dialog.id) await updateTask(dialog.id, payload)
    else await createTask(payload)
    ElMessage.success('保存成功')
        await load()
  } finally { dialog.saving = false; dialog.visible = false }
}
async function changeStatus(row, s) {
  if (s === row.status) return
  await updateTask(row.id, { status: s })
  row.status = s
  ElMessage.success(`状态已更新为「${STATUS_META[s].label}」`)
}
async function onDel(row) {
  await ElMessageBox.confirm(`删除任务「${row.title}」？`, '确认', { type: 'warning' })
  await deleteTask(row.id)
  ElMessage.success('已删除')
  await load()
}
async function onCopy() {
  await ElMessageBox.confirm(`把 ${date.value} 前一天的任务复制到 ${date.value}？`, '复制昨日', { type: 'info' })
  const r = await copyYesterday(pid.value, date.value)
  ElMessage.success(`已复制 ${r.copied} 条`)
  await load()
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; }
.status-trigger { cursor: pointer; }
.cl-prog { display: flex; align-items: center; gap: 8px; }
.cl-nums { font-family: var(--tech-mono, monospace); font-size: 12px; color: var(--tech-fg, #1a1d21); }
.cl-dim { color: var(--tech-dim, #9aa3b2); }
.cl-expand { padding: 8px 16px; }
.cl-empty { color: var(--tech-dim, #9aa3b2); font-size: 13px; padding: 4px 0; }
.cl-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cl-hint { color: var(--tech-dim, #9aa3b2); font-size: 12px; }
.exec-detail-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.exec-meta { color: var(--tech-dim, #9aa3b2); font-size: 12px; }
.exec-reason { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.5; max-height: 240px; overflow: auto; }
</style>
