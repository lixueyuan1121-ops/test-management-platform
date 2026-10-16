<template>
  <el-dialog :model-value="true" title="编写任务日报" width="min(920px, 96vw)" :before-close="close" :close-on-click-modal="false">
    <div class="digest-toolbar">
      <span>来源 {{ draft.task_ids.length }} 个任务 · {{ draft.filter_summary }}</span>
      <el-button size="small" @click="showHistory">已保存日报</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false"><el-button link @click="generate">重新生成</el-button></el-alert>
    <el-skeleton v-if="loading" :rows="8" animated />
    <el-form v-else label-width="72px">
      <el-form-item label="日报日期"><el-date-picker v-model="draft.report_date" type="date" value-format="YYYY-MM-DD" :clearable="false" /></el-form-item>
      <el-form-item label="标题"><el-input v-model="draft.title" maxlength="255" /></el-form-item>
      <el-tabs v-model="tab">
        <el-tab-pane label="编写" name="edit"><el-input v-model="draft.content" type="textarea" :rows="18" maxlength="60000" aria-label="日报正文" placeholder="根据当前筛选任务生成，可继续补充和修改" /></el-tab-pane>
        <el-tab-pane label="预览" name="preview"><div class="md-body digest-preview" v-html="renderMarkdown(draft.content)" /></el-tab-pane>
      </el-tabs>
    </el-form>
    <template #footer>
      <span v-if="savedAt" class="saved-at">已保存 {{ savedAt }}</span>
      <el-button @click="close">关闭</el-button>
      <el-button :disabled="!draft.content || loading" @click="exportDraft('md')">导出 Markdown</el-button>
      <el-button :disabled="!draft.content || loading" @click="exportDraft('txt')">导出文本</el-button>
      <el-button type="primary" :disabled="loading || !draft.content" :loading="saving" @click="save">保存日报</el-button>
    </template>
    <el-dialog v-model="historyVisible" title="我的已保存日报" width="min(720px, 92vw)" append-to-body>
      <el-alert v-if="historyError" title="历史日报加载失败" type="error" :closable="false"><el-button link @click="showHistory">重试</el-button></el-alert>
      <el-table :data="history" v-loading="historyLoading" empty-text="还没有保存的日报">
        <el-table-column prop="report_date" label="日期" width="120" />
        <el-table-column prop="title" label="标题" min-width="200" />
        <el-table-column label="操作" width="80"><template #default="{row}"><el-button link type="primary" @click="restore(row)">打开</el-button></template></el-table-column>
      </el-table>
    </el-dialog>
  </el-dialog>
</template>
<script setup>
import { reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { generateTaskDigest, saveTaskDigest, listTaskDigests } from '@/api/taskLedger'
import { downloadText } from '@/utils/taskLedger'
import { renderMarkdown } from '@/utils/markdown'
const props = defineProps({ context: { type: Object, required: true } })
const emit = defineEmits(['close'])
const draft = reactive({ id: null, ...props.context, task_ids: [...props.context.task_ids], title: '', content: '' })
const loading = ref(false), saving = ref(false), error = ref(''), savedAt = ref(''), tab = ref('edit')
const history = ref([]), historyVisible = ref(false), historyLoading = ref(false), historyError = ref(false)
let savedSnapshot = ''
const snapshot = () => JSON.stringify([draft.title, draft.content, draft.report_date])
async function allowDiscard() {
  if (!draft.content || snapshot() === savedSnapshot) return true
  try { await ElMessageBox.confirm('当前日报有未保存的内容，是否放弃这些修改？', '未保存的日报', { confirmButtonText: '放弃修改', cancelButtonText: '继续编写' }); return true } catch { return false }
}
async function close() { if (!saving.value && await allowDiscard()) emit('close') }
async function generate() {
  loading.value = true; error.value = ''
  try {
    Object.assign(draft, await generateTaskDigest(props.context))
  } catch { error.value = '日报生成失败，请重试' }
  finally { loading.value = false }
}
async function save() {
  if (!draft.title.trim() || !draft.content.trim() || !draft.report_date) { ElMessage.warning('请填写日期、标题和正文'); return }
  saving.value = true
  try {
    const result = await saveTaskDigest(draft)
    Object.assign(draft, result); savedSnapshot = snapshot(); savedAt.value = new Date().toLocaleTimeString()
    ElMessage.success('日报已保存')
  } finally { saving.value = false }
}
function exportDraft(ext) {
  downloadText(draft.content, `${draft.title || '测试日报'}.${ext}`, ext === 'md' ? 'text/markdown;charset=utf-8' : undefined)
}
async function showHistory() {
  historyVisible.value = true; historyLoading.value = true; historyError.value = false
  try { history.value = await listTaskDigests(props.context.project_id) }
  catch { historyError.value = true }
  finally { historyLoading.value = false }
}
async function restore(row) {
  if (!await allowDiscard()) return
  Object.assign(draft, row); savedSnapshot = snapshot(); savedAt.value = ''; error.value = ''; historyVisible.value = false
}
onMounted(() => props.context.history ? showHistory() : generate())
</script>
<style scoped>
.digest-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; color:var(--tech-muted); }
.digest-preview { max-height:55vh; overflow:auto; padding:12px; background:var(--tech-panel-2); }
.saved-at { margin-right:12px; color:var(--tech-muted); font-size:12px; }
</style>
