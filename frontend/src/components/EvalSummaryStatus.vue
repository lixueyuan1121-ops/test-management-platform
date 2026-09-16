<template>
  <section class="summary-status" :class="`summary-status--${state.type}`" role="status" aria-live="polite" :aria-busy="state.busy">
    <div class="status-line">
      <el-icon v-if="state.busy" class="is-loading"><Loading /></el-icon>
      <el-tag :type="state.type" effect="light">{{ state.label }}</el-tag>
      <span v-if="state.elapsed" class="status-meta">{{ state.busy ? '已等待' : '用时' }} {{ state.elapsed }}</span>
      <span v-if="state.retries" class="status-meta">已自动重试 {{ state.retries }} 次</span>
    </div>
    <p>{{ state.hint }}</p>
    <pre v-if="state.error" class="status-error">{{ state.error }}</pre>
    <details v-if="state.lastError"><summary>查看最近一次模型错误</summary><pre>{{ state.lastError }}</pre></details>
    <p v-if="connectionError" class="connection-error" role="alert">状态暂时无法更新，正在重新连接。后台任务可能仍在执行，请勿重复提交。</p>
    <p v-if="requestError" class="status-error" role="alert">{{ requestError }}</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { evalSummaryState } from '@/utils/evalSummaryState'
const props = defineProps({ task: { type: Object, default: () => ({}) }, submitting: Boolean,
  connectionError: Boolean, requestError: { type: String, default: '' } })
const state = computed(() => evalSummaryState(props.task, props.submitting))
</script>

<style scoped>
.summary-status { padding: 16px; margin: 12px 0; border: 1px solid #dce5f0; border-radius: 8px; background: #f5f8fc; }
.summary-status--warning { background: #fffbf2; border-color: #f0dfb7; }
.summary-status--danger { background: #fff5f5; border-color: #f5caca; }
.summary-status--success { background: #f2faf5; border-color: #c9e6d3; }
.status-line { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.status-meta { font-size: 12px; color: #606266; }
p { margin: 10px 0 0; font-size: 13px; line-height: 1.6; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.6 ui-monospace, monospace; margin: 10px 0 0; }
.status-error { color: #b42318; overflow-wrap: anywhere; }
.connection-error { color: #9a6700; }
details { margin-top: 10px; font-size: 12px; }
summary { cursor: pointer; color: #606266; }
</style>
