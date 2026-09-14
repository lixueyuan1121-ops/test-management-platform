<template>
  <section class="ai-live-progress" aria-label="AI 实时生成进度">
    <div class="live-heading">
      <strong role="status">{{ heading }}</strong>
      <span>{{ progress?.started_at ? '已执行' : '已等待' }} {{ elapsed }} · 已返回 {{ progress?.chars || 0 }} 字</span>
    </div>
    <p v-if="progress?.total" class="live-note">已处理 {{ progress.completed }} / {{ progress.total }} 项<span v-if="failedCount"> · {{ failedCount }} 项需要核对</span>。{{ active ? '完成校验后可查看正式结果。' : '' }}</p>
    <p v-if="active && !progress?.chars" class="live-note">{{ job?.status === 'pending' ? '任务已入队，开始执行后会自动展示返回内容。' : '等待模型返回正文，收到后会自动展示。' }}</p>
    <p v-else-if="active && idleSeconds >= 30" class="live-note">{{ idleSeconds }} 秒未收到新正文，正在等待后续返回。已收到的内容保留在下方。</p>
    <p v-if="job?.error" class="live-error">{{ job.error }}</p>
    <template v-if="units.length">
      <div class="live-toolbar">
        <el-select v-model="selectedId" fit-input-width aria-label="查看返回内容" @change="manualSelection = true">
          <el-option v-for="unit in units" :key="unit.id" :value="unit.id" :label="`${statusLabel(unit)} · ${unit.title}`" :title="unit.title" />
        </el-select>
        <el-tag size="small" type="warning">生成中间内容 · 未确认</el-tag>
        <el-button v-if="manualSelection || !stickToBottom" size="small" text @click="followLatest">查看最新内容</el-button>
      </div>
      <p v-if="selected?.truncated" class="live-note">预览仅保留最近一段返回内容；完整结果将在处理完成后展示。</p>
      <p v-if="selected?.note" class="live-note">{{ selected.note }}</p>
      <pre v-if="selected?.text" ref="output" class="live-output" tabindex="0" aria-label="实时返回正文" @scroll="onScroll">{{ preview }}</pre>
      <p v-else class="live-note">{{ selected?.chars ? '此项的预览已收起，以保留正在返回的内容。完整结果将在处理完成后展示。' : selected?.status === 'pending' ? '此项尚未开始。' : '此项尚未返回正文。' }}</p>
      <details v-if="selected?.text && preview !== selected.text" class="live-raw"><summary>查看原始返回</summary><pre>{{ selected.text }}</pre></details>
      <p v-if="progress?.omitted" class="live-note">另有 {{ progress.omitted }} 项已从进度预览中收起，仍计入处理总数。</p>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { readableAiPreview } from '@/utils/aiPreview'

const props = defineProps({ job: Object, active: Boolean })
const now = ref(Date.now()), openedAt = ref(Date.now()), selectedId = ref(''), manualSelection = ref(false)
const output = ref(null), stickToBottom = ref(true)
const progress = computed(() => props.job?.progress)
const units = computed(() => progress.value?.units || [])
const selected = computed(() => units.value.find(unit => unit.id === selectedId.value))
const preview = computed(() => {
  const unit = selected.value
  if (!unit?.text) return ''
  return unit.truncated ? unit.text : readableAiPreview(unit.text) || unit.text
})
const statusLabel = unit => ({ pending: '等待', paused: '尚未开始', running: unit.chars || unit.text ? '返回中' : '处理中', done: '已完成', warning: '需核对', failed: '失败' }[unit.status] || unit.status)
const failedCount = computed(() => units.value.filter(u => ['failed', 'warning'].includes(u.status)).length)
const heading = computed(() => {
  const status = props.job?.status
  if (status === 'failed') return '生成失败 · 已保留返回内容'
  if (status === 'cancelled') return '任务已取消'
  if (status === 'done') return '生成已完成'
  if (!props.active) return '已停止等待 · 显示最后收到的内容'
  if (status === 'pending') return props.job?.queue_position > 0 ? `排队中 · 前面还有 ${props.job.queue_position} 个任务` : '排队中 · 等待开始'
  return ({ preparing: '正在准备分析', reading_images: '正在识别需求图片', analyzing: '正在整理需求与验收规则',
    generating: '正在生成测试用例', scenarios: '正在分批整理具体场景', validating: '正在校验需求分析结果', saving: '正在合并、校验并保存用例' })[progress.value?.stage] || '等待任务返回进度'
})
const elapsed = computed(() => {
  const seconds = Math.max(0, Math.floor(((props.active ? now.value : progress.value?.updated_at || now.value) - (progress.value?.started_at || openedAt.value)) / 1000))
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`
})
const idleSeconds = computed(() => Math.max(0, Math.floor((now.value - (progress.value?.last_output_at || now.value)) / 1000)))
function chooseLatest() {
  const running = units.value.filter(u => u.status === 'running')
  const failed = units.value.filter(u => u.status === 'failed' && u.text)
  selectedId.value = (running.find(u => u.text) || running[0] || failed.at(-1) || units.value.filter(u => u.text).at(-1) || units.value.at(-1))?.id || ''
}
function onScroll() {
  const el = output.value
  if (el) stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 32
}
async function scrollToLatest() {
  await nextTick()
  if (output.value && stickToBottom.value) output.value.scrollTop = output.value.scrollHeight
}
function followLatest() { manualSelection.value = false; stickToBottom.value = true; chooseLatest(); scrollToLatest() }
watch(() => props.job?.id, () => { openedAt.value = Date.now(); manualSelection.value = false; stickToBottom.value = true })
watch(units, () => { if (!manualSelection.value || !selected.value) chooseLatest() }, { immediate: true })
watch(preview, scrollToLatest)
const timer = setInterval(() => { if (props.active) now.value = Date.now() }, 1000)
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.ai-live-progress { margin: 16px 0; padding: 18px; border: 1px solid #dce5f5; border-radius: 10px; background: #f8faff; min-width: 0; }
.live-heading, .live-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.live-heading { justify-content: space-between; color: #254c85; }
.live-heading span, .live-note { font-size: 13px; color: #606b7d; line-height: 1.7; }
.live-toolbar { margin: 12px 0; }
.live-toolbar .el-select { width: 420px; max-width: 100%; }
.live-output, .live-raw pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; line-height: 1.8; font-family: inherit; max-height: 320px; overflow: auto; padding: 16px; background: white; border: 1px solid #e2e8f1; border-radius: 6px; margin: 12px 0 0; }
.live-output { font-size: 14px; line-height: 1.8; }
.live-raw { margin-top: 10px; font-size: 12px; color: #687588; }
.live-raw summary { cursor: pointer; }
.live-error { color: #c64343; overflow-wrap: anywhere; }
@media (max-width: 600px) { .ai-live-progress { padding: 12px; } .live-toolbar .el-select { width: 100%; } }
</style>
