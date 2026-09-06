<template>
  <div class="cmd-page">
    <el-card>
      <template #header>
        <div class="hd">
          <span>测试指挥官 · 用一句话问质量</span>
          <el-select v-model="projectId" size="small" style="width:200px" @change="onProject">
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </div>
      </template>

      <!-- 能力引导：你可以问我… -->
      <div v-if="hints.length" class="hints">
        <span class="hints-t">你可以问我：</span>
        <el-tag v-for="(h, i) in hints" :key="i" size="small" class="hint" type="info" effect="plain">{{ h }}</el-tag>
      </div>

      <!-- 对话流 -->
      <div ref="scrollRef" class="conv" v-loading="loading" element-loading-text="指挥官思考中…">
        <div v-if="!messages.length" class="empty">在下面输入你的问题，例如「v2.3 的回归风险如何？」</div>

        <div v-for="(m, i) in messages" :key="i" class="row" :class="m.role">
          <!-- 用户气泡 -->
          <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.text }}</div>

          <!-- 助理气泡 -->
          <div v-else class="bubble asst-bubble" :class="{ 'clarify-bubble': m.type === 'clarify', 'system-bubble': m.type === 'system' }">
            <!-- answer：Markdown 叙事 + 可选结构化数据 -->
            <template v-if="m.type === 'answer'">
              <div class="md-body" v-html="renderMarkdown(m.answer)" />
              <el-collapse v-if="m.data" class="data-collapse">
                <el-collapse-item title="查看数据" :name="'d' + i">
                  <pre class="data-pre">{{ prettyJson(m.data) }}</pre>
                </el-collapse-item>
              </el-collapse>
              <div v-if="m.provider" class="meta">引擎：{{ m.provider }}</div>
            </template>

            <!-- clarify：提示文本 -->
            <template v-else-if="m.type === 'clarify'">
              <el-icon class="clarify-ic"><InfoFilled /></el-icon><span>{{ m.answer }}</span>
            </template>

            <!-- draft：确认卡 -->
            <template v-else-if="m.type === 'draft'">
              <el-card class="draft-card" shadow="never">
                <div class="draft-h"><el-icon><Warning /></el-icon><b>需要确认后执行</b></div>
                <div class="draft-sum">{{ m.draft.human_summary }}</div>
                <el-collapse class="data-collapse">
                  <el-collapse-item title="查看请求详情" :name="'p' + i">
                    <div class="draft-ep">{{ m.draft.method }} {{ m.draft.endpoint }}</div>
                    <pre class="data-pre">{{ prettyJson(m.draft.payload) }}</pre>
                  </el-collapse-item>
                </el-collapse>
                <div class="draft-btns">
                  <el-button type="primary" size="small" :loading="m.executing" :disabled="m.done"
                             @click="confirmDraft(m)">{{ m.done ? '已执行' : '确认执行' }}</el-button>
                  <el-button size="small" :disabled="m.done" @click="cancelDraft(m)">取消</el-button>
                </div>
              </el-card>
            </template>

            <!-- system：执行结果等系统提示 -->
            <template v-else>
              <span>{{ m.text }}</span>
            </template>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-bar">
        <el-input v-model="question" type="textarea" :rows="2" resize="none"
                  placeholder="问我一个关于测试/质量的问题，回车发送（Shift+Enter 换行）"
                  @keydown.enter="onEnter" :disabled="loading" />
        <el-button type="primary" :loading="loading" :disabled="!canSend" @click="send">发送</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, Warning } from '@element-plus/icons-vue'
import { commanderAsk, commanderCapabilities } from '@/api'
import http from '@/api/http'
import { renderMarkdown } from '@/utils/markdown'
import { useAppStore } from '@/store/app'

const projects = ref([])
const projectId = ref(null)
const hints = ref([])
const messages = ref([])
const question = ref('')
const loading = ref(false)
const scrollRef = ref(null)

const canSend = computed(() => !loading.value && !!question.value.trim() && !!projectId.value)

const prettyJson = (v) => { try { return JSON.stringify(v, null, 2) } catch { return String(v) } }

async function init() {
  try { projects.value = await useAppStore().fetchProjects() } catch { projects.value = [] }
  if (projects.value.length) projectId.value = projects.value[0].id
  loadCapabilities()
}
async function loadCapabilities() {
  try {
    const d = await commanderCapabilities()
    // 取前几条能力说明做引导（read/analyze 类更适合直接问）
    hints.value = (d.capabilities || []).map((c) => c.desc).filter(Boolean).slice(0, 6)
  } catch { hints.value = [] }
}
function onProject() {
  // 切项目清空对话（避免跨项目上下文串味）；能力清单与项目无关，无需重取
  messages.value = []
}

async function scrollToEnd() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function onEnter(e) {
  if (e.shiftKey) return   // Shift+Enter 换行
  e.preventDefault()
  send()
}

async function send() {
  const q = question.value.trim()
  if (!q || loading.value || !projectId.value) return
  messages.value.push({ role: 'user', text: q })
  question.value = ''
  loading.value = true
  await scrollToEnd()
  try {
    const d = await commanderAsk({ project_id: projectId.value, question: q })
    const type = d?.type
    if (type === 'answer') {
      messages.value.push({ role: 'assistant', type: 'answer', answer: d.answer, data: d.data || null, provider: d.provider || '' })
    } else if (type === 'draft') {
      messages.value.push({ role: 'assistant', type: 'draft', intent: d.intent, draft: d.draft || {}, executing: false, done: false })
    } else if (type === 'clarify') {
      messages.value.push({ role: 'assistant', type: 'clarify', answer: d.answer })
    } else {
      messages.value.push({ role: 'assistant', type: 'clarify', answer: d?.answer || '未识别的回复' })
    }
  } catch (e) {
    // 拦截器已弹错，这里补一条气泡便于回看
    messages.value.push({ role: 'assistant', type: 'clarify', answer: '出错了，请重试' })
  } finally {
    loading.value = false
    await scrollToEnd()
  }
}

async function confirmDraft(m) {
  const draft = m.draft || {}
  const path = String(draft.endpoint || '').replace(/^\/api/, '')
  const method = (draft.method || 'POST').toLowerCase()
  m.executing = true
  try {
    // 当前草稿均为 POST；method 泛化处理但以 POST 为主。
    if (method === 'post') await http.post(path, draft.payload || {})
    else if (method === 'put') await http.put(path, draft.payload || {})
    else if (method === 'patch') await http.patch(path, draft.payload || {})
    else if (method === 'delete') await http.delete(path, { data: draft.payload || {} })
    else await http.post(path, draft.payload || {})
    m.done = true
    ElMessage.success('已执行')
    messages.value.push({ role: 'assistant', type: 'system', text: `已执行：${draft.action || draft.human_summary || '操作'}` })
    await scrollToEnd()
  } catch (e) {
    // 拦截器已提示错误，重新启用按钮让用户可重试
  } finally {
    m.executing = false
  }
}

function cancelDraft(m) {
  m.done = true
  messages.value.push({ role: 'assistant', type: 'system', text: '已取消该操作' })
  scrollToEnd()
}

init()
</script>

<style scoped>
.cmd-page { padding: 4px; }
.hd { display: flex; justify-content: space-between; align-items: center; }
.hints { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 10px; }
.hints-t { font-size: 13px; color: #909399; }
.hint { cursor: default; }
.conv {
  min-height: 360px; max-height: 56vh; overflow-y: auto;
  background: #f7f9fc; border-radius: 8px; padding: 14px; margin-bottom: 12px;
}
.empty { color: #909399; font-size: 13px; text-align: center; padding: 40px 0; }
.row { display: flex; margin-bottom: 12px; }
.row.user { justify-content: flex-end; }
.row.assistant { justify-content: flex-start; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 10px; font-size: 14px; line-height: 1.6; }
.user-bubble { background: #00b386; color: #fff; white-space: pre-wrap; word-break: break-word; }
.asst-bubble { background: #fff; color: #303133; border: 1px solid #ebeef5; }
.clarify-bubble { background: #fdf6ec; border-color: #f5dab1; color: #b88230; display: flex; align-items: center; gap: 6px; }
.system-bubble { background: #f0f9eb; border-color: #e1f3d8; color: #529b2e; }
.clarify-ic { color: #e6a23c; }
.meta { margin-top: 6px; font-size: 12px; color: #c0c4cc; }
.data-collapse { margin-top: 8px; }
.data-pre {
  background: #f5f7fa; border-radius: 6px; padding: 10px 12px; margin: 0;
  overflow: auto; max-height: 320px; font-size: 12px; line-height: 1.5;
  font-family: ui-monospace, monospace; white-space: pre-wrap; word-break: break-word;
}
.draft-card { border: 1px solid #f5dab1; background: #fffdf7; }
.draft-h { display: flex; align-items: center; gap: 6px; color: #e6a23c; font-size: 14px; }
.draft-sum { margin: 8px 0; color: #303133; font-size: 14px; }
.draft-ep { font-size: 12px; color: #909399; font-family: ui-monospace, monospace; margin-bottom: 6px; }
.draft-btns { margin-top: 10px; display: flex; gap: 10px; }
.input-bar { display: flex; gap: 10px; align-items: flex-end; }
.input-bar .el-button { height: 56px; }

/* Markdown 正文（对齐 ReleaseNotes.vue 风格） */
.md-body { font-size: 14px; line-height: 1.7; color: #1a1d21; }
.md-body :deep(h1) { font-size: 18px; }
.md-body :deep(h2) { font-size: 16px; }
.md-body :deep(h3) { font-size: 15px; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3) { margin: 8px 0 6px; font-weight: 600; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md-body :deep(p) { margin: 6px 0; }
.md-body :deep(code) { background: #f2f4f7; border-radius: 3px; padding: 1px 5px; font-family: ui-monospace, monospace; font-size: 13px; }
.md-body :deep(pre) { background: #f5f7fa; border-radius: 6px; padding: 10px 12px; overflow: auto; }
.md-body :deep(table) { border-collapse: collapse; margin: 8px 0; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid #dcdfe6; padding: 5px 9px; }
.md-body :deep(a) { color: #00926e; }
</style>
