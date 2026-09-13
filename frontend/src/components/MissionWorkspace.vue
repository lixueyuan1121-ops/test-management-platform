<template>
  <WorkspacePage title="测试目标">
    <template #actions>
      <el-select v-model="projectId" aria-label="目标所属项目" @change="changeProject" style="width:190px"><el-option v-for="p in projects" :key="p.id" :value="p.id" :label="p.name" /></el-select>
      <el-button type="primary" :disabled="!canWrite" @click="newGoal">新建测试目标</el-button>
    </template>
    <p class="intro">描述要验证的结果。AI 理解需求、复用与补充用例、跟进执行并整理证据；你确认业务意图和执行方案。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="mission-layout">
      <aside class="mission-list" aria-label="已保存的测试目标">
        <el-empty v-if="!missions.length" description="还没有测试目标" :image-size="60" />
        <button v-for="m in missions" :key="m.id" class="mission-item" :class="{ selected: mission?.id === m.id }" @click="selectMission(m.id)">
          <span>#{{ m.id }} · {{ phaseLabel(m.phase) }}{{ m.paused ? ' · 已暂停' : '' }}</span><strong>{{ m.goal }}</strong>
        </button>
      </aside>
      <main class="mission-main">
        <el-empty v-if="!mission" description="从一个测试目标开始，或恢复左侧的目标" />
        <template v-else>
          <div class="mission-heading"><div><small>目标 #{{ mission.id }} · 验收版本 {{ mission.baseline_id ? `#${mission.baseline_id}` : '待确认' }}</small><h2>{{ mission.goal }}</h2></div><el-tag :type="mission.phase === 'completed' ? 'info' : 'primary'">{{ phaseLabel(mission.phase) }}</el-tag></div>
          <section class="progress-card" aria-label="AI 当前进度">
            <strong>{{ mission.paused ? '后续推进已暂停' : progressMessage }}</strong>
            <p>{{ mission.events.at(-1)?.message || '目标已保存' }}</p>
            <div class="actions">
              <el-button size="small" @click="refresh">刷新</el-button><el-button v-if="mission.phase === 'completed' && mission.plan?.cases?.length" size="small" @click="showPlan = !showPlan">{{ showPlan ? '收起方案' : '查看本轮方案' }}</el-button>
              <el-button v-if="canWrite && mission.phase !== 'completed' && !mission.paused" size="small" :disabled="acting" @click="act('pause')">暂停后续推进</el-button>
              <el-button v-if="canWrite && (mission.paused || mission.phase === 'attention') && !mission.stale" size="small" :disabled="acting" @click="act('resume')">继续推进</el-button>
              <el-button v-if="canWrite && ['awaiting_approval', 'attention'].includes(mission.phase) && !mission.report?.runs.length" size="small" :disabled="acting" @click="act('replan')">重新准备方案</el-button>
              <el-button v-if="canWrite && (mission.stale || mission.phase === 'completed')" size="small" @click="newRound">开始新一轮</el-button>
              <el-button v-if="canWrite && !mission.report?.runs.length && mission.phase !== 'completed'" size="small" @click="finishOpen = true">带缺口收口</el-button>
            </div>
            <p v-if="mission.paused" class="hint">已下发的执行仍可能继续。暂停不等于停止远端执行机。</p>
          </section>
          <el-alert v-if="mission.error || mission.stale" :title="mission.stale ? '验收规则已变化，旧执行证据不能代表当前版本。请核对新版本后开始新一轮。' : mission.error" type="warning" :closable="false" show-icon />
          <RequirementReview v-if="analysis && (mission.phase === 'clarifying' || mission.stale)" :key="mission.analysis_id"
            :analysis-id="mission.analysis_id" embedded :project-id="mission.project_id" :task-id="mission.task_id"
            :requirement="analysis.source_text" :provider="mission.provider" :source-id="analysis.source_id" :source-url="analysis.source_url"
            :disabled="!canWrite || mission.paused" :available="true" />
          <el-collapse v-else-if="analysis" class="source-detail"><el-collapse-item title="查看本次产品理解与原始资料" name="source">
            <p>{{ analysis.draft?.summary || '正在分析' }}</p><p>{{ analysis.draft?.scope }}</p>
            <p v-if="analysis.materials?.length">包含 {{ analysis.materials.length }} 张图片或页面，识别内容和原图可在需求详情中核对。</p>
            <el-button size="small" @click="reviewOpen = true">查看需求与图片依据</el-button>
          </el-collapse-item></el-collapse>
          <section v-if="mission.plan?.cases?.length && (mission.phase !== 'completed' || showPlan)" aria-label="测试方案" class="plan-card">
            <h3>测试方案</h3><p>{{ mission.plan.summary }}</p>
            <ul v-if="mission.plan.risks?.length"><li v-for="risk in mission.plan.risks" :key="risk">{{ risk }}</li></ul>
            <el-alert v-if="mission.plan.missing_criteria?.length" :title="`方案尚未覆盖 ${mission.plan.missing_criteria.length} 个验收条件；会保留在最终报告中`" type="warning" :closable="false" />
            <article v-for="c in mission.plan.cases" :key="c.id" class="case-row">
              <el-checkbox v-if="mission.phase === 'awaiting_approval'" :model-value="selectedIds.includes(c.id)" :disabled="!canWrite || c.kind === 'manual' || acting || mission.paused" @change="toggleCase(c.id, $event)">{{ c.kind === 'manual' ? '待人工处理' : '纳入本次执行' }}</el-checkbox>
              <h4>#{{ c.id }} {{ c.title }} <el-tag size="small" :type="c.reused ? 'success' : 'info'">{{ c.reused ? '复用已有用例' : '新补充用例' }}</el-tag></h4>
              <p>{{ c.reason }}</p><p class="hint">{{ c.platform }} · {{ c.kind }} · 验收条件 {{ c.criterion_ids.join('、') }}</p>
              <el-collapse><el-collapse-item title="核对前置、步骤、预期与来源" :name="c.id"><dl><dt>前置条件</dt><dd>{{ c.precondition || '未填写' }}</dd><dt>操作步骤</dt><dd>{{ c.steps }}</dd><dt>预期结果</dt><dd>{{ c.expected }}</dd><dt>覆盖关联来源</dt><dd v-for="p in c.provenance" :key="p.criterion_id">{{ p.criterion_id }} ← 版本 #{{ p.baseline_id }} / {{ p.source_criterion_id }}</dd></dl><el-button size="small" @click="openCases">在用例库查看或修订</el-button></el-collapse-item></el-collapse>
            </article>
            <div v-if="mission.phase === 'awaiting_approval' && canWrite" class="authorization">
              <h4>执行授权</h4>
              <p class="hint">上限 {{ mission.policy.max_cases }} 条，已选 {{ selectedIds.length }} 条。用例内容改变后需要重新准备方案。</p>
              <el-form label-position="top" :disabled="acting || mission.paused">
                <el-form-item label="执行设备"><el-select v-model="runner" placeholder="选择已登记的执行设备" aria-label="执行设备"><el-option v-for="d in devices" :key="d.id" :value="d.runner_id" :label="`${d.name} · ${d.platform}`" /></el-select><el-button link @click="$router.push('/my-devices')">管理设备</el-button></el-form-item>
                <el-form-item label="执行预算（分钟，包含排队）"><el-input-number v-model="budget" :min="5" :max="240" aria-label="执行预算" /></el-form-item>
                <el-checkbox v-model="retry">允许环境阻塞自动复测一次（仅 AI 高置信度归因，保留原失败）</el-checkbox>
                <el-checkbox v-model="reviewed" class="reviewed-check">我已核对所选用例的前置、步骤、预期及验收关联，授权在该设备和预算内执行</el-checkbox>
                <el-button type="primary" :loading="acting" :disabled="!reviewed || !runner || !selectedIds.length" @click="act('approve')">确认方案并开始执行</el-button>
              </el-form>
            </div>
          </section>
          <section v-if="mission.report && (mission.report.runs.length || mission.phase === 'completed')" aria-label="验收证据与质量结论" class="report-card">
            <h3>{{ mission.phase === 'completed' ? '质量结论' : '执行证据' }}</h3>
            <el-alert :title="mission.report.summary" :type="mission.report.verdict === 'ready_for_review' ? 'success' : 'warning'" :closable="false" />
            <p><b>{{ mission.report.verified }} / {{ mission.report.total }}</b> 个验收条件具备有效执行证据 · {{ mission.report.pending_rules.length }} 条规则仍待确认</p>
            <p class="hint">流程完成不等于可以发布。以下状态按当前版本与执行结果核对；重试通过保留为不稳定。</p>
            <el-table class="desktop-evidence" :data="mission.report.criteria" size="small" border><el-table-column prop="id" label="验收条件" width="110" /><el-table-column prop="text" label="产品预期" min-width="180" /><el-table-column label="证据状态" width="115"><template #default="{ row }">{{ evidenceLabel(row.state) }}</template></el-table-column><el-table-column label="追溯" min-width="150"><template #default="{ row }"><span>执行 {{ row.run_ids.map(i => `#${i}`).join('、') || '未执行' }}</span><p class="hint">{{ row.source_section }} {{ row.source_quote }}</p><span v-if="row.unexecuted_case_ids?.length">未执行用例：{{ row.unexecuted_case_ids.join('、') }}</span></template></el-table-column></el-table>
            <div class="mobile-evidence"><article v-for="c in mission.report.criteria" :key="c.id"><strong>{{ c.id }} · {{ evidenceLabel(c.state) }}</strong><p>{{ c.text }}</p><p class="hint">执行 {{ c.run_ids.map(i => `#${i}`).join('、') || '未执行' }} · {{ c.source_section }}</p><p class="hint">{{ c.source_quote }}</p></article></div>
            <el-collapse><el-collapse-item v-if="mission.report.excluded_rules?.length" title="本期排除及原因" name="excluded"><p v-for="r in mission.report.excluded_rules" :key="r.id">{{ r.id }} · {{ r.reason }}</p></el-collapse-item><el-collapse-item title="执行记录、失败归因与重试链" name="runs"><article v-for="r in mission.report.runs" :key="r.id" class="run-row"><strong>#{{ r.id }} · {{ evidenceLabel(r.status) }} · 第 {{ r.attempt }} 次{{ r.retry_of ? ` · 复测自 #${r.retry_of}` : '' }}</strong><p>{{ r.reason }}</p><p v-if="r.triage?.kind">AI 归因参考：{{ r.triage.kind }} · {{ r.triage.reason }}<br />建议：{{ r.triage.suggestion }}</p><p v-if="r.triage_error">归因失败：{{ r.triage_error }}</p><el-button v-if="r.has_report" size="small" @click="showRunEvidence(r.id)">查看执行步骤</el-button><a v-if="safeEvidence(r.evidence_url)" :href="r.evidence_url" target="_blank" rel="noopener noreferrer">查看证据</a></article><el-button size="small" @click="openResults">打开执行结果详情</el-button></el-collapse-item>
              <el-collapse-item v-if="mission.final_report?.generated_at" title="查看流程收口时的报告快照" name="snapshot"><p>{{ mission.final_report.generated_at }} · {{ mission.final_report.summary }}</p><p>{{ mission.final_report.verified }} / {{ mission.final_report.total }} 个验收条件；后续纠偏或版本变化会在上方当前证据中体现。</p></el-collapse-item></el-collapse>
          </section>
          <section class="timeline-card"><h3>推进记录与人工决定</h3><ol><li v-for="e in mission.events" :key="e.id"><time>{{ e.created_at?.replace('T', ' ') }}</time><span>{{ e.message }}</span><small v-if="e.actor_id">决定人 #{{ e.actor_id }}</small><small v-if="e.data?.job_id">AI 任务 #{{ e.data.job_id }}</small></li></ol></section>
        </template>
      </main>
    </div>
    <el-dialog v-model="createOpen" title="新建测试目标" width="min(780px, 95vw)" :close-on-click-modal="false">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <el-form label-position="top" :disabled="creating || importing">
        <el-form-item label="希望验证什么结果"><el-input v-model="goal" type="textarea" :rows="3" maxlength="4000" placeholder="例如：验证文件权限二期，重点检查保护目录删除确认、取消后文件保留和跨平台差异。" aria-label="测试目标描述" /></el-form-item>
        <el-form-item label="需求来源"><el-radio-group v-model="sourceMode"><el-radio value="new">导入新需求</el-radio><el-radio value="existing">已有需求分析</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="sourceMode === 'existing'" label="选择需求分析"><el-select v-model="existingAnalysisId" filterable aria-label="已有需求分析"><el-option v-for="a in analyses" :key="a.id" :value="a.id" :label="`#${a.id} · ${a.source_title || '文本需求'} · ${a.baseline_id ? '已确认' : '待确认'}`" /></el-select></el-form-item>
        <template v-else>
          <el-form-item label="关联测试任务"><el-select v-model="taskId" filterable aria-label="关联测试任务"><el-option v-for="t in tasks" :key="t.id" :value="t.id" :label="t.title" /></el-select><el-button link @click="$router.push('/tasks')">管理任务</el-button></el-form-item>
          <el-form-item label="需求链接（支持飞书文档中的图片）"><el-input v-model="sourceUrl" placeholder="粘贴需求文档链接"><template #append><el-button @click="importUrl">读取</el-button></template></el-input></el-form-item>
          <div class="actions"><el-upload :auto-upload="false" :show-file-list="false" :on-change="importFile" accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg,.webp,.gif,.bmp"><el-button>导入文档或图片</el-button></el-upload><el-upload v-if="sourceId" :auto-upload="false" :show-file-list="false" :on-change="appendImage" accept=".png,.jpg,.jpeg,.webp,.gif,.bmp"><el-button>补充图片</el-button></el-upload></div>
          <p v-if="sourceId" class="hint">{{ sourceTitle }} · 已读取 {{ materials.length }} 张图片或页面</p>
          <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" />
          <el-form-item label="需求正文"><el-input v-model="requirement" type="textarea" :rows="6" maxlength="60000" placeholder="可直接粘贴需求；图片识别与图文冲突会在分析阶段处理。" aria-label="需求正文" /></el-form-item>
          <el-form-item label="分析与生成引擎"><el-select v-model="provider"><el-option v-for="p in providers" :key="p.id" :value="p.id" :label="p.id" :disabled="!p.available" /></el-select></el-form-item>
        </template>
        <el-form-item label="方案用例上限"><el-input-number v-model="maxCases" :min="1" :max="50" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createOpen = false">取消</el-button><el-button type="primary" :loading="creating || importing" :disabled="!canCreate" @click="create">创建目标并开始分析</el-button></template>
    </el-dialog>
    <el-dialog v-model="reviewOpen" title="产品理解与验收依据" width="min(1100px, 96vw)"><RequirementReview v-if="reviewOpen && analysis" :analysis-id="analysis.id" :project-id="mission.project_id" :task-id="mission.task_id" :requirement="analysis.source_text" embedded disabled /></el-dialog>
    <el-dialog v-model="runOpen" title="执行证据" width="min(900px, 95vw)"><div v-loading="evidenceLoading"><template v-if="runEvidence"><p>#{{ runEvidence.id }} · {{ evidenceLabel(runEvidence.status) }} · {{ runEvidence.reason }}</p><ol v-if="Array.isArray(runEvidence.report)"><li v-for="(s, i) in runEvidence.report" :key="i" class="run-row"><strong>{{ s.ok === true ? '通过' : s.ok === false ? '失败' : '执行记录' }} · {{ s.action }}</strong><p>{{ s.desc || s.description }}</p><p v-if="s.error">{{ s.error }}</p><pre v-if="s.check">{{ JSON.stringify(s.check, null, 2) }}</pre></li></ol><pre v-else>{{ JSON.stringify(runEvidence.report, null, 2) }}</pre></template></div></el-dialog>
    <el-dialog v-model="finishOpen" title="保留缺口并收口" width="min(520px, 94vw)"><p>未执行与未确认内容会保留为风险，不会标记为通过。</p><el-input v-model="finishNote" type="textarea" :rows="3" placeholder="例如：剩余场景需线下人工验证，本次先收口自动化准备" /><template #footer><el-button :disabled="!finishNote.trim()" :loading="acting" @click="act('finish')">记录原因并收口</el-button></template></el-dialog>
  </WorkspacePage>
</template>
<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/store/app'
import { useAuthStore } from '@/store/auth'
import WorkspacePage from '@/components/WorkspacePage.vue'
import RequirementReview from '@/components/RequirementReview.vue'
import { listTestMissions, createTestMission, getTestMission, decideTestMission, listRequirementAnalyses, getRequirementAnalysis, listTasks, listMyDevices, extractUrl, extractFile, aiStatus, getMissionRunEvidence } from '@/api'
const route = useRoute(), router = useRouter(), app = useAppStore(), auth = useAuthStore()
const projects = ref([]), projectId = ref(null), missions = ref([]), mission = ref(null), analysis = ref(null), error = ref('')
const showPlan=ref(false), runOpen=ref(false), runEvidence=ref(null), evidenceLoading=ref(false)
const tasks = ref([]), analyses = ref([]), devices = ref([]), providers = ref([])
const createOpen = ref(false), reviewOpen = ref(false), finishOpen = ref(false), finishNote = ref(''), creating = ref(false), importing = ref(false), acting = ref(false)
const goal = ref(''), sourceMode = ref('new'), existingAnalysisId = ref(null), taskId = ref(null), requirement = ref(''), sourceUrl = ref(''), importedUrl = ref(''), sourceId = ref(null), sourceTitle = ref(''), materials = ref([]), warnings = ref([]), provider = ref('claude'), maxCases = ref(20)
const selectedIds = ref([]), runner = ref(''), budget = ref(60), retry = ref(false), reviewed = ref(false)
let timer, disposed = false, selectionVersion = 0, projectVersion = 0, refreshing = false
const canWrite = computed(() => auth.isPlatformAdmin || auth.memberships?.some(m => m.project_id === projectId.value && ['admin', 'member'].includes(m.role)))
const canCreate = computed(() => canWrite.value && goal.value.trim().length >= 3 && (sourceMode.value === 'existing' ? !!existingAnalysisId.value : !!taskId.value && !!requirement.value.trim() && (!sourceUrl.value.trim() || sourceUrl.value === importedUrl.value) && providers.value.some(p => p.id === provider.value && p.available)))
const phaseLabel = p => ({ analyzing:'理解需求', clarifying:'待确认验收', generating:'补充用例', planning:'制定方案', awaiting_approval:'待授权执行', executing:'执行中', triaging:'归因与复测', attention:'需要处理', completed:'已收口' }[p] || p)
const evidenceLabel = s => ({ verified:'证据齐备', missing:'待补验证', stale:'内容已变化', pending:'等待执行', running:'执行中', passed:'执行通过', failed:'执行失败', blocked:'执行阻塞', flaky:'重试后通过', unreviewed:'待核对用例', no_evidence:'缺少证据' }[s] || s)
const progressMessage = computed(() => ({ analyzing:'AI 正在理解正文、图片与历史项目规则', clarifying:'需要你确认业务意图', generating:'AI 正在为未覆盖的验收条件补充用例', planning:'AI 正在选择用例并说明风险与取舍', awaiting_approval:'需要你核对方案并授权执行', executing:'正在等待执行机回传结果', triaging:'AI 正在归因，并按授权条件决定是否复测', attention:'存在需要处理的阻碍', completed:'流程已收口，请查看质量结论与证据缺口' }[mission.value?.phase] || ''))
const messageOf = e => e?.response?.data?.msg || e?.response?.data?.detail || e?.message || '操作失败'
const safeEvidence = url => typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/(?!\/)/.test(url))
async function loadContext() {
  const v = ++projectVersion, pid = projectId.value
  tasks.value = []; analyses.value = []; devices.value = []
  const results = await Promise.allSettled([listTasks({ project_id:pid }), listRequirementAnalyses({ project_id:pid, limit:100 }), listMyDevices(), aiStatus()])
  if (disposed || v !== projectVersion) return
  for (let i=0;i<results.length;i++) if (results[i].status === 'rejected') error.value = messageOf(results[i].reason)
  tasks.value = results[0].status === 'fulfilled' ? (results[0].value.items || results[0].value) : []
  analyses.value = results[1].status === 'fulfilled' ? results[1].value : []
  devices.value = results[2].status === 'fulfilled' ? results[2].value : []
  providers.value = results[3].status === 'fulfilled' ? results[3].value.providers || [] : []
  provider.value = providers.value.find(p => p.available)?.id || 'claude'
}
async function changeProject() {
  selectionVersion++; app.setLastProject(projectId.value); mission.value = null; analysis.value = null; missions.value = []; error.value = ''
  existingAnalysisId.value = null; taskId.value = null; sourceId.value = null; requirement.value = ''; materials.value = []; warnings.value = []; sourceUrl.value = ''; importedUrl.value = ''
  await router.replace({ path:'/commander' }); await Promise.all([loadContext(), refresh()])
}
async function refresh() {
  if (!projectId.value || refreshing) return
  refreshing = true
  const pid = projectId.value, version = selectionVersion
  try {
    const list = await listTestMissions(pid)
    if (disposed || pid !== projectId.value) return
    missions.value = list
    if (mission.value && version === selectionVersion) await fetchSelected(mission.value.id, version)
  } catch(e) { if (!disposed && pid === projectId.value) error.value = messageOf(e) }
  finally { refreshing = false }
}
async function fetchSelected(id, version) {
  const value = await getTestMission(id)
  if (disposed || version !== selectionVersion || (mission.value?.id === id && value.revision < mission.value.revision)) return
  if (mission.value?.id !== id || mission.value?.plan?.baseline_id !== value.plan?.baseline_id || JSON.stringify(mission.value?.plan?.cases?.map(c => c.hash)) !== JSON.stringify(value.plan?.cases?.map(c => c.hash))) {
    selectedIds.value = (value.plan?.cases || []).filter(c => c.kind !== 'manual').map(c => c.id); reviewed.value = false
  }
  const needsAnalysis = analysis.value?.id !== value.analysis_id || (value.phase === 'clarifying' && mission.value?.phase !== 'clarifying')
  mission.value = value
  if (needsAnalysis && value.analysis_id) {
    const a = await getRequirementAnalysis(value.analysis_id)
    if (!disposed && version === selectionVersion) analysis.value = a
  }
}
async function selectMission(id) {
  showPlan.value=false
  const v = ++selectionVersion
  error.value = ''; analysis.value = null
  try { await fetchSelected(id, v); if (!disposed && v === selectionVersion) await router.replace({ path:'/commander', query:{ mission:id, project_id:projectId.value } }) }
  catch(e) { if (v === selectionVersion) error.value = messageOf(e) }
}
function newGoal() { goal.value=''; sourceMode.value='new'; taskId.value=null; existingAnalysisId.value=null; sourceId.value=null; sourceTitle.value=''; requirement.value=''; sourceUrl.value=''; importedUrl.value=''; materials.value=[]; warnings.value=[]; error.value=''; createOpen.value=true; loadContext() }
function newRound() { goal.value=mission.value.goal; sourceMode.value='existing'; existingAnalysisId.value=mission.value.analysis_id; createOpen.value=true; loadContext() }
function setSource(data) { requirement.value=data.text; sourceId.value=data.source_id; sourceTitle.value=data.title || data.filename || '需求资料'; materials.value=data.materials || []; warnings.value=data.warnings || [] }
async function importUrl() { importing.value=true; error.value=''; try { setSource(await extractUrl(sourceUrl.value)); importedUrl.value=sourceUrl.value } catch(e){ error.value=messageOf(e) } finally { importing.value=false } }
async function importFile(upload) { importing.value=true; error.value=''; try { setSource(await extractFile(upload.raw)); sourceUrl.value=''; importedUrl.value='' } catch(e){error.value=messageOf(e)} finally{importing.value=false} }
async function appendImage(upload) { importing.value=true; error.value=''; const text=requirement.value; try { setSource(await extractFile(upload.raw, sourceId.value)); requirement.value=text } catch(e){error.value=messageOf(e)} finally{importing.value=false} }
async function create() {
  if (!canCreate.value) return
  creating.value=true; error.value=''
  try {
    const payload={ project_id:projectId.value, goal:goal.value.trim(), max_cases:maxCases.value }
    if (sourceMode.value === 'existing') payload.analysis_id=existingAnalysisId.value
    else payload.requirement={ project_id:projectId.value, task_id:taskId.value, requirement:requirement.value, provider:provider.value, source_id:sourceId.value, source_url:importedUrl.value, source_title:sourceTitle.value, input_type:sourceId.value ? (importedUrl.value ? 'url' : 'file') : 'text' }
    const value=await createTestMission(payload); createOpen.value=false; await selectMission(value.id); await refresh()
  } catch(e){ error.value=messageOf(e) } finally{ creating.value=false }
}
function toggleCase(id, checked) { selectedIds.value = checked ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter(i => i !== id); reviewed.value=false }
async function act(action) {
  acting.value=true; error.value=''
  try { const value=await decideTestMission(mission.value.id,{ revision:mission.value.revision, action, runner:runner.value, max_retries:retry.value ? 1 : 0, time_budget_minutes:budget.value, case_ids:selectedIds.value, reviewed:reviewed.value, note:finishNote.value }); mission.value=value; finishOpen.value=false; await refresh() }
  catch(e) { error.value=messageOf(e); await refresh() }
  finally { acting.value=false }
}
async function showRunEvidence(runId) { runOpen.value=true; runEvidence.value=null; evidenceLoading.value=true; try { runEvidence.value=await getMissionRunEvidence(mission.value.id,runId) } catch(e){error.value=messageOf(e);runOpen.value=false} finally{evidenceLoading.value=false} }
function openCases() { router.push({ path:'/case-library', query:{ project_id:projectId.value, task_id:mission.value.task_id } }) }
function openResults() { router.push({ path:'/exec-results', query:{ project_id:projectId.value, task_id:mission.value.task_id, batch_id:mission.value.report?.runs?.[0]?.batch_id } }) }
onMounted(async () => {
  try {
    projects.value=await app.fetchProjects(); projectId.value=projects.value.find(p => p.id === Number(route.query.project_id))?.id || app.resolveProjectId(projects.value)
    if (projectId.value) { await Promise.all([loadContext(), refresh()]); if (route.query.mission) await selectMission(Number(route.query.mission)) }
    if (route.query.analysis_id) { sourceMode.value='existing'; existingAnalysisId.value=Number(route.query.analysis_id); createOpen.value=true }
  } catch(e){ error.value=messageOf(e) }
  if (!disposed) timer=setInterval(refresh, 4000)
})
onBeforeUnmount(() => { disposed=true; selectionVersion++; projectVersion++; clearInterval(timer) })
</script>
<style scoped>
.intro,.hint { color:var(--el-text-color-secondary); line-height:1.65; }.intro{max-width:900px;margin-bottom:24px}
.mission-layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:24px;align-items:start}.mission-list{display:grid;gap:8px}.mission-item{text-align:left;border:1px solid var(--el-border-color-lighter);border-radius:10px;padding:14px;background:var(--el-bg-color);cursor:pointer;color:var(--el-text-color-primary);overflow-wrap:anywhere}.mission-item span{display:block;font-size:12px;color:var(--el-text-color-secondary);margin-bottom:8px}.mission-item strong{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.mission-item.selected{border-color:var(--el-color-primary);background:var(--el-color-primary-light-9)}
.mission-main{min-width:0}.mission-heading{display:flex;gap:16px;justify-content:space-between;align-items:start}.mission-heading h2{font-size:22px;line-height:1.5;margin:10px 0 20px;overflow-wrap:anywhere}.mission-heading small{color:var(--el-text-color-secondary)}.progress-card,.plan-card,.report-card,.timeline-card{border:1px solid var(--el-border-color-lighter);padding:20px;border-radius:12px;margin-bottom:20px;background:var(--el-bg-color)}.progress-card{background:var(--el-color-primary-light-9)}.actions{display:flex;gap:10px;flex-wrap:wrap}.actions .el-button+.el-button{margin-left:0}.case-row{border-top:1px solid var(--el-border-color-lighter);padding:16px 0}.case-row h4{margin:10px 0}.case-row p,.run-row p{white-space:pre-wrap;overflow-wrap:anywhere}dl{margin:0}dt{font-weight:600;margin-top:10px}dd{margin:5px 0 12px;white-space:pre-wrap;line-height:1.7}.authorization{background:var(--el-fill-color-light);padding:16px;border-radius:8px}.reviewed-check{display:flex;margin:16px 0;height:auto}.reviewed-check :deep(.el-checkbox__label){white-space:normal;line-height:1.7}.source-detail{margin:16px 0}.timeline-card ol{padding-left:18px}.timeline-card li{padding:8px 0;line-height:1.6}.timeline-card time,.timeline-card small{display:block;color:var(--el-text-color-secondary);font-size:12px}.run-row{padding:12px 0;border-bottom:1px solid var(--el-border-color-lighter)}
.mobile-evidence{display:none}.run-row pre{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:600px){.desktop-evidence{display:none}.mobile-evidence{display:block}.mobile-evidence article{padding:14px 0;border-bottom:1px solid var(--el-border-color-lighter)}}
@media(max-width:900px){.mission-layout{grid-template-columns:1fr}.mission-list{grid-template-columns:repeat(2,minmax(0,1fr));max-height:220px;overflow:auto}.mission-heading{flex-wrap:wrap}.progress-card,.plan-card,.report-card,.timeline-card{padding:14px}}
@media(max-width:480px){.mission-list{grid-template-columns:1fr}.mission-heading h2{font-size:19px}.authorization :deep(.el-checkbox){height:auto;white-space:normal}.authorization :deep(.el-checkbox__label){white-space:normal}}
</style>
