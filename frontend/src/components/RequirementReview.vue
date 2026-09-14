<template>
  <section class="requirement-review" aria-label="需求澄清与验收确认">
    <el-steps v-if="!embedded" :active="baselineId ? 3 : draft ? 2 : 1" simple class="review-steps">
      <el-step title="导入需求" /><el-step title="澄清与确认" /><el-step title="生成用例" />
    </el-steps>
    <div class="review-head">
      <div><h3>需求澄清与验收确认</h3><p>原文说清楚的内容直接使用；只处理缺失、矛盾或需要你决定的问题。</p></div>
      <el-select v-if="!embedded" v-model="historyId" placeholder="恢复历史需求分析" clearable :disabled="working || disabled" @change="restoreAnalysis" style="width:260px">
        <el-option v-for="item in history" :key="item.id" :value="item.id" :label="`#${item.id} · ${item.source_title || '文本需求'} · ${statusLabel(item.status)}`" />
      </el-select>
    </div>
    <div class="review-actions">
      <el-button v-if="!embedded" type="primary" :disabled="disabled || !available || !projectId || !taskId || !requirement.trim() || working" @click="startAnalysis">{{ draft ? '重新分析需求' : '分析需求' }}</el-button>
      <el-button v-if="analysis && !draft && !working && ['pending', 'running'].includes(analysis.status)" @click="resumeAnalysis">继续查看分析进度</el-button>
      <el-button v-if="canRetry" type="primary" :disabled="disabled || saving || !available" @click="retryAnalysis">继续处理失败部分</el-button>
      <el-button v-if="working" @click="stopWaiting">停止等待</el-button>
      <span v-if="!taskId" class="hint">请先选择关联任务</span>
      <el-tag v-if="baselineId" type="success">已确认版本 #{{ baselineId }}</el-tag>
      <el-tag v-else-if="draft" type="warning">{{ dirty ? '修改尚未保存' : '待确认验收规则' }}</el-tag>
    </div>
    <AiJobProgress v-if="!draft && (working || liveJob?.progress)" :job="liveJob" :active="working" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div v-if="!draft && analysis && (canRetry || savedOutputs.length)" class="recovery-info">
      <p>已保存 {{ reusableImages }} 张图片识别结果、{{ completedParts }} 个分析阶段。继续处理会复用这些结果。</p>
      <p class="hint">完整草稿生成后进入人工评审；确认版本在你核对并确认后创建。</p>
      <el-button v-if="savedOutputs.length" size="small" @click="viewSavedOutput">查看已保存的返回内容</el-button>
    </div>
    <el-dialog v-model="savedOutputOpen" title="已保存的模型返回 · 未确认草稿" width="min(900px, 94vw)">
      <el-select v-model="savedOutputId" fit-input-width style="width:100%" aria-label="选择保存的分析输出" @change="loadSavedOutput">
        <el-option v-for="item in savedOutputs" :key="item.id" :value="item.id" :label="`${outputLabel(item.part_key, item.id)} · ${item.status === 'done' ? '已保存' : item.error_code || '未完成'} · ${item.chars} 字`" />
      </el-select>
      <p v-if="savedOutputError" class="blockers">{{ savedOutputError }}</p>
      <pre v-loading="savedOutputLoading" class="saved-output">{{ savedOutputText || '此阶段尚未收到正文' }}</pre>
    </el-dialog>
    <el-alert v-if="warnings.length" type="warning" :closable="false" show-icon title="资料范围需要核对">
      <ul><li v-for="(warning, i) in warnings" :key="i">{{ warning }}</li></ul>
    </el-alert>

    <article v-if="draft" class="review-focus">
      <h4>只处理需要你决定的内容</h4>
      <p>{{ draft.summary }}</p>
      <div class="review-actions">
        <el-button size="small" @click="tab = 'questions'">{{ attentionCount }} 个问题待处理</el-button>
        <el-button size="small" @click="filterPending">{{ pendingCount }} 条规则待确认</el-button>
        <el-button size="small" @click="tab = 'images'">{{ visuals.filter(v => v.status !== 'read').length }} 张图片存在不确定内容</el-button>
      </div>
      <p v-if="analysis?.review_focus" class="hint">{{ analysis.review_focus.note }}</p>
      <el-collapse v-if="analysis?.review_focus?.rules.some(r => r.baseline_id)">
        <el-collapse-item title="与历史人工确认的规则比较" name="changes">
          <p v-for="r in analysis.review_focus.rules.filter(r => r.baseline_id)" :key="r.id">{{ r.id }} · {{ r.kind === 'unchanged' ? '业务条件与历史一致，核对本期适用范围' : `已变化：${r.changed_fields.map(k => changeLabels[k] || k).join('、')}` }} · 历史版本 #{{ r.baseline_id }} · 确认人 #{{ r.confirmed_by }}</p>
        </el-collapse-item>
      </el-collapse>
    </article>
    <el-tabs v-if="draft || visuals.length" v-model="tab" class="review-tabs">
      <el-tab-pane v-if="draft" :label="`具体场景（${draft.scenarios?.length || 0}）`" name="scenarios"><AcceptanceScenarios :draft="draft" :disabled="disabled || working || saving" @image="id => showImage(visuals.find(v => v.id === id))" /></el-tab-pane>
      <el-tab-pane v-if="draft" label="本期范围" name="summary">
        <el-form label-position="top" :disabled="disabled || working || saving">
          <el-form-item v-for="field in overviewFields" :key="field.key" :label="field.label">
            <el-input v-model="draft[field.key]" type="textarea" :autosize="{ minRows: 2, maxRows: 7 }" />
          </el-form-item>
        </el-form>
      </el-tab-pane>
      <el-tab-pane v-if="draft" :label="`验收规则（${draft.rules.length}）`" name="rules">
        <div class="review-actions"><span>已明确 {{ confirmedCount }} · 待确认 {{ pendingCount }} · 本期排除 {{ excludedCount }}</span><el-button size="small" :disabled="disabled || saving || working" @click="addRule">补充规则</el-button></div>
        <p>已经说清楚的规则默认用于生成；待确认的先保留，不会混入本次用例。</p>
        <el-radio-group v-model="ruleFilter" aria-label="筛选验收规则" class="rule-filters">
          <el-radio-button value="all">全部（{{ rows.length }}）</el-radio-button>
          <el-radio-button value="pending">待确认（{{ pendingCount }}）</el-radio-button>
          <el-radio-button value="confirmed">已明确（{{ confirmedCount }}）</el-radio-button>
          <el-radio-button value="excluded">本期不测（{{ excludedCount }}）</el-radio-button>
        </el-radio-group>
        <el-empty v-if="!visibleRules.length" :description="ruleFilter === 'pending' ? '没有待确认的规则' : '没有符合筛选条件的规则'" />
        <el-table v-if="visibleRules.length" :data="visibleRules" border size="small" row-key="id">
          <el-table-column prop="id" label="编号" width="90" />
          <el-table-column label="要测什么、应该看到什么" min-width="290">
            <template #default="{ row }"><button class="rule-title" @click="editingRule = row">{{ row.title }}</button><p class="hint">{{ row.platform }} {{ row.module }} · {{ row.criteria.length }} 个验收条件</p><p><b>什么时候：</b>{{ row.condition || '还没说清楚' }}</p><p><b>做什么：</b>{{ row.action || '还没说清楚' }}</p><p><b>应该看到：</b>{{ row.expected || '还没说清楚' }}</p></template>
          </el-table-column>
          <el-table-column label="依据" width="110"><template #default="{ row }"><el-tag :type="row.source_type === 'explicit' ? 'info' : 'warning'" size="small">{{ row.source_type === 'explicit' ? '原文已说清楚' : '资料未说清楚' }}</el-tag></template></el-table-column>
          <el-table-column label="是否需要处理" width="200"><template #default="{ row }"><el-tag :type="stateOf(row) === 'confirmed' ? 'success' : stateOf(row) === 'excluded' ? 'info' : 'warning'">{{ {confirmed:'已明确，无需逐条确认',pending:'待确认',excluded:'本期不测'}[stateOf(row)] }}</el-tag><el-button v-if="stateOf(row) === 'pending'" link type="primary" @click="tab = 'questions'">去处理问题</el-button></template></el-table-column>
        </el-table>
        <p class="hint">发现不对可以点标题修改；本次不测的内容可在编辑中排除并说明原因。</p>
      </el-tab-pane>
      <el-tab-pane v-if="draft" :label="`澄清问题（${attentionCount} 待处理）`" name="questions">
        <p>只需回答影响测试结果的问题。明确的规则不用再确认，也不用逐个勾选场景。</p>
        <el-checkbox v-model="showAnswered">显示已处理的问题和补充建议</el-checkbox>
        <el-empty v-if="!questionsToShow.length && !pendingRows.length" description="没有必须处理的问题，可以直接确认本次生成范围" />
        <article v-for="question in questionsToShow" :key="question.id" class="question-card">
          <strong>{{ question.id }} · {{ question.question }}</strong>
          <p class="hint">{{ question.rule_ids.length ? `影响：${question.rule_ids.join('、')}` : '影响：本期整体范围' }} · {{ question.blocking ? '影响相关规则确认' : '补充建议' }}</p>
          <p class="evidence">{{ question.evidence }}</p>
          <ul><li v-for="option in question.options" :key="option">{{ option }}</li></ul>
          <el-input v-model="question.answer" type="textarea" :disabled="disabled || working || saving" :aria-label="`${question.id} 处理结论`" placeholder="请写清最终怎么做，例如：任务结束后立即恢复分享；同时检查下方对应规则是否一致。" @input="resetQuestionRules(question)" />
          <el-button v-for="rule in draft.rules.filter(r => !question.rule_ids.length || question.rule_ids.includes(r.id))" :key="rule.id" link type="primary" @click="editingRule = rule">修改对应规则：{{ rule.title }}</el-button>
        </article>
      <article v-for="item in pendingRows" :key="item.rule.id" class="question-card">
          <strong>{{ item.rule.title }}：{{ item.issues.length ? '还需要补充什么？' : '已补齐，保存后生效' }}</strong>
          <ul><li v-for="issue in item.issues" :key="issue">{{ issue }}</li></ul>
          <el-input v-if="item.rule.source_type !== 'explicit' || visuals.some(v => item.rule.source_material_ids.includes(v.id) && v.status !== 'read')" v-model="item.rule.review_note" type="textarea" :disabled="disabled || working || saving" :aria-label="`${item.rule.id} 处理说明`" placeholder="请写清按什么理解测试，以及依据是什么。" />
          <el-button link type="primary" @click="editingRule = item.rule">补充或修改规则</el-button>
          <el-button v-if="item.issues.some(x => x.includes('场景') || x.includes('操作示例'))" link type="primary" @click="tab = 'scenarios'">补充操作示例</el-button>
          <el-button link @click="editingRule = item.rule">本期不测并填写原因</el-button>
        </article>
      </el-tab-pane>
      <el-tab-pane :label="`图片与图表（${visuals.length}）`" name="images">
        <p class="hint">图片识别保留表格关系、流程箭头和原型文案；请对照原图核验，图文冲突在澄清问题中处理。</p>
        <el-empty v-if="!visuals.length" description="本次没有提取到图片" />
        <article v-for="visual in visuals" :key="visual.id" class="question-card">
          <div class="review-actions"><strong>{{ visual.id }} · {{ visual.location }}</strong><el-tag :type="visual.status === 'read' ? 'success' : 'warning'">{{ visual.status === 'read' ? '已识别 · 待人工核对' : visual.status === 'uncertain' ? '部分内容不确定' : '读取失败' }}</el-tag><el-button v-if="!visual.error" link type="primary" @click="showImage(visual)">对照原图</el-button></div>
          <div v-if="visual.text" class="visual-text" v-html="renderMarkdown(visual.text)" />
          <el-alert v-if="visual.uncertainties" :title="visual.uncertainties" type="warning" :closable="false" />
        </article>
      </el-tab-pane>
    </el-tabs>

    <div v-if="draft" class="confirm-area">
      <el-checkbox v-model="scopeReviewed" :disabled="disabled || working || saving">我同意用已明确的规则生成用例，待确认的内容暂不生成</el-checkbox>
      <el-input v-model="confirmationNote" type="textarea" :disabled="disabled || working || saving" :rows="2" placeholder="确认说明：资料缺失或图片识别不确定时，说明补充内容或本期排除范围" aria-label="验收确认说明" />
      <ul v-if="blockers.length" class="blockers"><li v-for="message in blockers" :key="message">{{ message }}</li></ul>
      <div class="review-actions"><el-button :loading="saving" :disabled="disabled || working || !dirty" @click="save">保存评审草稿</el-button><el-button type="primary" :loading="saving" :disabled="disabled || working || !scopeReviewed || !!blockers.length || !!baselineId" @click="confirm">确认本次生成范围（{{ confirmedCount }} 条）</el-button><span class="hint">本次使用 {{ confirmedCount }} 条；另有 {{ pendingCount }} 条待确认、{{ excludedCount }} 条本期不测。</span></div>
    </div>

    <el-drawer :model-value="!!editingRule" title="编辑验收规则" size="min(760px, 100vw)" @close="editingRule = null">
      <el-form v-if="editingRule" label-position="top" :disabled="disabled || working || saving">
        <el-form-item v-for="field in ruleFields" :key="field.key" :label="field.label"><el-input v-model="editingRule[field.key]" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" @input="editingRule.status = 'pending'" /></el-form-item>
        <el-form-item label="依据性质"><el-select v-model="editingRule.source_type" @change="editingRule.status = 'pending'"><el-option value="explicit" label="原文已说清楚" /><el-option value="inferred" label="资料未说清楚" /></el-select></el-form-item>
        <el-form-item label="关联图片"><el-select v-model="editingRule.source_material_ids" multiple @change="editingRule.status = 'pending'"><el-option v-for="visual in visuals" :key="visual.id" :value="visual.id" :label="`${visual.id} · ${visual.location}`" /></el-select></el-form-item>
        <el-form-item label="必须验证的验收条件">
          <div class="criteria-edit"><div v-for="criterion in editingRule.criteria" :key="criterion.id"><span class="hint">{{ criterion.id }}</span><el-input v-model="criterion.text" type="textarea" :rows="2" @input="editingRule.status = 'pending'" /></div><el-button size="small" @click="addCriterion">增加验收条件</el-button></div>
        </el-form-item>
        <el-form-item label="处理说明 / 这次不测的原因"><el-input v-model="editingRule.review_note" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="这次要不要测"><el-button v-if="editingRule.status === 'excluded'" @click="includeRule(editingRule)">恢复到本次范围</el-button><el-button v-else @click="editingRule.status = 'excluded'">本期不测（请填写原因）</el-button><p>内容完整且没有疑问时自动纳入，无需另点确认。</p></el-form-item>
      </el-form>
      <template #footer><el-button @click="editingRule = null">完成编辑</el-button></template>
    </el-drawer>
    <el-dialog v-model="imageOpen" title="原图与识别内容" width="min(1100px, 94vw)" @closed="clearImage">
      <div v-loading="imageLoading" class="image-comparison"><img v-if="imageUrl" :src="imageUrl" alt="需求原图" /><div v-if="imageReading" class="visual-text" v-html="renderMarkdown(imageReading.text || imageReading.uncertainties)" /></div>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { analyzeRequirement, listRequirementAnalyses, getRequirementAnalysis, retryRequirementAnalysis, getRequirementOutput, saveRequirementDraft, confirmRequirement, getRequirementImage, pollAiJob } from '@/api'
import AcceptanceScenarios from './AcceptanceScenarios.vue'
import { reviewRows, filterRules } from '@/utils/focusedReview'
import AiJobProgress from './AiJobProgress.vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({ projectId: Number, taskId: Number, requirement: { type: String, default: '' }, provider: String,
  sourceId: Number, sourceUrl: String, sourceTitle: String, inputType: String, sourceWarnings: { type: Array, default: () => [] },
  materials: { type: Array, default: () => [] }, disabled: Boolean, available: Boolean, analysisId: Number, embedded: Boolean })
const emit = defineEmits(['ready', 'busy', 'restore', 'loaded'])
const analysis = ref(null), draft = ref(null), history = ref([]), historyId = ref(null)
const liveJob = ref(null)
const savedOutputOpen = ref(false), savedOutputId = ref(null), savedOutputText = ref(''), savedOutputError = ref(''), savedOutputLoading = ref(false)
let outputVersion = 0
const savedOutputs = computed(() => analysis.value?.saved_outputs || [])
const canRetry = computed(() => !working.value && !draft.value && analysis.value && (analysis.value.can_retry || ['failed', 'cancelled'].includes(analysis.value.status)))
const reusableImages = computed(() => visuals.value.filter(v => ['read', 'uncertain'].includes(v.status) && v.text).length)
const completedParts = computed(() => new Set(savedOutputs.value.filter(p => p.status === 'done').map(p => p.part_key)).size)
const outputLabel = (key, id) => key === 'analysis' ? '需求规则' : key?.startsWith('scenes-v2-') ? `具体场景 · 返回记录 #${id}` : key?.startsWith('scenes-') ? `具体场景 ${key.slice(7)}` : key
const working = ref(false), saving = ref(false), dirty = ref(false), error = ref(''), baselineId = ref(null)
const scopeReviewed = ref(false), confirmationNote = ref(''), tab = ref('questions'), editingRule = ref(null)
const imageOpen = ref(false), imageLoading = ref(false), imageUrl = ref(''), imageReading = ref(null)
let version = 0, historyVersion = 0, controller = null, hydrating = false, restoring = false, disposed = false, imageVersion = 0
const changeLabels = { scope: '本期范围', module: '模块', platform: '平台', condition: '前提', action: '操作', expected: '预期结果', forbidden: '禁止行为', boundaries: '边界', evidence: '验证方式', criteria: '验收条件' }
const overviewFields = [{ key: 'summary', label: '产品理解：用户、入口与最终结果' }, { key: 'scope', label: '本期范围与平台' }, { key: 'out_of_scope', label: '本期不做什么' }, { key: 'flow', label: '关键流程 / 判定顺序' }]
const ruleFields = [{ key: 'title', label: '要测试的事情' }, { key: 'module', label: '模块' }, { key: 'platform', label: '适用平台' }, { key: 'condition', label: '什么时候 / 什么情况下' }, { key: 'action', label: '用户做什么，或发生什么事' }, { key: 'expected', label: '应该看到什么结果' }, { key: 'forbidden', label: '不得发生的结果' }, { key: 'boundaries', label: '边界与例外' }, { key: 'evidence', label: '如何验证 / 缺少的测试条件' }, { key: 'source_section', label: '原文章节 / 图片位置' }, { key: 'source_quote', label: '原文摘录' }]
const visuals = computed(() => analysis.value?.visual_readings || [])
const warnings = computed(() => [...(analysis.value?.source_info?.warnings || props.sourceWarnings), ...visuals.value.filter(v => v.status !== 'read').map(v => `${v.id}：${v.uncertainties}`)])
const ruleFilter = ref('all'), showAnswered = ref(false)
const rows = computed(() => baselineId.value && !dirty.value ? (draft.value?.rules || []).map(rule => ({rule, status:rule.status, issues:[]})) : reviewRows(draft.value, visuals.value))
const visibleRules = computed(() => filterRules(rows.value, ruleFilter.value).map(row => row.rule))
const stateOf = rule => rows.value.find(row => row.rule.id === rule.id)?.status || rule.status
const confirmedCount = computed(() => rows.value.filter(r => r.status === 'confirmed').length)
const pendingCount = computed(() => rows.value.filter(r => r.status === 'pending').length)
const excludedCount = computed(() => rows.value.filter(r => r.status === 'excluded').length)
const unansweredCount = computed(() => draft.value?.questions.filter(q => !q.answer.trim() && q.blocking && (!q.rule_ids.length || q.rule_ids.some(id => rows.value.some(r => r.rule.id === id && r.status !== 'excluded')))).length || 0)
const attentionCount = computed(() => unansweredCount.value + rows.value.filter(r => r.status === 'pending' && r.issues.some(issue => !issue.startsWith('还有影响'))).length)
const questionsToShow = computed(() => (draft.value?.questions || []).filter(q => showAnswered.value || (q.blocking && (!q.answer.trim() || !analysis.value?.draft?.questions.find(saved => saved.id === q.id)?.answer?.trim()) && (!q.rule_ids.length || q.rule_ids.some(id => rows.value.some(r => r.rule.id === id && r.status !== 'excluded'))))))
const pendingRows = computed(() => rows.value.filter(r => r.status === 'pending' || (dirty.value && r.status !== 'excluded' && analysis.value?.draft?.rules.find(saved => saved.id === r.rule.id)?.status === 'pending')))
const blockers = computed(() => {
  if (!draft.value) return []
  const messages = []
  if (!confirmedCount.value) messages.push('还没有可以生成用例的明确规则，请先处理一个问题。')
  for (const r of draft.value.rules) if (r.status === 'excluded' && !r.review_note.trim()) messages.push(`${r.title}：请说明这次为什么不测。`)
  if (warnings.value.length && !confirmationNote.value.trim()) messages.push('资料还有缺失，请在下方说明如何处理，或这次不测哪些内容。')
  return messages
})
function filterPending() { tab.value = 'rules'; ruleFilter.value = 'pending' }
function includeRule(rule) { rule.status = 'pending' }
const statusLabel = status => ({ pending: '排队中', running: '分析中', done: '可评审', failed: '失败', cancelled: '已取消' }[status] || status)
const messageOf = e => e?.response?.data?.msg || e?.message || '操作失败，请重试'

watch([working, saving], ([a, b]) => emit('busy', a || b))
watch(draft, () => { if (!hydrating) { dirty.value = true; baselineId.value = null; scopeReviewed.value = false; emit('ready', null) } }, { deep: true })
watch(() => [props.projectId, props.taskId, props.requirement, props.sourceId, props.sourceUrl], () => {
  if (restoring) return
  version++; controller?.abort(); working.value = false; analysis.value = null; draft.value = null
  liveJob.value = null
  outputVersion++; savedOutputOpen.value = false; savedOutputText.value = ''
  baselineId.value = null; editingRule.value = null; error.value = ''; scopeReviewed.value = false; confirmationNote.value = ''; emit('ready', null)
})
watch(() => [props.projectId, props.taskId], loadHistory, { immediate: true })

watch(() => props.analysisId, id => { if (id) restoreAnalysis(id) }, { immediate: true })

async function loadHistory() {
  const id = ++historyVersion
  history.value = []; historyId.value = null
  if (!props.projectId || !props.taskId) return
  try { const data = await listRequirementAnalyses({ project_id: props.projectId, task_id: props.taskId }); if (!disposed && id === historyVersion) history.value = data }
  catch { if (!disposed && id === historyVersion) error.value = '历史分析加载失败，可重新选择任务重试' }
}
async function replaceAnalysis(data) {
  hydrating = true
  analysis.value = data; draft.value = data.draft ? structuredClone(data.draft) : null
  liveJob.value = { id: data.job_id, status: data.status, progress: data.progress || liveJob.value?.progress, error: data.error }
  baselineId.value = data.draft && data.baseline_id || null; dirty.value = false; scopeReviewed.value = !!baselineId.value
  if (baselineId.value) confirmationNote.value = data.confirmation_note || ''
  emit('ready', baselineId.value); emit('loaded', data)
  await nextTick(); hydrating = false
}
async function restoreAnalysis(id) {
  if (!id) return
  const current = ++version
  controller?.abort(); working.value = false; liveJob.value = null
  outputVersion++; savedOutputOpen.value = false; savedOutputText.value = ''
  error.value = ''; saving.value = true
  try {
    const data = await getRequirementAnalysis(id)
    if (current !== version || disposed) return
    restoring = true; emit('restore', data); await nextTick()
    await replaceAnalysis(data); restoring = false
    if (data.error) error.value = data.error
    else if (!data.draft && data.job_id && ['pending', 'running'].includes(data.status)) {
      saving.value = false; working.value = true
      await waitAnalysis(data.job_id, data.id, current)
    }
  } catch (e) { if (current === version && !disposed) error.value = messageOf(e) }
  finally { restoring = false; if (current === version) saving.value = false }
}
async function waitAnalysis(jobId, analysisId, current) {
  const waiting = new AbortController()
  controller = waiting
  const timeout = setTimeout(() => waiting.abort(), 35 * 60 * 1000)
  try {
    await pollAiJob(jobId, { signal: waiting.signal, onTick: job => {
      if (current !== version || disposed) return
      liveJob.value = { ...job, progress: job.progress || liveJob.value?.progress }
      if (analysis.value) analysis.value.status = job.status
    } })
    const data = await getRequirementAnalysis(analysisId)
    if (current === version && !disposed) { await replaceAnalysis(data); ElMessage.success('需求分析完成，请核对范围、图片和验收规则') }
  } catch (e) {
    if (current === version && !disposed) {
      error.value = waiting.signal.aborted ? '已停止等待，后台仍可能继续；可从历史分析恢复查看。' : messageOf(e)
      try { const data = await getRequirementAnalysis(analysisId); if (current === version) await replaceAnalysis(data) } catch { /* Keep the recoverable task id. */ }
    }
  } finally {
    clearTimeout(timeout)
    if (current === version && !disposed) { working.value = false; controller = null; await loadHistory() }
  }
}
async function startAnalysis() {
  const current = ++version
  liveJob.value = { status: 'pending' }
  error.value = ''; working.value = true; baselineId.value = null; emit('ready', null)
  try {
    const result = await analyzeRequirement({ project_id: props.projectId, task_id: props.taskId, requirement: props.requirement, provider: props.provider,
      source_id: props.sourceId || null, source_url: props.sourceUrl || '', source_title: props.sourceTitle || '', input_type: props.inputType || 'text', source_warnings: props.sourceId ? [] : props.sourceWarnings.slice(0, 100) })
    if (current !== version || disposed) return
    analysis.value = { id: result.analysis_id, job_id: result.job_id, status: 'pending' }; draft.value = null
    await waitAnalysis(result.job_id, result.analysis_id, current)
  } catch (e) { if (current === version && !disposed) { error.value = messageOf(e); working.value = false } }
}
async function resumeAnalysis() { working.value = true; error.value = ''; await waitAnalysis(analysis.value.job_id, analysis.value.id, ++version) }
async function retryAnalysis() {
  if (!canRetry.value) return
  const current = ++version, id = analysis.value.id
  working.value = true; error.value = ''; liveJob.value = { status: 'pending' }
  try {
    const result = await retryRequirementAnalysis(id)
    if (current !== version || disposed) return
    analysis.value.job_id = result.job_id; analysis.value.status = 'pending'; analysis.value.can_retry = false
    await waitAnalysis(result.job_id, id, current)
  } catch (e) { if (current === version && !disposed) { error.value = messageOf(e); working.value = false } }
}
function viewSavedOutput() { savedOutputOpen.value = true; savedOutputId.value = savedOutputs.value[0]?.id; loadSavedOutput() }
async function loadSavedOutput() {
  const request = ++outputVersion, id = analysis.value?.id
  savedOutputLoading.value = true; savedOutputText.value = ''; savedOutputError.value = ''
  try {
    const result = await getRequirementOutput(id, savedOutputId.value)
    if (request === outputVersion && !disposed && analysis.value?.id === id) { savedOutputText.value = result.raw; savedOutputError.value = result.error || '' }
  } catch (e) { if (request === outputVersion && !disposed) savedOutputError.value = messageOf(e) }
  finally { if (request === outputVersion && !disposed) savedOutputLoading.value = false }
}
function stopWaiting() { controller?.abort() }
async function save() {
  if (!draft.value || !analysis.value || !dirty.value) return true
  saving.value = true; error.value = ''
  const reviewed = scopeReviewed.value
  try { const data = await saveRequirementDraft(analysis.value.id, { revision: analysis.value.revision, draft: draft.value }); await replaceAnalysis(data); scopeReviewed.value = reviewed; return true }
  catch (e) { error.value = messageOf(e); return false }
  finally { saving.value = false }
}
async function confirm() {
  if (blockers.value.length || !scopeReviewed.value) return
  if (!(await save())) return
  if (blockers.value.length) { error.value = '修改已保存，还有内容没说清楚，请查看澄清问题'; return }
  saving.value = true; error.value = ''
  try {
    const data = await confirmRequirement(analysis.value.id, { revision: analysis.value.revision, source_hash: analysis.value.source_hash, scope_reviewed: scopeReviewed.value, confirmation_note: confirmationNote.value })
    baselineId.value = data.baseline_id; emit('ready', data.baseline_id); ElMessage.success('验收版本已保存，可以生成用例')
  } catch (e) { error.value = messageOf(e) }
  finally { saving.value = false }
}
function resetQuestionRules(q) { for (const r of draft.value.rules) if (!q.rule_ids.length || q.rule_ids.includes(r.id)) r.status = 'pending' }
function addRule() {
  let i = 1; while (draft.value.rules.some(r => r.id === `R${i}`)) i++
  const rule = { id: `R${i}`, title: '补充验收规则', module: '', platform: '', condition: '', action: '', expected: '', forbidden: '', boundaries: '', evidence: '', source_type: 'inferred', source_quote: '', source_section: '', source_material_ids: [], criteria: [], status: 'pending', review_note: '' }
  draft.value.rules.push(rule); editingRule.value = rule
}
function addCriterion() {
  const r = editingRule.value
  let i = 1; while (draft.value.rules.some(rule => rule.criteria.some(c => c.id === `${r.id}-C${i}`))) i++
  r.criteria.push({ id: `${r.id}-C${i}`, text: '' }); r.status = 'pending'
}
async function showImage(reading) {
  if (!reading) return
  clearImage(); imageReading.value = reading; imageOpen.value = true; imageLoading.value = true
  const current = ++imageVersion
  try { const blob = await getRequirementImage(analysis.value.source_id, reading.id); if (current === imageVersion && !disposed) imageUrl.value = URL.createObjectURL(blob) }
  catch (e) { ElMessage.error(messageOf(e)) }
  finally { if (current === imageVersion) imageLoading.value = false }
}
function clearImage() { imageVersion++; if (imageUrl.value) URL.revokeObjectURL(imageUrl.value); imageUrl.value = ''; imageReading.value = null }
onBeforeUnmount(() => { disposed = true; version++; historyVersion++; controller?.abort(); clearImage(); emit('busy', false) })
</script>

<style scoped>
.requirement-review { margin-top:24px; padding:20px; border:1px solid var(--el-border-color); border-radius:12px; background:var(--el-bg-color); }
.review-focus { padding:16px; margin:16px 0; background:var(--el-fill-color-light); border-radius:10px; }
.review-focus h4 { margin:0 0 8px; }
.review-steps { margin-bottom:20px; }
.review-head,.review-actions { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.review-head { justify-content:space-between; align-items:flex-start; }
h3 { margin:0; font-size:16px; }.review-head p,.hint { color:var(--el-text-color-secondary); font-size:12px; line-height:1.6; }
.review-actions { margin:12px 0; }.review-tabs { margin-top:16px; }.rule-title { border:0; padding:0; background:none; color:var(--el-color-primary); text-align:left; cursor:pointer; font:inherit; font-weight:600; }
.question-card { border:1px solid var(--el-border-color-lighter); border-radius:8px; padding:16px; margin:12px 0; line-height:1.65; overflow-wrap:anywhere; }
.evidence { white-space:pre-wrap; background:var(--el-fill-color-light); padding:10px; }.confirm-area { margin-top:16px; padding-top:16px; border-top:1px solid var(--el-border-color-lighter); }
.confirm-area .el-checkbox { height:auto; white-space:normal; margin-bottom:12px; }.blockers { color:var(--el-color-warning-dark-2); font-size:13px; line-height:1.8; }
.criteria-edit { display:grid; gap:10px; width:100%; }.image-comparison { display:grid; grid-template-columns:1fr 1fr; gap:20px; min-height:100px; }.image-comparison img { width:100%; height:auto; object-fit:contain; align-self:start; }
.visual-text { max-height:440px; overflow:auto; font-size:13px; }.visual-text :deep(table) { border-collapse:collapse; }.visual-text :deep(td),.visual-text :deep(th) { padding:6px; border:1px solid var(--el-border-color); }
.requirement-review :deep(.el-alert) { margin:12px 0; }.requirement-review :deep(.el-table .cell) { overflow-wrap:anywhere; }
@media(max-width:760px) { .requirement-review { padding:12px; }.image-comparison { grid-template-columns:1fr; }.review-head .el-select { width:100%!important; }.review-focus { padding:16px; margin:16px 0; background:var(--el-fill-color-light); border-radius:10px; }
.review-focus h4 { margin:0 0 8px; }
.review-steps { padding:12px 8px; } }
</style>

<style scoped>
.recovery-info { padding: 12px 16px; margin: 12px 0; border: 1px solid #dce5f5; border-radius: 8px; background: #f8faff; font-size: 14px; line-height: 1.7; }
.recovery-info .el-button { max-width: 100%; height: auto; white-space: normal; line-height: 1.6; }
.recovery-info .el-button :deep(span) { overflow-wrap: anywhere; }
.saved-output { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 55vh; overflow: auto; font-size: 13px; line-height: 1.7; padding: 12px; background: #f6f8fb; }
</style>

<style scoped>
.rule-filters{margin:8px 0 16px;display:flex;flex-wrap:wrap;gap:6px}.question-card{scroll-margin-top:80px}
</style>
