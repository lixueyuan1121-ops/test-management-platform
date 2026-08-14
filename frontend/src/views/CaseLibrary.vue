<template>
  <div class="case-library">
    <el-card>
      <template #header>
        <div class="header">
          <span>用例库</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="reviewStatus" placeholder="采纳状态" size="small" clearable style="width:120px" @change="load">
              <el-option label="已采纳" value="adopted" />
              <el-option label="已否决" value="rejected" />
              <el-option label="待定" value="pending" />
            </el-select>
            <el-select v-model="taskId" placeholder="关联任务" size="small" clearable filterable fit-input-width style="width:240px" @change="load">
              <el-option v-for="t in tasks" :key="t.id" :label="t.description || t.title" :value="t.id" :title="t.description || t.title" />
            </el-select>
            <el-select v-model="category" placeholder="维度" size="small" clearable style="width:110px" @change="load">
              <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
            </el-select>
            <el-select v-model="execKindFilter" placeholder="执行类型" size="small" clearable style="width:120px">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
            <el-input
              v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px"
              @keyup.enter="load" @clear="load"
            />
          </div>
        </div>
      </template>

      <div v-if="selected.length" class="dispatch-bar">
        <span class="sel-info">已选 {{ selected.length }} 条</span>
        <el-select v-model="runner" size="small" style="width:180px"
                   :placeholder="myDevices.length ? '选择我的设备' : '未登记设备'" no-data-text="去『我的设备』注册">
          <el-option v-for="d in myDevices" :key="d.runner_id" :label="`${d.name}(${d.runner_id})`" :value="d.runner_id" />
        </el-select>
        <el-button type="primary" size="small" :loading="dispatching" @click="dispatchSelected">发送到执行机</el-button>
        <el-divider direction="vertical" />
        <el-button size="small" @click="bulkReview('adopted')">批量采纳</el-button>
        <el-button size="small" type="danger" plain @click="bulkDelete">批量删除</el-button>
        <span class="sel-hint">下发仅对『已采纳+有关联任务+非人工』的选中项生效</span>
      </div>

      <el-table :data="displayRows" v-loading="loading" size="small" border stripe empty-text="没有符合条件的用例"
                @selection-change="(s) => (selected = s)">
        <el-table-column type="selection" width="42" />
        <el-table-column label="维度" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="CAT_TYPE[row.category] || 'info'" effect="plain" size="small">{{ row.category || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="PRI_TYPE[(row.priority || '').toUpperCase()] || 'info'" size="small">{{ row.priority || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行类型" width="110" align="center">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.kind_reason" :content="'AI 判定：' + (row.kind_reason || '')" placement="top">
              <el-select :model-value="row.exec_kind || 'gui'" size="small" style="width:90px"
                         @change="(v) => onExecKindChange(row, v)">
                <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
              </el-select>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="180">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="采纳状态" width="100" align="center">
          <template #default="{ row }">
            <el-select :model-value="row.review_status || 'pending'" size="small" style="width:80px"
                       @change="(v) => onReviewChange(row, v)">
              <el-option label="采纳" value="adopted" />
              <el-option label="否决" value="rejected" />
              <el-option label="待定" value="pending" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="info" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="edit.visible" title="编辑用例" width="560px">
      <el-form label-width="72px">
        <el-form-item label="标题"><el-input v-model="edit.title" /></el-form-item>
        <el-form-item label="维度">
          <el-select v-model="edit.category" clearable style="width:140px">
            <el-option v-for="c in CATEGORIES" :key="c" :label="c" :value="c" />
          </el-select>
          <el-select v-model="edit.priority" clearable style="width:100px;margin-left:8px" placeholder="优先级">
            <el-option v-for="p in ['P0','P1','P2','P3']" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="步骤"><el-input v-model="edit.steps" type="textarea" :rows="4" placeholder="可多步,换行分隔" /></el-form-item>
        <el-form-item label="预期"><el-input v-model="edit.expected" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="edit.visible = false">取消</el-button>
        <el-button type="primary" :loading="edit.saving" @click="doEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detail.visible" title="用例详情" size="480px">
      <div v-if="detail.row" class="detail">
        <p><b>{{ detail.row.title }}</b></p>
        <p class="d-row"><span class="d-k">执行类型</span> {{ (detail.row.exec_kind || 'gui').toUpperCase() }}</p>
        <p v-if="detail.row.kind_reason" class="d-row"><span class="d-k">判定理由</span> {{ detail.row.kind_reason }}</p>
        <p class="d-row"><span class="d-k">维度/优先级</span> {{ detail.row.category || '—' }} / {{ detail.row.priority || '—' }}</p>
        <p class="d-row"><span class="d-k">步骤</span></p>
        <pre class="d-pre">{{ detail.row.steps || '—' }}</pre>
        <p class="d-row"><span class="d-k">预期</span></p>
        <pre class="d-pre">{{ detail.row.expected || '—' }}</pre>
        <p class="d-row"><span class="d-k">script</span></p>
        <pre class="d-pre">{{ prettyScript(detail.row.script) }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProjects, listTasks, listCases, setCaseExecKind, attachChecklist, enqueueExec, listMyDevices, reviewTestcase, updateTestcase, deleteTestcase } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 维度 / 优先级 → el-tag 配色（与 AITestGen 口径一致）
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const CATEGORIES = ['功能', '边界', '异常', '兼容', '性能']
// 采纳三态 → 配色/文案（采纳=success / 否决=danger / 待定=info）
const RV_TYPE = { adopted: 'success', rejected: 'danger', pending: 'info' }
const RV_LABEL = { adopted: '已采纳', rejected: '已否决', pending: '待定' }
// 自动化执行类型：gui(客户端 UI) / api(接口) / cli(命令行) / e2e(多步端到端) / manual(人工，不下发)。
// 下发到 runner 时决定 Claude Code 怎么跑；manual 不派发到执行机。
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' },
  { value: 'api', label: 'API' },
  { value: 'cli', label: 'CLI' },
  { value: 'e2e', label: 'E2E' },
  { value: 'manual', label: '人工' },
]

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const reviewStatus = ref(null)
const category = ref(null)
const keyword = ref('')
const rows = ref([])
const loading = ref(false)
const execKindFilter = ref(null)   // 执行类型筛选(null=全部)

// 执行类型 → 标签配色 + 排序权重(同类聚拢,方便按类勾选)
const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const KIND_ORDER = { gui: 0, e2e: 1, api: 2, cli: 3, manual: 4 }

// 展示行:按执行类型筛选 + 按类型排序聚拢(同类相邻,方便"选某类全勾")
const displayRows = computed(() => {
  let rs = rows.value
  if (execKindFilter.value) rs = rs.filter((r) => (r.exec_kind || 'gui') === execKindFilter.value)
  return [...rs].sort((a, b) => (KIND_ORDER[a.exec_kind || 'gui'] ?? 9) - (KIND_ORDER[b.exec_kind || 'gui'] ?? 9))
})

// ---- 下发到执行机(用例库入口)----
// 可下发的执行机 = 当前成员登记的"我的设备"(下发到自己机器执行)。
const myDevices = ref([])
const selected = ref([])
const runner = ref('')
const dispatching = ref(false)

// 某行能否下发:必须已采纳(attachChecklist 要求)+ 有关联任务 + 非 manual。
function canDispatch(row) {
  return row.review_status === 'adopted' && !!row.task_id && (row.exec_kind || 'gui') !== 'manual'
}

// 用例库下发:用例不一定挂清单项 → 先按任务分组 attachChecklist 建/取清单项,再 enqueue。
async function dispatchSelected() {
  if (!selected.value.length) return
  if (!runner.value) { ElMessage.warning('请先选择执行设备(去『我的设备』注册)'); return }
  // selection 已放开(为支持批量采纳/删除),这里只取可下发的选中项
  const items = selected.value.filter(canDispatch)
  if (!items.length) { ElMessage.warning('选中项里没有可下发的用例(需:已采纳 + 有关联任务 + 非人工)'); return }
  const skipped = selected.value.length - items.length
  dispatching.value = true
  try {
    // 按 task_id 分组:同一任务的用例一起 attachChecklist,拿回 checklist_item.id
    const byTask = new Map()
    for (const r of items) {
      if (!byTask.has(r.task_id)) byTask.set(r.task_id, [])
      byTask.get(r.task_id).push(r.id)
    }
    const itemIds = []
    for (const [tid, caseIds] of byTask) {
      const checklist = await attachChecklist(tid, caseIds)   // 幂等:已存在则复用,返回这些用例对应的清单项
      for (const it of checklist) {
        if (caseIds.includes(it.test_case_id)) itemIds.push(it.id)
      }
    }
    if (!itemIds.length) { ElMessage.warning('未能生成可下发的清单项'); return }
    const res = await enqueueExec(pid.value, runner.value, itemIds)
    const n = res?.run_ids?.length || itemIds.length
    ElMessage.success(`已下发 ${n} 条到 ${runner.value}${skipped ? `(跳过 ${skipped} 条不可下发)` : ''},执行机跑完会自动回写结果`)
  } catch { /* http 拦截器已提示 */ }
  finally { dispatching.value = false }
}

onMounted(async () => {
  try {
    myDevices.value = await listMyDevices()
    if (myDevices.value.length) runner.value = myDevices.value[0].runner_id
  } catch { myDevices.value = [] }
  projects.value = await listProjects()
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null
  if (!pid.value) { tasks.value = []; rows.value = []; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listCases({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      review_status: reviewStatus.value || undefined,
      category: category.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
  } finally { loading.value = false }
}

function fmtTime(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(0, 16)
}

async function onExecKindChange(row, val) {
  const prev = row.exec_kind || 'gui'
  if (val === prev) return
  row.exec_kind = val   // 乐观更新
  try {
    await setCaseExecKind(row.id, val)
    ElMessage.success(`已设为 ${val.toUpperCase()} 执行`)
  } catch {
    row.exec_kind = prev   // 失败回滚（http 拦截器已弹错）
  }
}

// 改采纳状态(乐观更新,失败回滚)
async function onReviewChange(row, val) {
  const prev = row.review_status || 'pending'
  if (val === prev) return
  row.review_status = val
  try {
    await reviewTestcase(row.id, val)
    ElMessage.success('采纳状态已更新')
  } catch { row.review_status = prev }
}

// ---- 编辑 ----
const edit = reactive({ visible: false, id: null, title: '', steps: '', expected: '', category: null, priority: null, saving: false })
function openEdit(row) {
  edit.id = row.id
  edit.title = row.title || ''
  edit.steps = row.steps || ''
  edit.expected = row.expected || ''
  edit.category = row.category || null
  edit.priority = (row.priority || '').toUpperCase() || null
  edit.visible = true
}
async function doEdit() {
  if (!edit.title.trim()) { ElMessage.warning('标题不能为空'); return }
  edit.saving = true
  try {
    await updateTestcase(edit.id, {
      title: edit.title.trim(), steps: edit.steps, expected: edit.expected,
      category: edit.category || '', priority: edit.priority || '',
    })
    edit.visible = false
    ElMessage.success('已保存')
    await load()
  } catch { /* 已提示 */ }
  finally { edit.saving = false }
}

// ---- 详情 ----
const detail = reactive({ visible: false, row: null })
function openDetail(row) { detail.row = row; detail.visible = true }
function prettyScript(s) {
  if (!s) return '(无 script,该用例由 claude 兜底执行或非结构化)'
  try { return JSON.stringify(typeof s === 'string' ? JSON.parse(s) : s, null, 2) } catch { return String(s) }
}

// ---- 删除 ----
async function onDelete(row) {
  try {
    await ElMessageBox.confirm(`删除用例「${row.title}」?其验收清单项会一并清理(执行历史保留)。`, '删除用例', { type: 'warning' })
  } catch { return }
  try { await deleteTestcase(row.id); ElMessage.success('已删除'); await load() } catch { /* 已提示 */ }
}

// ---- 批量 ----
async function bulkReview(statusVal) {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  try {
    for (const id of ids) await reviewTestcase(id, statusVal)
    ElMessage.success(`已批量${statusVal === 'adopted' ? '采纳' : '更新'} ${ids.length} 条`)
    await load()
  } catch { /* 已提示 */ }
}
async function bulkDelete() {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(`删除选中的 ${ids.length} 条用例?其验收清单项会一并清理。`, '批量删除', { type: 'warning' })
  } catch { return }
  try {
    for (const id of ids) await deleteTestcase(id)
    ElMessage.success(`已删除 ${ids.length} 条`)
    await load()
  } catch { /* 已提示 */ }
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.dispatch-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; padding: 8px 12px; background: #f3f8f6; border: 1px solid #d6e9e2; border-radius: 6px; }
.sel-info { font-weight: 600; color: #00926e; }
.sel-hint { color: #90a4ae; font-size: 12px; }
.detail { font-size: 13px; color: #334; }
.detail .d-row { margin: 8px 0 2px; }
.detail .d-k { display: inline-block; min-width: 72px; color: #90a4ae; }
.detail .d-pre { background: #f5f7fa; border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 12px; max-height: 220px; overflow: auto; }
</style>
