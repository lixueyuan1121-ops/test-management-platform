<template>
  <el-drawer :model-value="visible" :size="'min(760px, 100vw)'" class="run-inspector" :show-close="false" @update:model-value="$emit('update:visible', $event)">
    <template #header>
      <div class="inspector-heading">
        <div><span class="eyebrow">执行 #{{ row?.run_id }}</span><h2>{{ title }}</h2></div>
        <el-tooltip content="关闭详情"><el-button :icon="Close" aria-label="关闭详情" text @click="$emit('update:visible', false)" /></el-tooltip>
      </div>
    </template>
    <template v-if="row">
      <div class="run-meta">
        <el-tag :type="verdictType">{{ verdictLabel }}</el-tag>
        <span>{{ engineLabel }}</span><span v-if="row.score != null">{{ row.score }} / 5 分</span>
        <span v-if="row.reported_duration != null">上报耗时 {{ row.reported_duration }}{{ /^\d+(\.\d+)?$/.test(String(row.reported_duration)) ? ' 秒' : '' }}</span>
        <el-link v-if="shareUrl" :href="shareUrl" target="_blank" rel="noopener noreferrer" :icon="Link" type="primary">会话原文</el-link>
      </div>
      <el-tabs v-model="activeTab" class="inspector-tabs">
        <el-tab-pane label="判定与复核" name="verdict">
          <section class="evidence-section">
            <h3>判定理由</h3>
            <div class="prose" v-html="renderMd(row.verdict_reason || row.reason || '暂无判定理由')"></div>
          </section>
          <section v-if="row.verdict_dims" class="evidence-section">
            <h3>分项判定</h3>
            <div v-for="dim in dimensions.filter(d => row.verdict_dims[d.key])" :key="dim.key" class="dimension-result">
              <div class="dimension-title"><el-icon :class="row.verdict_dims[dim.key].pass === true ? 'passed' : row.verdict_dims[dim.key].pass === false ? 'failed' : 'unknown'"><CircleCheck v-if="row.verdict_dims[dim.key].pass === true" /><CircleClose v-else-if="row.verdict_dims[dim.key].pass === false" /><QuestionFilled v-else /></el-icon><h4>{{ dim.label }}</h4><span v-if="row.verdict_dims[dim.key].pass == null">无法定论</span></div>
              <div class="prose" v-html="renderMd(row.verdict_dims[dim.key].note)"></div>
              <blockquote v-if="row.verdict_dims[dim.key].evidence_quote">{{ row.verdict_dims[dim.key].evidence_quote }}</blockquote>
            </div>
          </section>
          <section v-if="row.verdict" class="evidence-section"><h3>人工复核</h3><slot name="review" /></section>
        </el-tab-pane>
        <el-tab-pane label="提问与回答" name="conversation">
          <section v-for="block in conversation" :key="block.label" class="evidence-section"><h3>{{ block.label }}</h3><div class="prose" v-html="renderMd(block.text)"></div></section>
        </el-tab-pane>
        <el-tab-pane label="采集记录" name="trace">
          <div v-if="traceLoading" role="status" class="trace-state">正在读取采集记录…</div>
          <div v-else-if="traceError" role="alert" class="trace-state">{{ traceError }}<el-button text type="primary" @click="loadTrace">重新加载</el-button></div>
          <template v-else>
            <div v-if="!trace" class="trace-state">暂无 trace 记录</div>
            <template v-else>
              <section class="evidence-section"><h3>思考记录</h3><div class="prose" v-html="renderMd(trace.thinking || '未取得思考记录')"></div></section>
              <section class="evidence-section"><h3>工具 / MCP <span class="count">{{ trace.tool_calls?.length || 0 }}</span></h3>
                <div v-if="!trace.tool_calls?.length" class="trace-state">未取得工具调用记录</div>
                <el-collapse v-else>
                  <el-collapse-item v-for="(tool, i) in trace.tool_calls" :key="i" :title="`${i + 1}. ${tool.name || tool.original_tool_name || '未命名工具'} · ${tool.reached_result ? '有结果记录' : '未取得结果'}`" :name="i">
                    <h4>参数</h4><pre>{{ pretty(tool.args) }}</pre><h4>返回结果</h4><pre>{{ tool.result_text || '未取得结果' }}</pre>
                  </el-collapse-item>
                </el-collapse>
              </section>
              <section class="evidence-section"><h3>产物记录</h3><pre>{{ pretty(trace.artifacts || []) }}</pre></section>
              <section v-if="trace.capture_diagnostics" class="evidence-section"><h3>采集诊断</h3><pre>{{ pretty(trace.capture_diagnostics) }}</pre></section>
            </template>
          </template>
          <section v-if="row.raw_message" class="evidence-section"><h3>原始 message</h3><pre>{{ pretty(row.raw_message) }}</pre></section>
        </el-tab-pane>
      </el-tabs>
    </template>
    <template #footer><div class="inspector-actions"><slot name="actions" /></div></template>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { Close, Link, CircleCheck, CircleClose, QuestionFilled } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps({ visible: Boolean, row: Object, title: String })
defineEmits(['update:visible'])
const activeTab = ref('verdict')
const trace = ref(null)
const traceLoading = ref(false)
const traceError = ref('')
let controller
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderMd = text => DOMPurify.sanitize(md.render(String(text || '暂无内容')))
const pretty = value => {
  if (typeof value === 'string') { try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value } }
  return JSON.stringify(value ?? null, null, 2)
}
const engineLabel = computed(() => ({ namiwork: '纳米Work', workbuddy: 'WorkBuddy' }[props.row?.target_engine] || props.row?.target_engine || '未标注产品'))
const verdictLabel = computed(() => ({ pass: '通过', fail: '不通过', error: '待复核' }[props.row?.verdict] || (props.row?.status === 'failed' ? '执行失败' : '未判定')))
const verdictType = computed(() => ({ pass: 'success', fail: 'danger', error: 'info' }[props.row?.verdict] || 'info'))
const shareUrl = computed(() => /^https?:\/\//i.test(props.row?.share_link || '') ? props.row.share_link : null)
const dimensions = [{ key: 'thinking_complete', label: '思考推理' }, { key: 'tools_ok', label: '工具 / MCP 调用' }, { key: 'artifact_expected', label: '产物 / 答案' }, { key: 'dimension_ok', label: '主考维度' }]
const conversation = computed(() => [{ label: '提问 Prompt', text: props.row?.payload?.prompt }, { label: '预期 Expected', text: props.row?.payload?.expected }, { label: '最终回答', text: props.row?.answer }])

async function loadTrace() {
  controller?.abort()
  const current = new AbortController()
  controller = current
  trace.value = null
  traceError.value = ''
  traceLoading.value = false
  const source = props.row?.trace
  if (!source) return
  if (typeof source === 'object' && !Array.isArray(source)) { trace.value = source; return }
  traceLoading.value = true
  try {
    // Only fetch this platform's uploaded traces, never arbitrary runner-supplied URLs.
    const url = new URL(source, window.location.origin)
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/uploads/')) throw new Error('unsupported source')
    const response = await fetch(url, { signal: current.signal, credentials: 'same-origin' })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid trace')
    if (!current.signal.aborted) trace.value = data
  } catch (error) {
    if (!current.signal.aborted) traceError.value = '采集记录读取失败，尚无法核实工具过程。'
  } finally { if (!current.signal.aborted) traceLoading.value = false }
}
watch(() => [props.visible, props.row?.run_id, props.row?.trace], () => {
  controller?.abort()
  activeTab.value = 'verdict'
  trace.value = null
  traceError.value = ''
  traceLoading.value = false
})
watch(activeTab, tab => { if (tab === 'trace') loadTrace() })
onBeforeUnmount(() => controller?.abort())
</script>

<style scoped>
.inspector-heading { display: flex; align-items: flex-start; gap: 16px; width: 100%; }
.inspector-heading > div { flex: 1; min-width: 0; }
.eyebrow { color: #68717d; font-size: 12px; }
h2 { font-size: 19px; line-height: 1.5; margin: 6px 0 0; color: #202329; overflow-wrap: anywhere; }
h3 { font-size: 14px; margin: 0 0 14px; color: #202329; }
h4 { font-size: 13px; margin: 0; }
.run-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; color: #68717d; font-size: 12px; margin-bottom: 16px; }
.evidence-section { padding: 18px 0; border-bottom: 1px solid #e7ebef; }
.evidence-section:last-child { border-bottom: 0; }
.dimension-result { padding: 14px 0; border-bottom: 1px solid #eff1f4; }
.dimension-result:last-child { border: 0; }
.dimension-title { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.dimension-title > span { margin-left: auto; font-size: 12px; color: #68717d; }
.passed { color: var(--el-color-success); } .failed { color: var(--el-color-danger); } .unknown { color: #68717d; }
.prose { color: #414b58; font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; }
.prose :deep(p) { margin: 0 0 10px; }
.prose :deep(img) { max-width: 100%; }
.prose :deep(table) { display: block; max-width: 100%; overflow: auto; border-collapse: collapse; }
.prose :deep(td), .prose :deep(th) { border: 1px solid #e7ebef; padding: 6px 10px; }
.prose :deep(pre), pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; background: #f5f7f9; padding: 12px; border-radius: 4px; font-size: 12px; line-height: 1.6; }
blockquote { margin: 12px 0 0; padding: 8px 12px; border-left: 2px solid #cbd5e1; font-size: 13px; color: #68717d; overflow-wrap: anywhere; }
.trace-state { font-size: 13px; color: #68717d; padding: 16px 0; }
.count { font-weight: 400; color: #68717d; margin-left: 6px; }
.inspector-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
</style>

<style>
.run-inspector .el-drawer__header { margin-bottom: 0; padding-bottom: 16px; border-bottom: 1px solid #e7ebef; }
.run-inspector .el-drawer__footer { border-top: 1px solid #e7ebef; }
.run-inspector .el-collapse-item__header { height: auto; min-height: 48px; line-height: 1.6; padding: 10px 0; overflow-wrap: anywhere; }
</style>
