<template>
  <section class="native-metrics" aria-label="AI 目标闭环成效">
    <div class="metric-head"><div><h2>AI 目标闭环成效</h2><p>以真实任务、人工决定和执行证据衡量成效</p></div><el-select v-model="days" aria-label="目标统计时间范围" @change="load" style="width:170px"><el-option :value="7" label="目标 · 近 7 天" /><el-option :value="30" label="目标 · 近 30 天" /><el-option :value="90" label="目标 · 近 90 天" /></el-select></div>
    <el-alert v-if="error" title="目标成效暂时无法读取" type="error" :closable="false"><el-button link @click="load">重试</el-button></el-alert>
    <div v-else-if="data" class="metric-grid">
      <div><small>已收口 / 创建目标</small><strong>{{ data.completed }} / {{ data.total }}</strong><span>{{ data.ready_for_review }} 个具备发布评审条件</span></div>
      <div><small>有效验收证据</small><strong>{{ data.verified_criteria }} / {{ data.total_criteria }}</strong><span>已收口目标的全部确认条件</span></div>
      <div><small>平均人工介入</small><strong>{{ data.avg_interventions ?? '—' }} <em>次</em></strong><span>包含验收、授权、暂停与重规划</span></div>
      <div><small>平均目标耗时</small><strong>{{ data.avg_duration_minutes ?? '—' }} <em>分钟</em></strong><span>从创建至收口，含人工等待</span></div>
    </div>
    <p v-if="data" class="metric-note">{{ data.metric_note }}</p><p v-if="data" class="metric-note">{{ data.cost_note }}{{ data.recorded_generation_cost_usd !== null ? ` 已记录 $${data.recorded_generation_cost_usd}，共 ${data.cost_samples} 个样本。` : '' }}</p>
    <el-button type="primary" plain @click="$router.push('/commander')">进入测试目标</el-button>
  </section>
</template>
<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { testMissionMetrics } from '@/api'
const days=ref(30), data=ref(null), error=ref(false)
let version=0
async function load(){ const v=++version; error.value=false; try{const d=await testMissionMetrics({days:days.value}); if(v===version)data.value=d}catch{if(v===version)error.value=true} }
defineExpose({ load })
onMounted(load); onBeforeUnmount(()=>version++)
</script>
<style scoped>
.native-metrics{padding:24px;margin-bottom:28px;background:var(--el-bg-color);border:1px solid var(--el-border-color-lighter);border-radius:12px;color:var(--el-text-color-primary)}.metric-head{display:flex;justify-content:space-between;align-items:start;gap:20px}.metric-head h2{font-size:21px;margin:0 0 8px}.metric-head p,.metric-note{color:var(--el-text-color-secondary);font-size:13px;line-height:1.65}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin:24px 0}.metric-grid small,.metric-grid strong,.metric-grid span{display:block}.metric-grid strong{font-size:29px;margin:10px 0}.metric-grid span{font-size:12px;color:var(--el-text-color-secondary)}em{font-size:13px;font-style:normal}@media(max-width:850px){.metric-grid{grid-template-columns:repeat(2,1fr)}.metric-head{flex-wrap:wrap}}@media(max-width:480px){.native-metrics{padding:16px}.metric-grid{gap:16px}.metric-grid strong{font-size:24px}}
</style>
