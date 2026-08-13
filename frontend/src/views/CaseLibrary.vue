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
            <el-input
              v-model="keyword" placeholder="按测试点搜索" size="small" clearable style="width:180px"
              @keyup.enter="load" @clear="load"
            />
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="没有符合条件的用例">
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
            <el-select :model-value="row.exec_kind || 'gui'" size="small" style="width:90px"
                       @change="(v) => onExecKindChange(row, v)">
              <el-option v-for="k in EXEC_KINDS" :key="k.value" :label="k.label" :value="k.value" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="测试点" min-width="200" show-overflow-tooltip />
        <el-table-column label="步骤" min-width="200">
          <template #default="{ row }"><span class="multiline">{{ row.steps || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="预期结果" min-width="180">
          <template #default="{ row }"><span class="multiline">{{ row.expected || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="采纳状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="RV_TYPE[row.review_status] || 'info'" size="small">{{ RV_LABEL[row.review_status] || '待定' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关联任务" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.task_title || '—' }}</template>
        </el-table-column>
        <el-table-column label="生成时间" width="140">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listProjects, listTasks, listCases, setCaseExecKind } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

// 维度 / 优先级 → el-tag 配色（与 AITestGen 口径一致）
const CAT_TYPE = { 功能: 'primary', 边界: 'warning', 异常: 'danger', 兼容: 'info', 性能: 'success' }
const PRI_TYPE = { P0: 'danger', P1: 'warning', P2: 'primary', P3: 'info' }
const CATEGORIES = ['功能', '边界', '异常', '兼容', '性能']
// 采纳三态 → 配色/文案（采纳=success / 否决=danger / 待定=info）
const RV_TYPE = { adopted: 'success', rejected: 'danger', pending: 'info' }
const RV_LABEL = { adopted: '已采纳', rejected: '已否决', pending: '待定' }
// 自动化执行类型：gui(客户端 UI) / api(接口) / cli(命令行)。下发到 runner 时决定 Claude Code 怎么跑。
const EXEC_KINDS = [
  { value: 'gui', label: 'GUI' },
  { value: 'api', label: 'API' },
  { value: 'cli', label: 'CLI' },
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

onMounted(async () => {
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
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.multiline { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
</style>
