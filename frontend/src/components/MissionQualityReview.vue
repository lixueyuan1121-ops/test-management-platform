<template>
  <section class="quality-review" aria-label="独立用例审查">
    <h4>独立用例审查</h4>
    <el-alert :title="!quality ? '该方案尚未完成独立审查，请重新准备方案' : quality.status === 'passed' ? '所选方案已完成独立审查，请继续核对并授权' : '发现需要修订或澄清的问题；有问题的用例不能下发'" :type="quality?.status === 'passed' ? 'success' : 'warning'" :closable="false" />
    <p class="hint">{{ quality?.note }}</p>
    <article v-for="r in quality?.cases.filter(c => c.findings.length) || []" :key="r.case_id">
      <strong>用例 #{{ r.case_id }}</strong>
      <div v-for="(f, i) in r.findings" :key="i" class="finding">
        <p><b>{{ labels[f.kind] || f.kind }}</b> · {{ f.criterion_ids.join('、') }}</p><p>{{ f.reason }}</p>
        <template v-if="f.kind !== 'clarification' && fields.some(k => f[`suggested_${k}`])">
          <el-collapse><el-collapse-item title="对照修订建议" :name="`${r.case_id}-${i}`">
            <div v-for="k in fields.filter(k => f[`suggested_${k}`])" :key="k" class="diff"><div><small>当前{{ fieldLabels[k] }}</small><p>{{ cases.find(c => c.id === r.case_id)?.[k] || '未填写' }}</p></div><div><small>建议{{ fieldLabels[k] }}</small><p>{{ f[`suggested_${k}`] }}</p></div></div>
            <p class="hint">应用后保存为待采纳的修订副本，保留原用例并重新审查。脚本需要与修改后的场景一致；每个用例本轮最多应用一次 AI 修订。</p>
            <el-button v-if="!disabled" :disabled="repairs.includes(r.case_id)" @click="$emit('repair', {repair_case_id:r.case_id, repair_finding_index:i})">应用此修订草稿</el-button>
          </el-collapse-item></el-collapse>
        </template>
        <p v-else-if="f.kind === 'clarification'" class="hint">请回到需求中处理产品决定，再重新准备方案。<el-button v-if="!disabled" link type="primary" @click="$emit('clarify')">处理需求歧义</el-button></p>
      </div>
    </article>
  </section>
</template>
<script setup>
defineProps({quality:Object,cases:{type:Array,default:()=>[]},repairs:{type:Array,default:()=>[]},disabled:Boolean})
defineEmits(['repair', 'clarify'])
const labels={unsupported_expected:'预期缺少依据',missing_branch:'遗漏验收分支',unobservable:'无法观察结果',precondition:'前置资料缺失',clarification:'业务歧义待澄清'}
const fields=['precondition','steps','expected'],fieldLabels={precondition:'前提',steps:'步骤',expected:'预期'}
</script>
<style scoped>
.quality-review{background:var(--el-fill-color-light);padding:16px;border-radius:10px;margin:16px 0}.finding{border-top:1px solid var(--el-border-color);padding:12px 0;white-space:pre-wrap;overflow-wrap:anywhere}.diff{display:grid;grid-template-columns:1fr 1fr;gap:14px}.diff>div{background:var(--el-bg-color);padding:12px}.hint,small{font-size:12px;color:var(--el-text-color-secondary)}@media(max-width:650px){.diff{grid-template-columns:1fr}}
</style>
