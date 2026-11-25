<template>
  <div class="md-tasks functional-workspace">
    <WorkspacePage title="提测执行">
      <template #actions>
        <el-button size="small" :icon="Refresh" aria-label="刷新" :loading="loading" @click="loadRuns" />
        <el-button type="primary" @click="openDispatch">下发提测文档</el-button>
      </template>
      <template #filters>
        <el-select v-model="pid" style="width:220px" @change="remember">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable size="small" style="width:130px" @change="loadRuns">
          <el-option v-for="s in statuses" :key="s" :label="s" :value="s" />
        </el-select>
      </template>

      <el-table :data="runs" v-loading="loading" size="small">
        <el-table-column prop="id" label="#" width="56" />
        <el-table-column prop="req_no" label="提测号" width="110" show-overflow-tooltip />
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="env_group" label="环境分组" width="110" show-overflow-tooltip />
        <el-table-column prop="client_version" label="客户端" width="96" />
        <el-table-column prop="runner" label="执行机" width="110" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="缺陷" width="60">
          <template #default="{ row }">{{ row.findings?.length || '—' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.finished_at || row.started_at || row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">查看</el-button>
            <el-button v-if="row.can_cancel" link type="warning" @click="onCancel(row)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </WorkspacePage>

    <!-- 下发 -->
    <el-dialog v-model="dispatchVisible" title="下发提测文档" width="720px" :close-on-click-modal="!submitting">
      <el-form :model="form" label-width="92px">
        <el-form-item label="标题" required><el-input v-model="form.title" placeholder="如：工作台消息列表改版" /></el-form-item>
        <el-form-item label="提测号"><el-input v-model="form.req_no" placeholder="可选，上报缺陷时用作标题前缀" style="width:220px" /></el-form-item>
        <el-form-item label="执行机" required>
          <el-select v-model="form.runner" filterable allow-create default-first-option style="width:220px" placeholder="选 / 填 runner_id">
            <el-option v-for="d in devices" :key="d.id" :label="`${d.name}（${d.runner_id}）`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境分组" required>
          <el-input v-model="form.env_group" placeholder="执行机本地 hosts 分组名，如 work-test" style="width:220px" />
          <span class="hint">只填名字；真实 hosts 内容在执行机上，平台不存</span>
        </el-form-item>
        <el-form-item label="客户端版本"><el-input v-model="form.client_version" placeholder="可选，如 3.2.1.88" style="width:160px" /></el-form-item>
        <el-form-item label="文档" required>
          <div class="doc-row">
            <input ref="fileEl" type="file" accept=".md,.markdown,.txt" class="file" @change="onFile" />
            <el-button size="small" @click="fileEl?.click()">选择 md</el-button>
            <span class="hint">{{ form.content ? `${form.content.length} 字符` : '或直接粘贴到下方' }}</span>
          </div>
          <el-input v-model="form.content" type="textarea" :rows="12" placeholder="提测文档 / 测试用例（md）。执行机上的 agent 会按这份文档操作客户端。" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="submitting" @click="dispatchVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">下发</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-drawer v-model="detailVisible" :title="detail?.title || '详情'" size="62%" destroy-on-close>
      <div v-if="detail" class="detail">
        <div class="meta">
          <el-tag size="small" :type="statusType(detail.status)">{{ detail.status }}</el-tag>
          <span>提测号 {{ detail.req_no || '—' }}</span>
          <span>环境 {{ detail.env_group }}</span>
          <span>客户端 {{ detail.client_version || '—' }}</span>
          <span>执行机 {{ detail.runner }}</span>
          <span v-if="detail.duration_ms != null">{{ Math.round(detail.duration_ms / 1000) }}s</span>
        </div>
        <el-alert v-if="detail.reason" :title="detail.reason" type="info" :closable="false" class="block" />

        <template v-if="detail.findings?.length">
          <h3>缺陷候选（{{ detail.findings.length }}）</h3>
          <el-table :data="detail.findings" size="small">
            <el-table-column type="index" width="50" />
            <el-table-column prop="title" label="标题" min-width="180" />
            <el-table-column prop="severity" label="严重度" width="80" />
            <el-table-column label="极库云" width="130">
              <template #default="{ row }">
                <span v-if="row.external_ref">{{ row.external_ref }}</span>
                <span v-else class="hint">未上报</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="bug-actions">
            <el-button size="small" type="primary" :loading="reporting" :disabled="!pid" @click="reportBugs(false)">上报未报的到极库云</el-button>
            <span class="hint">只有你确认是产品缺陷的才上报；环境/偶发问题点「取消」，别污染缺陷库</span>
          </div>
        </template>

        <h3>执行结果</h3>
        <pre class="md">{{ detail.result_md || '（执行机未返回结果文档）' }}</pre>

        <h3>环境校验</h3>
        <pre class="md">{{ detail.env_check ? JSON.stringify(detail.env_check, null, 2) : '—' }}</pre>

        <h3>任务文档</h3>
        <pre class="md">{{ detail.content }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'
import '@/styles/workspace-overlays.css'
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { listMyDevices, listMdRuns, getMdRun, createMdRun, cancelMdRun, reportMdRunBugs } from '@/api'

const app = useAppStore()
const projects = ref([])
const pid = ref(null)
const devices = ref([])
const runs = ref([])
const loading = ref(false)
const statuses = ['pending', 'running', 'passed', 'failed', 'blocked', 'canceled']

const dispatchVisible = ref(false)
const submitting = ref(false)
const fileEl = ref(null)
const form = reactive({ title: '', req_no: '', runner: '', env_group: '', client_version: '', content: '' })

const detailVisible = ref(false)
const detail = ref(null)
const reporting = ref(false)

const statusType = (s) => ({ pending: 'info', running: 'warning', passed: 'success', failed: 'danger', blocked: 'warning', canceled: 'info' }[s] || 'info')
function fmtTime(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false }) }

function remember() { if (pid.value) setLastProjectId(pid.value) }

async function loadDevices() { try { devices.value = await listMyDevices() } catch { devices.value = [] } }
async function loadRuns() {
  loading.value = true
  try {
    const params = {}
    if (pid.value) params.project_id = pid.value
    if (statusFilter.value) params.status = statusFilter.value
    runs.value = await listMdRuns(params)
  } finally { loading.value = false }
}
const statusFilter = ref('')

function openDispatch() {
  Object.assign(form, { title: '', req_no: '', runner: devices.value[0]?.runner_id || '', env_group: '', client_version: '', content: '' })
  dispatchVisible.value = true
}

async function onFile(e) {
  const f = e.target.files?.[0]
  if (!f) return
  form.content = await f.text()
  if (!form.title) form.title = f.name.replace(/\.(md|markdown|txt)$/i, '')
  // 文档里若有 frontmatter，顺手把 env_group / 版本带出来，省一次手填（失败就算了）
  const m = form.content.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (m) {
    for (const line of m[1].split(/\r?\n/)) {
      const kv = line.match(/^\s*(env_group|client_version|req|title)\s*:\s*(.+?)\s*(#.*)?$/)
      if (!kv) continue
      const v = kv[2].replace(/^["']|["']$/g, '')
      if (kv[1] === 'env_group' && !form.env_group) form.env_group = v
      if (kv[1] === 'client_version' && !form.client_version) form.client_version = v
      if (kv[1] === 'req' && !form.req_no) form.req_no = v
      if (kv[1] === 'title' && !form.title) form.title = v
    }
  }
}

async function submit() {
  if (!form.title.trim()) return ElMessage.warning('请填写标题')
  if (!form.runner.trim()) return ElMessage.warning('请选择执行机')
  if (!form.env_group.trim()) return ElMessage.warning('请填写环境分组名')
  if (!form.content.trim()) return ElMessage.warning('请选择或粘贴提测文档')
  submitting.value = true
  try {
    await createMdRun({
      project_id: pid.value || null,
      title: form.title.trim(),
      req_no: form.req_no.trim() || null,
      runner: form.runner.trim(),
      env_group: form.env_group.trim(),
      client_version: form.client_version.trim() || null,
      content: form.content,
      source: 'page',
    })
    dispatchVisible.value = false
    ElMessage.success('已下发，执行机下一轮轮询会取走')
    await loadRuns()
  } finally { submitting.value = false }
}

async function openDetail(row) {
  detail.value = await getMdRun(row.id)
  detailVisible.value = true
}

async function onCancel(row) {
  await ElMessageBox.confirm(`取消任务 #${row.id}？运行中的任务由执行机在下次心跳后自行收尾（还原环境）。`, '确认', { type: 'warning' })
  await cancelMdRun(row.id)
  ElMessage.success('已取消')
  await loadRuns()
}

async function reportBugs() {
  if (!detail.value) return
  await ElMessageBox.confirm('把尚未上报的缺陷候选上报到极库云？请先确认它们都是产品缺陷。', '确认上报', { type: 'warning' })
  reporting.value = true
  try {
    const res = await reportMdRunBugs(detail.value.id, {})
    detail.value = res.run
    const failed = res.results.filter((r) => !r.ok)
    if (res.reported) ElMessage.success(`已上报 ${res.reported} 条`)
    if (failed.length) ElMessage.warning(`${failed.length} 条上报失败：${failed[0].msg || ''}`)
    await loadRuns()
  } finally { reporting.value = false }
}

onMounted(async () => {
  projects.value = await app.fetchProjects()
  pid.value = pickDefaultProjectId(projects.value)
  await Promise.all([loadDevices(), loadRuns()])
})
</script>

<style scoped>
.md-tasks :deep(.el-dialog .el-form-item__content > .el-input),
.md-tasks :deep(.el-dialog .el-form-item__content > .el-select) { width: 100% !important; }
.hint { font-size: 12px; color: var(--tech-muted); margin-left: 8px; }
.doc-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.doc-row .file { display: none; }
.meta { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 13px; margin-bottom: 12px; }
.block { margin-bottom: 12px; }
.bug-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0 16px; }
.md { max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: var(--tech-code-bg); padding: 10px; border-radius: 4px; font-size: 12px; line-height: 1.6; }
h3 { font-size: 14px; margin: 18px 0 8px; }
</style>
