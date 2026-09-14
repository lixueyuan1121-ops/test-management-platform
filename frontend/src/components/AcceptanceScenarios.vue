<template>
  <section aria-label="具体操作示例">
    <p>这里展示规则对应的操作示例，不需要逐个确认。只有内容缺失或需求不清楚时，才需要到澄清问题中处理。</p>
    <el-button v-if="!disabled" size="small" @click="fillMissing">补齐缺失场景卡</el-button>
    <el-empty v-if="!draft.scenarios?.length" description="还没有场景卡，可从验收条件补齐，再补充具体角色与状态" />
    <article v-for="s in draft.scenarios || []" :key="s.id" class="scene-card">
      <div class="scene-title"><strong>{{ s.id }} · {{ rule(s)?.title }}</strong><el-tag type="info">操作示例 · 无需逐条确认</el-tag></div>
      <p class="hint">{{ s.criterion_ids.join('、') }} · {{ rule(s)?.status === 'excluded' ? '本期排除' : rule(s)?.status === 'pending' ? '规则仍待确认' : '本期验收' }}</p>
      <p><b>谁：</b>{{ s.actor || '角色待补充' }}</p>
      <div class="scene-flow">
        <div v-for="(field, i) in fields.slice(1, 4)" :key="field.key" :class="{ active: (playing[s.id] ?? -1) === i }"><small>{{ field.label }}</small><p>{{ s[field.key] || '待补充' }}</p></div>
      </div>
      <p v-if="s.counterexample"><b>不能出现：</b>{{ s.counterexample }}</p>
      <el-button size="small" @click="playing[s.id] = ((playing[s.id] ?? -1) + 1) % 3">{{ playing[s.id] == null ? '演示状态变化' : '查看下一状态' }}</el-button>
      <p class="source"><b>{{ rule(s)?.source_type === 'explicit' ? '原文依据' : '推导建议，需核对依据' }}：</b>{{ rule(s)?.source_quote || rule(s)?.review_note || '尚未补充依据' }}<br />{{ rule(s)?.source_section }}</p>
      <el-button v-for="id in rule(s)?.source_material_ids || []" :key="id" size="small" link type="primary" @click="$emit('image', id)">对照原图 {{ id }}</el-button>
      <p v-for="q in draft.questions.filter(q => !q.rule_ids.length || q.rule_ids.includes(s.rule_id))" :key="q.id" class="question">{{ q.question }}<br />{{ q.answer ? `产品决定：${q.answer}` : `待决定：${q.options.join(' / ') || '请在澄清问题中填写结论'}` }}</p>
      <el-collapse><el-collapse-item title="编辑场景内容" :name="s.id"><el-form label-position="top" :disabled="disabled"><el-form-item v-for="f in fields" :key="f.key" :label="f.label"><el-input v-model="s[f.key]" type="textarea" :autosize="{minRows:1,maxRows:4}" :aria-label="`${s.id} ${f.label}`" @input="s.reviewed = false" /></el-form-item></el-form></el-collapse-item></el-collapse>

    </article>
    <p class="hint">发现内容不对可以直接修改，保存后会检查是否还缺少必要信息。</p>
  </section>
</template>
<script setup>
import { reactive } from 'vue'
const props = defineProps({ draft: { type: Object, required: true }, disabled: Boolean })
defineEmits(['image'])
const playing = reactive({})
const fields = [{key:'actor',label:'谁'},{key:'given',label:'起始状态'},{key:'when',label:'操作'},{key:'then',label:'可观察结果'},{key:'counterexample',label:'不能出现的结果（有明确依据时填写）'}]
const rule = s => props.draft.rules.find(r => r.id === s.rule_id)
function fillMissing() {
  props.draft.scenarios ||= []
  props.draft.scenario_review_required = true
  const covered = new Set(props.draft.scenarios.flatMap(s => s.criterion_ids))
  for (const r of props.draft.rules.filter(r => r.status !== 'excluded')) for (const c of r.criteria) {
    if (covered.has(c.id)) continue
    let n = 1; while (props.draft.scenarios.some(s => s.id === `S${n}`)) n++
    props.draft.scenarios.push({id:`S${n}`,rule_id:r.id,criterion_ids:[c.id],actor:'',given:r.condition,when:r.action,then:c.text,counterexample:r.forbidden || '',kind:'normal',reviewed:false})
  }
}
</script>
<style scoped>
.scene-card{border:1px solid var(--el-border-color-lighter);border-radius:10px;padding:18px;margin:16px 0;line-height:1.7;overflow-wrap:anywhere}.scene-title{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.scene-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0}.scene-flow>div{padding:12px;background:var(--el-fill-color-light);border:2px solid transparent;border-radius:8px}.scene-flow .active{border-color:var(--el-color-primary)}.scene-flow p{white-space:pre-wrap}.source,.question{font-size:13px;white-space:pre-wrap;padding:10px;background:var(--el-fill-color-light)}.hint,small{color:var(--el-text-color-secondary)}.scene-card .el-checkbox{height:auto;white-space:normal;margin-top:12px}.scene-card :deep(.el-checkbox__label){white-space:normal}@media(max-width:650px){.scene-flow{grid-template-columns:1fr}.scene-card{padding:12px}}
</style>
