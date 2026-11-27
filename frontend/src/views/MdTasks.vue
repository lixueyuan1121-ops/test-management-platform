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
          <template #default="{ row }">{{ row.verdict ? row.findings.length : '—' }}</template>
        </el-table-column>
        <el-table-column label="北京时间" width="160">
          <template #default="{ row }">{{ formatBeijingTime(row.finished_at || row.started_at || row.created_at) }}</template>
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
          <el-select v-model="form.runners" multiple filterable allow-create default-first-option style="width:220px" placeholder="选择执行机，可多选">
            <el-option v-for="d in devices" :key="d.id" :label="`${d.name}（${d.runner_id}）`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境分组" required>
          <el-select v-model="form.env_group" placeholder="选择环境分组" style="width:220px">
            <el-option v-for="group in envGroups" :key="group" :label="group" :value="group" />
          </el-select>
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
          <template v-if="detail.retest">
            <span>复验工单 {{ detail.retest.external_ref }}</span>
            <el-button v-if="detail.retest.source_run_id" link type="primary" @click="openDetail({ id: detail.retest.source_run_id })">原提测任务 #{{ detail.retest.source_run_id }}</el-button>
          </template>
        </div>
        <el-alert v-if="detail.reason" :title="detail.reason" type="info" :closable="false" class="block" />

        <h3 v-if="detail.verdict">{{ detail.retest ? '复验发现' : '缺陷候选' }}（{{ detail.findings.length }}）</h3>
        <template v-if="detail.findings?.length">
          <el-table :data="detail.findings" size="small">
            <el-table-column type="index" width="50" />
            <el-table-column prop="title" label="标题" min-width="180" />
            <el-table-column prop="severity" label="严重度" width="80" />
            <el-table-column v-if="detail.evidence_summary || detail.evidence?.length" label="随缺陷提交的附件" min-width="185">
              <template #default="{ row, $index }">
                <div v-for="file in row.evidence" :key="file.id">
                  <el-button link type="primary" @click="downloadEvidence(file)">{{ file.name }}</el-button>
                  <span v-if="row.external_ref" class="hint" :title="file.message">{{ attachmentState[file.state] }}</span>
                </div>
                <span v-if="!row.evidence?.length" class="hint">暂无关联附件</span>
                <el-button v-if="row.evidence_target && row.evidence?.some(f => f.state !== 'sent')"
                  link type="primary" :disabled="reporting" @click="submitReports({ indexes: [$index], receipt_action: 'attachments' })">补传附件</el-button>
              </template>
            </el-table-column>
            <el-table-column v-if="!detail.retest" label="极库云" width="180">
              <template #default="{ row, $index }">
                <template v-if="row.external_ref">
                  <a v-if="caseLink(row)" :href="caseLink(row)" target="_blank" rel="noopener noreferrer">{{ row.external_ref }}</a>
                  <span v-else>{{ row.external_ref }}</span>
                  <div v-if="row.report_receipt?.skill" class="hint">极库云提 Bug 技能 · {{ row.report_receipt.priority }}</div>
                  <div v-if="row.report_receipt?.assignee" class="hint">执行人 {{ row.report_receipt.assignee }}</div>
                  <div v-for="run in row.retests" :key="run.id">
                    <el-button link type="primary" @click="openDetail(run)">复验 #{{ run.id }} · {{ retestStatus[run.status] || run.status }}</el-button>
                    <span v-if="run.client_version" class="hint">{{ run.client_version }}</span>
                  </div>
                  <el-button v-if="['admin', 'member'].includes(auth.roleIn(detail.project_id))"
                    link type="primary" :disabled="['pending', 'running'].includes(row.retests?.[0]?.status)" @click="openRetest(row)">下发复验</el-button>
                </template>
                <template v-else-if="row.report_state">
                  <span>{{ row.report_state === 'sending' ? '上报中' : '回执待核对' }}</span>
                  <el-button link type="primary" :disabled="reporting" @click="submitReports({ indexes: [$index], receipt_action: 'check' })">核对回执</el-button>
                </template>
                <el-button v-else link type="primary" :disabled="reporting || !detail.project_id" @click="openAssignment({ indexes: [$index] })">上报缺陷</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!detail.retest" class="bug-actions">
            <el-button size="small" type="primary" :loading="reporting" :disabled="!detail.project_id || !detail.findings.some(f => !f.external_ref && !f.report_state)" @click="openAssignment({})">上报未报的到极库云</el-button>
            <span class="hint">只有你确认是产品缺陷的才上报；环境/偶发问题点「取消」，别污染缺陷库</span>
          </div>
        </template>

        <template v-if="detail.evidence_summary || detail.evidence?.length">
          <h3>执行附件（{{ detail.evidence?.length || 0 }}<template v-if="detail.evidence_summary"> / {{ detail.evidence_summary.expected }}</template>）</h3>
          <div v-for="file in detail.evidence" :key="file.id">
            <el-button link type="primary" @click="downloadEvidence(file)">{{ file.name }}</el-button>
            <span class="hint">{{ file.case || '任务级' }} · {{ Math.ceil(file.size / 1024) }} KiB</span>
          </div>
          <div v-for="(note, index) in detail.evidence_summary?.notes" :key="index" class="hint">{{ note }}</div>
        </template>
        <h3>执行结果</h3>
        <pre class="md">{{ detail.result_md || '（执行机未返回结果文档）' }}</pre>

        <h3>环境校验</h3>
        <pre class="md">{{ detail.env_check ? JSON.stringify(detail.env_check, null, 2) : '—' }}</pre>

        <h3>任务文档</h3>
        <pre class="md">{{ detail.content }}</pre>
      </div>
    </el-drawer>
    <el-dialog v-model="retestVisible" title="下发缺陷复验" width="480px" :close-on-click-modal="!retesting" :close-on-press-escape="!retesting" :show-close="!retesting">
      <p>{{ retestForm.ref }} · {{ retestForm.title }}</p>
      <el-form label-position="top" :disabled="retesting">
        <el-form-item label="执行机" required>
          <el-select v-model="retestForm.runner" filterable allow-create default-first-option placeholder="选择执行机或输入执行机 ID">
            <el-option v-if="detail?.runner && !devices.some(d => d.runner_id === detail.runner)" :label="detail.runner" :value="detail.runner" />
            <el-option v-for="d in devices" :key="d.id" :label="`${d.name}（${d.runner_id}）`" :value="d.runner_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境分组" required>
          <el-select v-model="retestForm.env_group" placeholder="选择环境分组">
            <el-option v-if="detail?.env_group && !envGroups.includes(detail.env_group)" :label="detail.env_group" :value="detail.env_group" />
            <el-option v-for="group in envGroups" :key="group" :label="group" :value="group" />
          </el-select>
        </el-form-item>
        <el-form-item label="修复版本"><el-input v-model="retestForm.version" maxlength="64" placeholder="留空验证当前安装版本" /></el-form-item>
      </el-form>
      <div v-if="retestForm.error" role="alert">{{ retestForm.error }}</div>
      <template #footer>
        <el-button :disabled="retesting" @click="retestVisible = false">取消</el-button>
        <el-button type="primary" :loading="retesting" @click="submitRetest">确认下发</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="reviewVisible" title="核对极库云回执" width="560px">
      <template v-if="receipt">
        <p>任务 #{{ receipt.runId }} · {{ receipt.title }}</p>
        <template v-if="receipt.matches.length">
          <p>{{ receipt.msg || '请核对相关缺陷，选择要关联的工单。' }}</p>
          <el-select v-model="selectedMatter" placeholder="选择工单" style="width:100%">
            <el-option v-for="m in receipt.matches" :key="m.id" :value="m.id" :label="['#' + m.id, m.title, m.status, m.created_at].filter(Boolean).join(' · ')" />
          </el-select>
          <p v-if="selectedMatter">{{ receipt.matches.find(m => m.id === selectedMatter)?.description }}</p>
        </template>
        <p v-else>当前项目中未查到同标题缺陷。确认极库云中没有对应工单后，可以重新上报。</p>
      </template>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button :disabled="reporting" @click="resolveReceipt('retry')">重新建单</el-button>
        <el-button v-if="receipt?.matches.length" type="primary" :disabled="reporting || !selectedMatter" @click="resolveReceipt('link')">关联所选工单{{ detail?.findings[receipt.index]?.evidence?.length ? '并提交附件' : '' }}</el-button>
      </template>
    </el-dialog>
    <el-dialog :model-value="!!assignment" @update:model-value="visible => { if (!visible) assignment = null }"
      :title="assignment?.payload.receipt_action === 'retry' ? '确认重新建单' : '确认上报'" width="480px">
      <template v-if="assignment">
        <p v-if="assignment.payload.receipt_action === 'retry'">请确认极库云中没有可关联的工单，再重新建单。</p>
        <p v-else-if="assignment.payload.indexes">{{ detail?.findings[assignment.payload.indexes[0]]?.title }}</p>
        <p v-else>本次新建的缺陷将指派给同一位执行人。</p>
        <el-form label-position="top">
          <el-form-item label="执行人" required>
            <el-select v-model="assignment.email" filterable :loading="assignment.loading"
              placeholder="搜索姓名或邮箱" no-data-text="该项目暂无可指派的执行人">
              <el-option v-for="m in assignment.members" :key="m.email" :label="m.name + '（' + m.email + '）'" :value="m.email" />
            </el-select>
          </el-form-item>
        </el-form>
        <div v-if="assignment.error" role="alert">{{ assignment.error }}
          <el-button link type="primary" @click="loadExecutors(assignment)">重新加载</el-button>
        </div>
      </template>
      <template #footer>
        <el-button @click="assignment = null">取消</el-button>
        <el-button type="primary" :disabled="!assignment?.email || assignment.loading || reporting" @click="confirmAssignment">确认上报</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import WorkspacePage from '@/components/WorkspacePage.vue'
import { Refresh } from '@element-plus/icons-vue'
import '@/styles/workspace-overlays.css'
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { listMyDevices, listMdRuns, getMdRun, getMdReportExecutors, createMdRuns, createMdRetest, cancelMdRun, reportMdRunBugs, downloadMdEvidence } from '@/api'

const app = useAppStore()
const auth = useAuthStore()
const projects = ref([])
const pid = ref(null)
const devices = ref([])
const runs = ref([])
const loading = ref(false)
const statuses = ['pending', 'running', 'passed', 'failed', 'blocked', 'canceled']

const dispatchVisible = ref(false)
const submitting = ref(false)
const fileEl = ref(null)
const envGroups = ['online', 'pre', 'test']
const form = reactive({ title: '', req_no: '', runners: [], env_group: '', client_version: '', content: '' })

const detailVisible = ref(false)
const detail = ref(null)
const reporting = ref(false)
const assignment = ref(null)
const retestVisible = ref(false)
const retesting = ref(false)
const retestForm = ref({})
const retestStatus = { pending: '待执行', running: '执行中', passed: '通过', failed: '失败', blocked: '阻塞', canceled: '已取消' }
const reviewVisible = ref(false)
const receipt = ref(null)
const selectedMatter = ref(null)

const statusType = (s) => ({ pending: 'info', running: 'warning', passed: 'success', failed: 'danger', blocked: 'warning', canceled: 'info' }[s] || 'info')
const beijingTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
})
function formatBeijingTime(iso) {
  if (!iso) return '—'
  // 历史提测记录的 ISO 字符串省略了 UTC 时区标记。
  const timestamp = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(timestamp) ? '—' : beijingTime.format(timestamp)
}

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
  Object.assign(form, { title: '', req_no: '', runners: devices.value[0]?.runner_id ? [devices.value[0].runner_id] : [], env_group: '', client_version: '', content: '' })
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
      if (kv[1] === 'env_group' && !form.env_group) {
        const group = { 'work-test': 'test', 'work-staging': 'pre' }[v] || v
        if (envGroups.includes(group)) form.env_group = group
      }
      if (kv[1] === 'client_version' && !form.client_version) form.client_version = v
      if (kv[1] === 'req' && !form.req_no) form.req_no = v
      if (kv[1] === 'title' && !form.title) form.title = v
    }
  }
}

async function submit() {
  if (!form.title.trim()) return ElMessage.warning('请填写标题')
  if (!form.runners.length) return ElMessage.warning('请选择执行机')
  if (!envGroups.includes(form.env_group)) return ElMessage.warning('请选择环境分组')
  if (!form.content.trim()) return ElMessage.warning('请选择或粘贴提测文档')
  submitting.value = true
  try {
    const created = await createMdRuns({
      project_id: pid.value || null,
      title: form.title.trim(),
      req_no: form.req_no.trim() || null,
      runners: form.runners,
      env_group: form.env_group.trim(),
      client_version: form.client_version.trim() || null,
      content: form.content,
      source: 'page',
    })
    dispatchVisible.value = false
    ElMessage.success(`已向 ${created.length} 台执行机下发任务`)
    await loadRuns()
  } finally { submitting.value = false }
}

async function openDetail(row) {
  detail.value = await getMdRun(row.id)
  detailVisible.value = true
}

function openRetest(row) {
  retestForm.value = { runId: detail.value.id, ref: row.external_ref, title: row.title, previous: row.retests?.[0]?.id || 0,
    runner: detail.value.runner, env_group: detail.value.env_group,
    version: row.retests?.[0]?.client_version ?? detail.value.client_version ?? '', error: '' }
  retestVisible.value = true
}

async function submitRetest() {
  if (retesting.value) return
  const form = retestForm.value
  const runner = form.runner?.trim()
  if (!runner || runner.length > 64) return ElMessage.warning('请选择执行机或输入不超过 64 个字符的执行机 ID')
  if (!form.env_group) return ElMessage.warning('请选择环境分组')
  retesting.value = true
  try {
    const result = await createMdRetest(form.runId, {
      external_ref: form.ref, previous_run_id: form.previous, client_version: form.version.trim(), runner,
      // 历史自定义环境由服务端从原任务继承。
      env_group: envGroups.includes(form.env_group) ? form.env_group : undefined,
    })
    retestVisible.value = false
    if (result.created) ElMessage.success(`已下发复验任务 #${result.run_id}`)
    else ElMessage.info(`已有复验任务 #${result.run_id}，请查看最新结果`)
    const updated = await getMdRun(form.runId, { silent: true }).catch(() => null)
    if (updated && detail.value?.id === form.runId) detail.value = updated
    await loadRuns().catch(() => {})
  } catch (error) {
    form.error = error.response?.data?.msg || '未收到下发回执，可重试以核对复验任务'
  } finally { retesting.value = false }
}

async function onCancel(row) {
  await ElMessageBox.confirm(`取消任务 #${row.id}？运行中的任务由执行机在下次心跳后自行收尾（还原环境）。`, '确认', { type: 'warning' })
  await cancelMdRun(row.id)
  ElMessage.success('已取消')
  await loadRuns()
}

const attachmentState = { pending: '待提交', sending: '上传中', sent: '已附到工单', failed: '上传失败', uncertain: '回执待核对' }

async function downloadEvidence(file) {
  const blob = await downloadMdEvidence(detail.value.id, file.id)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}

function caseLink(row) {
  const receipt = row.report_receipt
  const id = /^geelib#([1-9]\d*)$/.exec(row.external_ref)?.[1]
  if (!id || !Number.isSafeInteger(receipt?.sub_id) || !Number.isSafeInteger(receipt?.type_id)) return ''
  return `https://geelib.qihoo.net/geelib/project/matterType/${receipt.type_id}?subId=${receipt.sub_id}&matterId=${id}&typeId=${receipt.type_id}`
}

function openAssignment(payload, preferred = '') {
  if (reporting.value || !detail.value) return
  assignment.value = { runId: detail.value.id, payload, preferred, members: [], email: '', loading: true, error: '' }
  loadExecutors(assignment.value)
}

async function loadExecutors(form) {
  form.loading = true
  form.error = ''
  try {
    const { members, default_email } = await getMdReportExecutors(form.runId)
    form.members = members
    const email = (form.preferred || default_email || '').toLowerCase()
    form.email = members.find(m => m.email.toLowerCase() === email)?.email || ''
    if (!members.length) form.error = '该极库云项目暂无可指派的执行人'
  } catch (error) {
    form.error = error.response?.data?.msg || '无法加载极库云项目执行人'
  } finally { form.loading = false }
}

function confirmAssignment() {
  const form = assignment.value
  if (!form?.email || form.loading || form.runId !== detail.value?.id || !detailVisible.value) return
  assignment.value = null
  return submitReports({ ...form.payload, executor_email: form.email })
}

async function resolveReceipt(action) {
  const item = receipt.value
  if (!item || item.runId !== detail.value?.id) return
  const payload = { indexes: [item.index], receipt_action: action, review_key: item.review_key }
  if (action === 'retry') return openAssignment(payload, item.executor)
  await submitReports({ ...payload, matter_id: selectedMatter.value })
}

async function submitReports(payload) {
  if (reporting.value || !detail.value) return
  const runId = detail.value.id
  reporting.value = true
  try {
    const res = await reportMdRunBugs(runId, payload)
    if (detail.value?.id !== runId || !detailVisible.value) return
    detail.value = res.run
    const review = res.results.find(r => r.status === 'review')
    if (review) {
      const finding = res.run.findings[review.index]
      receipt.value = { ...review, runId, title: finding?.title, executor: payload.executor_email || finding?.report_executor }
      selectedMatter.value = review.matches.length === 1 ? review.matches[0].id : null
      reviewVisible.value = true
    } else {
      reviewVisible.value = false
    }
    if (res.reported) ElMessage.success(`已上报 ${res.reported} 条`)
    const files = res.results.reduce((n, r) => n + (r.attachment_count || 0), 0)
    if (files) {
      const sent = res.results.reduce((n, r) => n + (r.attachments_sent || 0), 0)
      if (sent === files) ElMessage.success(`已附到工单 ${sent} 份附件`)
      else ElMessage.warning(`工单已关联，附件已提交 ${sent}/${files}，可在候选行补传`)
    }
    const linked = res.results.find(r => r.status === 'linked')
    if (linked) ElMessage.success(`已关联 ${linked.external_ref}`)
    const pending = res.results.find(r => ['sending', 'uncertain', 'changed'].includes(r.status))
    if (pending) ElMessage.info(pending.msg)
    const failed = res.results.filter(r => r.status === 'failed' || (!r.ok && !r.status))
    if (failed.length) ElMessage.warning(`${failed.length} 条上报失败：${failed[0].msg}`)
    const checkError = res.results.find(r => r.status === 'check_failed')
    if (checkError) ElMessage.warning(checkError.msg)
    await loadRuns().catch(() => {}) // 列表刷新失败由请求拦截器提示，工单回执已显示。
  } catch (error) {
    if (detail.value?.id !== runId || !detailVisible.value) return
    if (error.response && error.response.status < 500) {
      ElMessage.error(error.response.data?.msg || '上报请求未完成，请检查授权和候选状态')
      return
    }
    // 请求可能在远端建单后中断，只读取已保存的结果。
    const latest = await getMdRun(runId, { silent: true }).catch(() => null)
    if (detail.value?.id !== runId || !detailVisible.value) return
    if (latest) {
      detail.value = latest
      reviewVisible.value = false
      ElMessage.warning('上报中断，已刷新工单状态。回执待确认的缺陷请核对回执。')
    } else {
      ElMessage.warning('上报中断，暂时无法读取工单状态。请稍后刷新任务详情。')
    }
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
