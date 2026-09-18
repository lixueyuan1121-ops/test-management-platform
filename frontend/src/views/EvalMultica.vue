<template>
  <div class="multica-page">
    <header class="page-head">
      <div><h1>Multica 复测</h1><p>集中管理已推送反馈，保留原结果，与优化后的复测结果对照。</p></div>
      <el-select v-model="pid" aria-label="选择项目" placeholder="选择项目" @change="changeProject">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
    </header>
    <div class="collection-tools">
      <el-input v-model="search" aria-label="搜索已推送结果" placeholder="搜索用例、提问、RUN ID 或 Multica 编号" clearable @keyup.enter="resetPage" @clear="resetPage" />
      <el-select v-model="engine" aria-label="筛选原产品" placeholder="全部原产品" clearable @change="resetPage">
        <el-option v-for="(label, key) in engines" :key="key" :label="label" :value="key" />
      </el-select>
      <el-button @click="resetPage">搜索</el-button><el-button :loading="loading" @click="load">刷新</el-button>
      <el-button type="primary" :disabled="!canWrite || !selection.length" @click="openRetest">复测所选（{{ selection.length }}）</el-button>
    </div>
    <p class="hint">共 {{ total }} 条已推送结果 · 选择仅限当前页。历史推送记录自动纳入；未记录推送日期的显示为“历史推送”。</p>
    <el-table ref="table" :data="rows" v-loading="loading" row-key="run_id" border @selection-change="selection = $event">
      <el-table-column type="selection" width="45" :selectable="r => canWrite && !r.active_retests" />
      <el-table-column label="反馈用例" min-width="250">
        <template #default="{ row }"><strong>{{ title(row) }}</strong><div class="hint">RUN-{{ row.run_id }} · {{ row.batch_id || '独立下发' }}</div>
          <div class="model-text">{{ config(row) }}</div></template>
      </el-table-column>
      <el-table-column label="原产品" width="115"><template #default="{ row }">{{ product(row) }}</template></el-table-column>
      <el-table-column label="原判定" width="120"><template #default="{ row }"><el-tag :type="tone(row)">{{ result(row) }}</el-tag><div v-if="row.score != null" class="hint">{{ row.score }} / 5 分</div></template></el-table-column>
      <el-table-column label="推送记录" min-width="180"><template #default="{ row }">
        <div>{{ row.multica_pushed_at ? date(row.multica_pushed_at) : '历史推送' }}</div>
        <el-link v-if="safeLink(row.multica_ref)" :href="row.multica_ref" target="_blank" rel="noopener noreferrer">查看 Multica 任务</el-link>
        <small v-else class="ref-text">{{ row.multica_ref || '未记录任务编号' }}</small>
      </template></el-table-column>
      <el-table-column label="最近复测记录" min-width="195"><template #default="{ row }">
        <template v-if="row.latest_retest"><div>{{ product(row.latest_retest) }} · {{ result(row.latest_retest) }}<span v-if="row.latest_retest.score != null"> · {{ row.latest_retest.score }} 分</span></div>
          <div class="hint">共 {{ row.retest_count }} 条复测记录<span v-if="row.active_retests"> · {{ row.active_retests }} 条执行 / 判定中</span></div></template><span v-else class="hint">尚未复测</span>
      </template></el-table-column>
      <el-table-column label="操作" width="125" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openHistory(row)">记录与对照</el-button></template></el-table-column>
      <template #empty><el-empty :description="loadError || (pid ? '暂无已推送 Multica 的测评结果' : '请选择项目')" :image-size="60" /></template>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @current-change="load" @size-change="resetPage" />

    <el-dialog v-model="retestVisible" title="发起 Multica 复测" width="min(620px, 95vw)" :close-on-click-modal="!submitting" :close-on-press-escape="!submitting" :show-close="!submitting">
      <p>已选 {{ picked.length }} 条反馈。使用原提问、附件和验收标准；多轮会话会自动补齐同次执行的其他轮次。</p>
      <div class="original-configs"><div v-for="r in picked" :key="r.run_id">RUN-{{ r.run_id }} · {{ product(r) }} · {{ config(r) }}</div></div>
      <el-form label-position="top">
        <el-form-item label="复测产品">
          <el-checkbox v-model="changeProducts" :disabled="submitting">修改产品（未勾选时沿用各条原产品）</el-checkbox>
          <el-select v-model="targetEngines" multiple :disabled="!changeProducts || submitting" aria-label="选择复测产品" style="width:100%">
            <el-option v-for="(label, key) in engines" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="复测模型">
          <el-checkbox v-model="changeModel" :disabled="submitting">修改模型（未勾选时沿用各条原模型）</el-checkbox>
          <el-input v-model="model" :disabled="!changeModel || submitting" maxlength="64" aria-label="复测模型" :placeholder="modelPlaceholder" />
          <small class="hint">修改后应用到所选产品；勾选修改但留空表示使用客户端当前模型。WorkBuddy / QWork 不携带纳米Work专用模式或思考深度。</small>
        </el-form-item>
      </el-form>
      <p class="hint">自动分配支持相应产品的在线测评设备。新建复测任务与记录，原判定和推送状态保持不变；执行后可进入任务进行批量判定。</p>
      <template #footer><el-button :disabled="submitting" @click="retestVisible = false">取消</el-button><el-button type="primary" :loading="submitting" :disabled="changeProducts && !targetEngines.length" @click="submitRetest">创建并执行复测</el-button></template>
    </el-dialog>

    <el-drawer v-model="historyVisible" title="原结果与复测记录" size="min(980px, 100vw)">
      <div v-loading="historyLoading" class="history-content">
        <template v-if="history">
        <h2>{{ title(history.source) }}</h2>
        <div class="original-result"><strong>原结果：{{ product(history.source) }} · {{ result(history.source) }}</strong><span v-if="history.source.score != null"> · {{ history.source.score }} / 5 分</span>
          <p>{{ history.source.verdict_reason || history.source.reason || '暂无判定理由' }}</p>
          <el-button link type="primary" @click="inspectorVisible = true">查看原执行详情</el-button></div>
        <h3>复测记录 <el-button link type="primary" :loading="historyLoading" @click="openHistory(history.source, true)">刷新复测记录</el-button></h3><p class="hint">每行是一次产品执行。模型、产品变更会显示在这里，便于判断结果是否可比。</p>
        <el-table :data="history.retests" border>
          <el-table-column label="执行 / 时间" min-width="150"><template #default="{ row }">RUN-{{ row.run_id }}<div class="hint">{{ date(row.created_at) }}</div></template></el-table-column>
          <el-table-column label="产品 / 配置" min-width="200"><template #default="{ row }">{{ product(row) }}<div class="model-text">{{ config(row) }}</div></template></el-table-column>
          <el-table-column label="结果" min-width="110"><template #default="{ row }"><el-tag :type="tone(row)">{{ result(row) }}</el-tag><div v-if="row.score != null">{{ row.score }} / 5 分</div></template></el-table-column>
          <el-table-column label="判定 / 异常原因" min-width="230" prop="verdict_reason"><template #default="{ row }">{{ row.verdict_reason || row.reason || '—' }}</template></el-table-column>
          <el-table-column label="操作" width="150"><template #default="{ row }"><el-button v-if="row.eval_task_id" link type="primary" @click="viewTask(row)">查看任务 / 判定</el-button><el-link v-if="safeLink(row.share_link)" :href="row.share_link" target="_blank" rel="noopener noreferrer">对话原文</el-link></template></el-table-column>
        </el-table>
        </template>
      </div>
    </el-drawer>
    <EvalRunInspector v-if="history" v-model:visible="inspectorVisible" :row="history.source" :title="title(history.source)" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listMulticaResults, listMulticaRetests, createMulticaRetest } from '@/api'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'
import { pickDefaultProjectId, setLastProjectId } from '@/utils/lastProject'
import { fmtDialogOptions } from '@/utils/dialogOptions'
import EvalRunInspector from '@/components/EvalRunInspector.vue'

const route = useRoute(), router = useRouter(), app = useAppStore(), auth = useAuthStore()
const engines = { namiwork: '纳米Work', workbuddy: 'WorkBuddy', qwork: 'QWork' }
const statuses = { pending: '待执行', running: '执行中', done: '待判定', judging: '判定中', judged: '已判定', failed: '执行失败', cancelled: '已取消' }
const product = r => engines[r.target_engine || 'namiwork'] || r.target_engine
const title = r => r.payload?.title || r.payload?.prompt || `RUN-${r.run_id}`
const config = r => fmtDialogOptions(r.payload?.dialog_options) || '沿用客户端配置'
const result = r => r.status === 'failed' ? '执行失败' : ({ pass: '通过', fail: '不通过', error: '判定异常' }[r.verdict] || statuses[r.status] || '—')
const tone = r => r.status === 'failed' || r.verdict === 'fail' ? 'danger' : r.verdict === 'pass' ? 'success' : 'info'
const date = d => d ? String(d).replace('T', ' ').slice(0, 19) : '—'
const safeLink = u => /^https?:\/\//i.test(String(u || ''))
const projects = ref([]), pid = ref(null), rows = ref([]), total = ref(0), page = ref(1), pageSize = ref(20)
const search = ref(''), engine = ref(''), selection = ref([]), table = ref(), loading = ref(false), loadError = ref('')
const canWrite = computed(() => ['admin', 'member'].includes(auth.roleIn(pid.value)))
const retestVisible = ref(false), submitting = ref(false), picked = ref([]), changeProducts = ref(false), changeModel = ref(false)
const targetEngines = ref([]), model = ref(''), modelPlaceholder = ref('')
const historyVisible = ref(false), historyLoading = ref(false), history = ref(null), inspectorVisible = ref(false)
let revision = 0, historyRevision = 0, timer, alive = true
async function load() {
  if (!pid.value) return
  const current = ++revision
  loading.value = true; loadError.value = ''; selection.value = []; table.value?.clearSelection()
  try {
    const data = await listMulticaResults({ project_id: pid.value, page: page.value, page_size: pageSize.value, search: search.value, target_engine: engine.value || undefined })
    if (current !== revision) return
    rows.value = data.items; total.value = data.total
  } catch { if (current === revision) { rows.value = []; loadError.value = '加载失败，请点击刷新重试' } }
  finally { if (current === revision) loading.value = false }
}
function resetPage() { page.value = 1; load() }
function changeProject() {
  revision++; historyRevision++; historyVisible.value = false; inspectorVisible.value = false; history.value = null; retestVisible.value = false
  rows.value = []; total.value = 0; search.value = ''; engine.value = ''; selection.value = []; setLastProjectId(pid.value); resetPage()
}
function openRetest() {
  picked.value = [...selection.value]; changeProducts.value = false; changeModel.value = false
  targetEngines.value = [...new Set(picked.value.map(r => r.target_engine || 'namiwork'))]
  const models = [...new Set(picked.value.map(r => r.payload?.dialog_options?.model || ''))]
  model.value = models.length === 1 ? models[0] : ''
  modelPlaceholder.value = models.length > 1 ? '各条模型不同，默认分别沿用原模型' : '原配置未指定模型'
  retestVisible.value = true
}
async function submitRetest() {
  if (submitting.value || !picked.value.length) return
  submitting.value = true
  const projectId = pid.value
  try {
    const data = await createMulticaRetest({ project_id: projectId, run_ids: picked.value.map(r => r.run_id),
      target_engines: changeProducts.value ? targetEngines.value : null, model: changeModel.value ? model.value : null })
    if (pid.value !== projectId) return
    retestVisible.value = false
    ElMessage.success(`已创建复测任务 #${data.task_id}，下发 ${data.run_ids.length} 条（补齐 ${data.context_count} 条会话上下文）`)
    await load()
    await openHistory(picked.value[0])
  } catch { /* API interceptor displays actionable errors; retain the form for retry. */ }
  finally { submitting.value = false }
}
async function openHistory(row, refresh = false) {
  const current = ++historyRevision
  historyLoading.value = true; historyVisible.value = true; if (!refresh) history.value = null
  try { const data = await listMulticaRetests(row.run_id); if (current === historyRevision) history.value = data }
  catch { if (current === historyRevision) historyVisible.value = false }
  finally { if (current === historyRevision) historyLoading.value = false }
}
function viewTask(row) { setLastProjectId(pid.value); router.push({ path: '/eval-tasks', query: { project_id: pid.value, task_id: row.eval_task_id, batch_id: row.batch_id } }) }
onMounted(async () => {
  try { projects.value = await app.fetchProjects() } catch { return }
  if (!alive) return
  pid.value = projects.value.find(p => p.id === Number(route.query.project_id))?.id || pickDefaultProjectId(projects.value)
  await load()
  if (!alive) return
  timer = setInterval(() => {
    if (!loading.value && !submitting.value && !retestVisible.value && !selection.value.length && !historyVisible.value && rows.value.some(r => r.active_retests)) load()
  }, 5000)
})
onBeforeUnmount(() => { alive = false; revision++; historyRevision++; clearInterval(timer) })
</script>

<style scoped>
.multica-page { padding:24px; }
.page-head { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:20px; }
h1 { font-size:23px; margin:0 0 8px; } h2 { font-size:18px; overflow-wrap:anywhere; }
.page-head p, .hint { font-size:12px; color:#7a8798; margin:7px 0; }
.page-head .el-select { width:240px; flex-shrink:0; }
.collection-tools { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.collection-tools .el-input { width:340px; } .collection-tools .el-select { width:150px; }
.collection-tools .el-button + .el-button { margin-left:0; }
.el-pagination { justify-content:flex-end; margin-top:18px; flex-wrap:wrap; }
.model-text, .ref-text { display:block; color:#65758b; font-size:12px; overflow-wrap:anywhere; }
.original-configs { padding:12px; max-height:140px; overflow:auto; background:#f5f7fa; border-radius:6px; font-size:12px; margin-bottom:18px; }
.original-configs div { margin:4px 0; overflow-wrap:anywhere; }
.history-content { min-height:100px; }
.original-result { padding:16px; background:#f5f7fa; border-radius:8px; }
.original-result p { white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; }
.el-form-item :deep(.el-form-item__content) { display:block; }
@media(max-width:640px) { .multica-page { padding:12px; } .page-head { align-items:flex-start; flex-direction:column; } .page-head .el-select, .collection-tools .el-input { width:100%; } }
</style>
