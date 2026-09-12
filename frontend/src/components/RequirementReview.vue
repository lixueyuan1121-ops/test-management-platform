<template>
  <section class="requirement-review" aria-label="需求澄清与验收确认">
    <el-steps :active="baselineId ? 3 : draft ? 2 : 1" simple class="review-steps">
      <el-step title="导入需求" /><el-step title="澄清与确认" /><el-step title="生成用例" />
    </el-steps>
    <div class="review-head">
      <div><h3>需求澄清与验收确认</h3><p>先确认本期做什么、如何验收，再生成可追溯的测试用例。</p></div>
      <el-select v-model="historyId" placeholder="恢复历史需求分析" clearable :disabled="working || disabled" @change="restoreAnalysis" style="width:260px">
        <el-option v-for="item in history" :key="item.id" :value="item.id" :label="`#${item.id} · ${item.source_title || '文本需求'} · ${statusLabel(item.status)}`" />
      </el-select>
    </div>
    <div class="review-actions">
      <el-button type="primary" :disabled="disabled || !available || !projectId || !taskId || !requirement.trim() || working" @click="startAnalysis">{{ draft ? '重新分析需求' : '分析需求' }}</el-button>
      <el-button v-if="analysis && !draft && !working && ['pending', 'running'].includes(analysis.status)" @click="resumeAnalysis">继续查看分析进度</el-button>
      <el-button v-if="working" @click="stopWaiting">停止等待</el-button>
      <span v-if="!taskId" class="hint">请先选择关联任务</span>
      <span v-if="working" role="status">{{ jobStatus === 'pending' ? '等待分析…' : `正在识别图片并整理验收规则${materialCount ? `（${materialCount} 张图片）` : ''}…` }}</span>
      <el-tag v-if="baselineId" type="success">已确认版本 #{{ baselineId }}</el-tag>
      <el-tag v-else-if="draft" type="warning">{{ dirty ? '修改尚未保存' : '待确认验收规则' }}</el-tag>
    </div>
    <el-progress v-if="working" :percentage="100" :indeterminate="true" :show-text="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="warnings.length" type="warning" :closable="false" show-icon title="资料范围需要核对">
      <ul><li v-for="(warning, i) in warnings" :key="i">{{ warning }}</li></ul>
    </el-alert>

    <el-tabs v-if="draft || visuals.length" v-model="tab" class="review-tabs">
      <el-tab-pane v-if="draft" label="本期范围" name="summary">
        <el-form label-position="top" :disabled="disabled || working || saving">
          <el-form-item v-for="field in overviewFields" :key="field.key" :label="field.label">
            <el-input v-model="draft[field.key]" type="textarea" :autosize="{ minRows: 2, maxRows: 7 }" />
          </el-form-item>
        </el-form>
      </el-tab-pane>
      <el-tab-pane v-if="draft" :label="`验收规则（${draft.rules.length}）`" name="rules">
        <div class="review-actions"><span>已确认 {{ confirmedCount }} · 待确认 {{ pendingCount }} · 本期排除 {{ excludedCount }}</span><el-button size="small" :disabled="disabled || saving || working" @click="addRule">补充规则</el-button></div>
        <el-table :data="draft.rules" border size="small" row-key="id">
          <el-table-column prop="id" label="编号" width="90" />
          <el-table-column label="规则与验收条件" min-width="290">
            <template #default="{ row }"><button class="rule-title" @click="editingRule = row">{{ row.title }}</button><p class="hint">{{ row.platform }} {{ row.module }} · {{ row.criteria.length }} 个验收条件</p><div>{{ row.expected || '预期结果尚待澄清' }}</div></template>
          </el-table-column>
          <el-table-column label="依据" width="110"><template #default="{ row }"><el-tag :type="row.source_type === 'explicit' ? 'info' : 'warning'" size="small">{{ row.source_type === 'explicit' ? '原文明确' : '推导建议' }}</el-tag></template></el-table-column>
          <el-table-column label="评审" width="155"><template #default="{ row }"><el-select v-model="row.status" :disabled="disabled || working || saving" :aria-label="`${row.id} 评审状态`"><el-option label="待确认" value="pending" /><el-option label="确认本期验收" value="confirmed" /><el-option label="本期排除" value="excluded" /></el-select></template></el-table-column>
        </el-table>
        <p class="hint">点击规则可编辑条件、预期与原文依据。推导规则确认、本期排除、图片不确定时，请填写说明。</p>
      </el-tab-pane>
      <el-tab-pane v-if="draft" :label="`澄清问题（${unansweredCount} 待处理）`" name="questions">
        <el-empty v-if="!draft.questions.length" description="未发现需要拍板的问题，仍请核对规则与原文" />
        <article v-for="question in draft.questions" :key="question.id" class="question-card">
          <strong>{{ question.id }} · {{ question.question }}</strong>
          <p class="hint">{{ question.rule_ids.length ? `影响：${question.rule_ids.join('、')}` : '影响：本期整体范围' }} · {{ question.blocking ? '影响相关规则确认' : '补充建议' }}</p>
          <p class="evidence">{{ question.evidence }}</p>
          <ul><li v-for="option in question.options" :key="option">{{ option }}</li></ul>
          <el-input v-model="question.answer" type="textarea" :disabled="disabled || working || saving" :aria-label="`${question.id} 处理结论`" placeholder="填写产品决定及适用范围，并把结论落实到关联规则中" @input="resetQuestionRules(question)" />
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
      <el-checkbox v-model="scopeReviewed" :disabled="disabled || working || saving">我已核对本期范围、资料完整性及选中的验收规则</el-checkbox>
      <el-input v-model="confirmationNote" type="textarea" :disabled="disabled || working || saving" :rows="2" placeholder="确认说明：资料缺失或图片识别不确定时，说明补充内容或本期排除范围" aria-label="验收确认说明" />
      <ul v-if="blockers.length" class="blockers"><li v-for="message in blockers.slice(0, 8)" :key="message">{{ message }}</li><li v-if="blockers.length > 8">还有 {{ blockers.length - 8 }} 项，请逐条处理</li></ul>
      <div class="review-actions"><el-button :loading="saving" :disabled="disabled || working || !dirty" @click="save">保存评审草稿</el-button><el-button type="primary" :loading="saving" :disabled="disabled || working || !scopeReviewed || !!blockers.length || !!baselineId" @click="confirm">确认验收规则</el-button><span class="hint">仅生成已确认规则；待澄清规则保留并单独列出。</span></div>
    </div>

    <el-drawer :model-value="!!editingRule" title="编辑验收规则" size="min(760px, 100vw)" @close="editingRule = null">
      <el-form v-if="editingRule" label-position="top" :disabled="disabled || working || saving">
        <el-form-item v-for="field in ruleFields" :key="field.key" :label="field.label"><el-input v-model="editingRule[field.key]" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" @input="editingRule.status = 'pending'" /></el-form-item>
        <el-form-item label="依据性质"><el-select v-model="editingRule.source_type" @change="editingRule.status = 'pending'"><el-option value="explicit" label="原文明确" /><el-option value="inferred" label="推导建议" /></el-select></el-form-item>
        <el-form-item label="关联图片"><el-select v-model="editingRule.source_material_ids" multiple @change="editingRule.status = 'pending'"><el-option v-for="visual in visuals" :key="visual.id" :value="visual.id" :label="`${visual.id} · ${visual.location}`" /></el-select></el-form-item>
        <el-form-item label="必须验证的验收条件">
          <div class="criteria-edit"><div v-for="criterion in editingRule.criteria" :key="criterion.id"><span class="hint">{{ criterion.id }}</span><el-input v-model="criterion.text" type="textarea" :rows="2" @input="editingRule.status = 'pending'" /></div><el-button size="small" @click="addCriterion">增加验收条件</el-button></div>
        </el-form-item>
        <el-form-item label="评审说明 / 本期排除原因"><el-input v-model="editingRule.review_note" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="评审状态"><el-radio-group v-model="editingRule.status"><el-radio value="pending">待确认</el-radio><el-radio value="confirmed">确认本期验收</el-radio><el-radio value="excluded">本期排除</el-radio></el-radio-group></el-form-item>
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
import { analyzeRequirement, listRequirementAnalyses, getRequirementAnalysis, saveRequirementDraft, confirmRequirement, getRequirementImage, pollAiJob } from '@/api'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({ projectId: Number, taskId: Number, requirement: { type: String, default: '' }, provider: String,
  sourceId: Number, sourceUrl: String, sourceTitle: String, inputType: String, sourceWarnings: { type: Array, default: () => [] },
  materials: { type: Array, default: () => [] }, disabled: Boolean, available: Boolean })
const emit = defineEmits(['ready', 'busy', 'restore'])
const analysis = ref(null), draft = ref(null), history = ref([]), historyId = ref(null)
const working = ref(false), saving = ref(false), dirty = ref(false), error = ref(''), jobStatus = ref(''), baselineId = ref(null)
const scopeReviewed = ref(false), confirmationNote = ref(''), tab = ref('summary'), editingRule = ref(null)
const imageOpen = ref(false), imageLoading = ref(false), imageUrl = ref(''), imageReading = ref(null)
let version = 0, historyVersion = 0, controller = null, hydrating = false, restoring = false, disposed = false, imageVersion = 0
const overviewFields = [{ key: 'summary', label: '产品理解：用户、入口与最终结果' }, { key: 'scope', label: '本期范围与平台' }, { key: 'out_of_scope', label: '本期不做什么' }, { key: 'flow', label: '关键流程 / 判定顺序' }]
const ruleFields = [{ key: 'title', label: '规则标题' }, { key: 'module', label: '模块' }, { key: 'platform', label: '适用平台' }, { key: 'condition', label: '前提条件' }, { key: 'action', label: '触发操作 / 事件' }, { key: 'expected', label: '应发生的结果' }, { key: 'forbidden', label: '不得发生的结果' }, { key: 'boundaries', label: '边界与例外' }, { key: 'evidence', label: '如何验证 / 缺少的测试条件' }, { key: 'source_section', label: '原文章节 / 图片位置' }, { key: 'source_quote', label: '原文摘录' }]
const visuals = computed(() => analysis.value?.visual_readings || [])
const materialCount = computed(() => analysis.value?.materials?.length || props.materials.length)
const warnings = computed(() => [...(analysis.value?.source_info?.warnings || props.sourceWarnings), ...visuals.value.filter(v => v.status !== 'read').map(v => `${v.id}：${v.uncertainties}`)])
const confirmedCount = computed(() => draft.value?.rules.filter(r => r.status === 'confirmed').length || 0)
const pendingCount = computed(() => draft.value?.rules.filter(r => r.status === 'pending').length || 0)
const excludedCount = computed(() => draft.value?.rules.filter(r => r.status === 'excluded').length || 0)
const unansweredCount = computed(() => draft.value?.questions.filter(q => !q.answer.trim()).length || 0)
const blockers = computed(() => {
  if (!draft.value) return []
  const messages = [], selected = draft.value.rules.filter(r => r.status === 'confirmed')
  if (!selected.length) messages.push('至少确认一条验收规则')
  for (const rule of draft.value.rules) {
    if (rule.status === 'excluded' && !rule.review_note.trim()) messages.push(`${rule.id}：请填写本期排除原因`)
    if (rule.status !== 'confirmed') continue
    if (!rule.condition.trim() || !rule.action.trim() || !rule.expected.trim() || !rule.criteria.length || rule.criteria.some(c => !c.text.trim())) messages.push(`${rule.id}：请补齐前提、操作、预期和验收条件`)
    if (rule.source_type === 'inferred' && !rule.review_note.trim()) messages.push(`${rule.id}：推导规则需要确认依据或假设说明`)
    if (visuals.value.some(v => rule.source_material_ids.includes(v.id) && v.status !== 'read') && !rule.review_note.trim()) messages.push(`${rule.id}：请说明关联图片的核对结论`)
  }
  for (const q of draft.value.questions) if (q.blocking && !q.answer.trim() && (!q.rule_ids.length || selected.some(r => q.rule_ids.includes(r.id)))) messages.push(`${q.id}：${q.question}`)
  if (warnings.value.length && !confirmationNote.value.trim()) messages.push('请在确认说明中记录资料缺口的处理或排除范围')
  return messages
})
const statusLabel = status => ({ pending: '排队中', running: '分析中', done: '可评审', failed: '失败', cancelled: '已取消' }[status] || status)
const messageOf = e => e?.response?.data?.msg || e?.message || '操作失败，请重试'

watch([working, saving], ([a, b]) => emit('busy', a || b))
watch(draft, () => { if (!hydrating) { dirty.value = true; baselineId.value = null; scopeReviewed.value = false; emit('ready', null) } }, { deep: true })
watch(() => [props.projectId, props.taskId, props.requirement, props.sourceId, props.sourceUrl], () => {
  if (restoring) return
  version++; controller?.abort(); working.value = false; analysis.value = null; draft.value = null
  baselineId.value = null; editingRule.value = null; error.value = ''; scopeReviewed.value = false; confirmationNote.value = ''; emit('ready', null)
})
watch(() => [props.projectId, props.taskId], loadHistory, { immediate: true })

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
  baselineId.value = data.baseline_id || null; dirty.value = false; scopeReviewed.value = !!data.baseline_id
  if (data.baseline_id) confirmationNote.value = data.confirmation_note || ''
  emit('ready', baselineId.value)
  await nextTick(); hydrating = false
}
async function restoreAnalysis(id) {
  if (!id) return
  const current = ++version
  error.value = ''; saving.value = true
  try {
    const data = await getRequirementAnalysis(id)
    if (current !== version || disposed) return
    restoring = true; emit('restore', data); await nextTick()
    await replaceAnalysis(data); restoring = false
    if (data.error) error.value = data.error
  } catch (e) { if (current === version && !disposed) error.value = messageOf(e) }
  finally { restoring = false; if (current === version) saving.value = false }
}
async function waitAnalysis(jobId, analysisId, current) {
  controller = new AbortController()
  const timeout = setTimeout(() => controller?.abort(), 35 * 60 * 1000)
  try {
    await pollAiJob(jobId, { signal: controller.signal, onTick: job => { if (current === version) jobStatus.value = job.status } })
    const data = await getRequirementAnalysis(analysisId)
    if (current === version && !disposed) { await replaceAnalysis(data); ElMessage.success('需求分析完成，请核对范围、图片和验收规则') }
  } catch (e) {
    if (current === version && !disposed) {
      error.value = controller?.signal.aborted ? '已停止等待，后台仍可能继续；可从历史分析恢复查看。' : messageOf(e)
      try { const data = await getRequirementAnalysis(analysisId); if (current === version) await replaceAnalysis(data) } catch { /* Keep the recoverable task id. */ }
    }
  } finally {
    clearTimeout(timeout)
    if (current === version && !disposed) { working.value = false; controller = null; await loadHistory() }
  }
}
async function startAnalysis() {
  const current = ++version
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
@media(max-width:760px) { .requirement-review { padding:12px; }.image-comparison { grid-template-columns:1fr; }.review-head .el-select { width:100%!important; }.review-steps { padding:12px 8px; } }
</style>
