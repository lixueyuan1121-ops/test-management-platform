<template>
  <div class="functional-workspace imports">
    <WorkspacePage title="导入任务">
      <template #actions>
        <el-select v-model="pid" placeholder="选择项目" style="width:200px" @change="changeProject">
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button :loading="loading" @click="refresh">刷新</el-button>
      </template>
      <template #filters>
        <el-select v-model="filter" clearable placeholder="全部状态" style="width:190px" @change="resetPage">
          <el-option v-for="s in ['pending','needs_confirmation','done','failed']" :key="s" :label="labels[s]" :value="s" />
        </el-select>
        <span>提交后由后台处理；疑似重复由项目管理员集中确认。</span>
      </template>
      <el-alert v-if="loadError" title="加载失败，请点击刷新重试" type="error" :closable="false" />
      <el-table :data="rows" v-loading="loading" border empty-text="暂无导入任务">
        <el-table-column prop="job_id" label="任务编号" width="100" />
        <el-table-column prop="requirement" label="需求" min-width="220" show-overflow-tooltip />
        <el-table-column prop="user_id" label="提交人 ID" width="100" />
        <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag :type="typeOf(row.status)">{{ labels[row.status] }}</el-tag></template></el-table-column>
        <el-table-column label="处理进度" min-width="280"><template #default="{ row }">新增 {{ row.created_cases }} · 复用 {{ row.reused_cases }} · 待处理 {{ row.counts.pending }} · 待确认 {{ row.counts.needs_confirmation }} · 失败 {{ row.counts.failed }}</template></el-table-column>
        <el-table-column prop="created_at" label="提交时间" width="190" />
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click="openJob(row.job_id)">查看详情</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next, jumper" @current-change="load" />
    </WorkspacePage>
    <el-drawer v-model="drawer" title="导入任务详情" size="75%" @closed="closeJob">
      <div v-loading="detailLoading">
        <template v-if="job">
          <p>{{ job.requirement }} · 任务 #{{ job.job_id }}</p>
          <el-alert v-if="!job.can_manage" type="info" title="疑似重复确认与失败重试由项目管理员处理" :closable="false" />
          <el-button @click="openJob(job.job_id)">刷新任务</el-button>
          <el-table :data="job.items" border>
            <el-table-column prop="title" label="提交用例" min-width="200" />
            <el-table-column label="状态" width="130"><template #default="{ row }">{{ labels[row.status] }}</template></el-table-column>
            <el-table-column label="结果 / 原因" min-width="200"><template #default="{ row }">
              <template v-if="row.receipt">
                {{ row.receipt.disposition === 'created' ? '新增' : '复用' }}用例 #{{ row.receipt.case_id }} ·
                <router-link :to="`/exec-results?project_id=${pid}&batch_id=${row.receipt.batch_id}`">执行记录 #{{ row.receipt.run_id }}</router-link>
              </template>
              <span v-else>{{ row.error || (row.status === 'pending' ? '后台排队处理中，可离开本页' : '请对照场景确认') }}</span>
            </template></el-table-column>
            <el-table-column label="操作" width="160"><template #default="{ row }">
              <el-button link type="primary" @click="inspect(row)">查看 / 对比</el-button>
              <el-button v-if="job.can_manage && row.status === 'failed'" link type="warning" :disabled="saving" @click="retry(row)">重试</el-button>
            </template></el-table-column>
          </el-table>
        </template>
      </div>
    </el-drawer>
    <el-dialog v-model="dialog" title="场景对比与确认" width="min(1100px, 95vw)" :close-on-click-modal="false">
      <div v-if="selected" class="compare">
        <section><h3>本次提交：{{ selected.source.title }}</h3>
          <p><b>前置条件：</b>{{ selected.source.precondition || '无' }}</p>
          <p><b>操作步骤：</b>{{ selected.source.steps }}</p><p><b>预期结果：</b>{{ selected.source.expected }}</p>
          <details><summary>查看本次脚本与实测报告</summary><pre>{{ JSON.stringify({ script: selected.source.script, report: selected.source.report }, null, 2) }}</pre></details>
        </section>
        <el-alert v-if="selected.error" :title="selected.error" type="warning" :closable="false" />
        <section v-for="c in selected.plan?.candidates || []" :key="c.case_id" class="candidate">
          <h3>已有用例 #{{ c.case_id }}：{{ c.title }}</h3>
          <p><b>前置条件：</b>{{ c.precondition || '无' }}</p><p><b>操作步骤：</b>{{ c.steps }}</p><p><b>预期结果：</b>{{ c.expected }}</p>
          <span>{{ c.reason }}</span>
          <el-button v-if="canResolve" :type="choice === c.case_id ? 'primary' : 'default'" @click="choice = c.case_id">{{ choice === c.case_id ? '已选复用此用例' : '复用此用例' }}</el-button>
        </section>
        <template v-if="canResolve">
          <el-button v-if="canCreate" :type="choice === 'create' ? 'primary' : 'default'" @click="choice = 'create'">确认为不同场景，单独新建</el-button>
          <el-input v-model="reason" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="请说明复用或独立建例的依据（至少 5 个字符）" />
          <p>确认后由后台重新检查；候选变化时会重新进入待确认，不覆盖已有用例。</p>
        </template>
      </div>
      <template #footer><el-button @click="dialog = false">关闭</el-button><el-button v-if="canResolve" type="primary" :loading="saving" :disabled="choice == null || reason.trim().length < 5" @click="resolve">确认并交给后台</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import WorkspacePage from '@/components/WorkspacePage.vue'
import { useAppStore } from '@/store/app'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import http from '@/api/http'
const app = useAppStore(), route = useRoute(), router = useRouter()
const projects = ref([]), pid = ref(null), rows = ref([]), page = ref(1), total = ref(0), filter = ref('')
const loading = ref(false), loadError = ref(false), drawer = ref(false), detailLoading = ref(false), job = ref(null)
const dialog = ref(false), selected = ref(null), choice = ref(null), reason = ref(''), saving = ref(false)
let generation = 0, detailGeneration = 0
const labels = { pending: '后台处理中', needs_confirmation: '待确认', done: '已入库', failed: '处理失败', completed: '全部入库' }
const canResolve = computed(() => job.value?.can_manage && selected.value?.status === 'needs_confirmation')
const canCreate = computed(() => !(selected.value?.plan?.candidates || []).some(c => c.reason === '脚本与验收条件一致'))
const typeOf = s => ({ completed: 'success', done: 'success', needs_confirmation: 'warning', failed: 'danger' }[s] || 'info')
async function load() {
  const version = ++generation
  if (!pid.value) return
  loading.value = true; loadError.value = false
  try {
    const data = await http.get('/verified-imports/jobs', { params: { project_id: pid.value, page: page.value, status: filter.value } })
    if (version !== generation) return
    rows.value = data.items; total.value = data.total
    if (!rows.value.length && page.value > 1) { page.value = Math.max(1, Math.ceil(total.value / 20)); await load() }
  } catch { if (version === generation) { loadError.value = true; rows.value = [] } }
  finally { if (version === generation) loading.value = false }
}
function resetPage() { page.value = 1; load() }
function changeProject() {
  ++detailGeneration; job.value = null; drawer.value = false; dialog.value = false; rows.value = []; total.value = 0
  setLastProjectId(pid.value); router.replace({ query: { project_id: pid.value } }); resetPage()
}
async function openJob(id) {
  const version = ++detailGeneration; drawer.value = true; detailLoading.value = true; job.value = null
  try {
    const data = await http.get(`/verified-imports/jobs/${id}`)
    if (version !== detailGeneration || data.project_id !== pid.value) return
    job.value = data
    router.replace({ query: { project_id: pid.value, job_id: id } })
  } catch { if (version === detailGeneration) drawer.value = false }
  finally { if (version === detailGeneration) detailLoading.value = false }
}
function closeJob() { ++detailGeneration; job.value = null; dialog.value = false; router.replace({ query: { project_id: pid.value } }) }
async function refresh() { await load(); if (drawer.value && job.value) await openJob(job.value.job_id) }
async function inspect(row) {
  const id = job.value.job_id, version = detailGeneration
  try {
    const data = await http.get(`/verified-imports/jobs/${id}/items/${row.id}`)
    if (version !== detailGeneration) return
    selected.value = data; choice.value = null; reason.value = ''; dialog.value = true
  } catch { /* shared HTTP handler displays error */ }
}
async function resolve() {
  saving.value = true
  const id = job.value.job_id
  try {
    await http.post(`/verified-imports/jobs/${id}/items/${selected.value.id}/resolve`, {
      action: choice.value === 'create' ? 'create' : 'reuse', case_id: choice.value === 'create' ? null : choice.value,
      token: selected.value.plan.confirmation_token, reason: reason.value.trim(),
    })
    dialog.value = false; ElMessage.success('已提交确认，后台继续处理'); await openJob(id); await load()
  } catch { await inspect(selected.value) }
  finally { saving.value = false }
}
async function retry(row) {
  const id = job.value.job_id; saving.value = true
  try { await http.post(`/verified-imports/jobs/${id}/items/${row.id}/retry`); ElMessage.success('已重新排队'); await openJob(id); await load() }
  catch { /* shared HTTP handler displays error */ }
  finally { saving.value = false }
}
onMounted(async () => {
  try {
    projects.value = await app.fetchProjects()
    const requested = Number(route.query.project_id), requestedJob = Number(route.query.job_id)
    pid.value = projects.value.some(p => p.id === requested) ? requested : pickDefaultProjectId(projects.value)
    await load()
    if (requestedJob && pid.value === requested) await openJob(requestedJob)
  } catch { loadError.value = true }
})
</script>
<style scoped>
.imports { padding: 20px; }
.el-pagination { margin-top: 16px; }
.compare p { white-space: pre-wrap; overflow-wrap: anywhere; }
.compare pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 300px; overflow: auto; }
.candidate { border: 1px solid var(--el-border-color); border-radius: 8px; padding: 12px; margin: 12px 0; }
.compare .el-textarea { margin-top: 12px; }
.el-table { margin-top: 12px; }
</style>
