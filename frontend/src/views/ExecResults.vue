<template>
  <div class="exec-results">
    <el-card>
      <template #header>
        <div class="header">
          <span>执行结果</span>
          <div class="filters">
            <el-select v-model="pid" placeholder="选择项目" size="small" style="width:160px" @change="onProjectChange">
              <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="taskId" placeholder="任务" size="small" clearable filterable fit-input-width style="width:220px" @change="load">
              <el-option v-for="t in tasks" :key="t.id" :label="t.description || t.title" :value="t.id" :title="t.description || t.title" />
            </el-select>
            <el-select v-model="runner" placeholder="执行设备" size="small" clearable style="width:150px" @change="load">
              <el-option v-for="rn in runners" :key="rn" :label="rn" :value="rn" />
            </el-select>
            <el-select v-model="verdict" placeholder="结果" size="small" clearable style="width:100px" @change="load">
              <el-option label="通过" value="pass" />
              <el-option label="失败" value="fail" />
            </el-select>
            <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" size="small" border stripe empty-text="暂无执行记录">
        <el-table-column label="结果" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.verdict === 'pass' ? 'success' : (row.verdict === 'fail' ? 'danger' : 'info')" size="small">
              {{ row.verdict === 'pass' ? '通过' : (row.verdict === 'fail' ? '失败' : (STATUS_LABEL[row.status] || row.status)) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="66" align="center">
          <template #default="{ row }"><el-tag :type="KIND_TYPE[row.kind] || 'info'" size="small" effect="plain">{{ KIND_LABEL[row.kind] || row.kind }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="title" label="用例" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || `#${row.case_id ?? '—'}` }}</template>
        </el-table-column>
        <el-table-column prop="runner" label="设备" width="120" show-overflow-tooltip />
        <el-table-column label="原因/结论" min-width="240">
          <template #default="{ row }"><span class="reason">{{ row.reason || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="耗时" width="80" align="center">
          <template #default="{ row }">{{ row.duration_ms != null ? (row.duration_ms / 1000).toFixed(1) + 's' : '—' }}</template>
        </el-table-column>
        <el-table-column label="证据" width="70" align="center">
          <template #default="{ row }">
            <el-link v-if="row.evidence_url" type="primary" @click="showEvidence(row)">查看</el-link>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="执行时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.updated_at || row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div class="foot-hint">共 {{ rows.length }} 条(每次执行都留痕,不覆盖;同一用例多次复测按时间倒序)</div>
    </el-card>

    <el-dialog v-model="ev.visible" title="执行证据" width="480px">
      <p class="ev-path">{{ ev.path }}</p>
      <el-alert type="info" :closable="false" show-icon>证据为执行机本地路径(截图等),在对应设备上查看。</el-alert>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { listProjects, listTasks, listExecHistory } from '@/api'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'

const KIND_TYPE = { gui: 'success', api: 'primary', cli: 'warning', e2e: 'danger', manual: 'info' }
const KIND_LABEL = { gui: 'GUI', api: 'API', cli: 'CLI', e2e: 'E2E', manual: '人工' }
const STATUS_LABEL = { pending: '待执行', running: '执行中', passed: '通过', failed: '失败' }

const projects = ref([])
const pid = ref(null)
const tasks = ref([])
const taskId = ref(null)
const runner = ref(null)
const verdict = ref(null)
const rows = ref([])
const loading = ref(false)
const ev = ref({ visible: false, path: '' })

// 设备筛选项:从当前结果集里已出现的 runner 去重(无需额外接口)
const runners = computed(() => [...new Set(rows.value.map((r) => r.runner).filter(Boolean))])

onMounted(async () => {
  projects.value = await listProjects()
  if (projects.value.length) {
    pid.value = pickDefaultProjectId(projects.value)
    await onProjectChange()
  }
})

async function onProjectChange() {
  taskId.value = null; runner.value = null; verdict.value = null
  if (!pid.value) { tasks.value = []; rows.value = []; return }
  setLastProjectId(pid.value)
  tasks.value = await listTasks({ project_id: pid.value })
  await load()
}

async function load() {
  if (!pid.value) return
  loading.value = true
  try {
    rows.value = await listExecHistory({
      project_id: pid.value,
      task_id: taskId.value || undefined,
      runner: runner.value || undefined,
      verdict: verdict.value || undefined,
    })
  } finally { loading.value = false }
}

function showEvidence(row) { ev.value = { visible: true, path: row.evidence_url } }
function fmtTime(s) { return s ? String(s).replace('T', ' ').slice(0, 16) : '—' }
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.reason { white-space: pre-line; color: #5a6b7b; font-size: 13px; }
.foot-hint { margin-top: 8px; color: #90a4ae; font-size: 12px; }
.ev-path { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; margin-bottom: 10px; }
</style>
