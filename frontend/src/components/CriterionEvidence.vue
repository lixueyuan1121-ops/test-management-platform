<template>
  <div v-for="a in assessments || []" :key="a.run_id" class="criterion-evidence">
    <strong>执行 #{{ a.run_id }} · {{ labels[a.verdict] || '核验中' }}</strong><p>{{ a.reason }}</p>
    <div v-for="(r, i) in a.refs" :key="i"><el-button link type="primary" @click="$emit('open', a.run_id)">步骤 {{ r.step_no }} · {{ r.kind === 'image' ? '图片观察' : '实际断言' }}</el-button><p>{{ r.region }} {{ r.quote }}</p></div>
  </div>
</template>
<script setup>
defineProps({assessments:Array})
defineEmits(['open'])
const labels={supported:'证据支持',contradicted:'证据与预期冲突',insufficient:'证据不足'}
</script>
<style scoped>
.criterion-evidence{font-size:12px;margin-top:10px;white-space:pre-wrap;overflow-wrap:anywhere}.criterion-evidence p{margin:5px 0}
</style>
