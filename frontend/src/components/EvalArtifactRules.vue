<template>
  <div class="artifact-rules">
    <p class="hint">可选：下载实际产物并检查硬性要求。未采集到文件时标为证据不足。单个文件上限20MB。</p>
    <div v-for="(rule, index) in modelValue" :key="index" class="rule-row">
      <el-input :model-value="rule.file_pattern" placeholder="文件名，如 *.xlsx" aria-label="产物文件名" :disabled="disabled" @update:model-value="set(index, 'file_pattern', $event)" />
      <el-select :model-value="rule.kind" aria-label="产物检查类型" :disabled="disabled" @update:model-value="set(index, 'kind', $event)">
        <el-option v-for="(label, kind) in kinds" :key="kind" :label="label" :value="kind" />
      </el-select>
      <template v-if="['cell_formula', 'cell_value'].includes(rule.kind)">
        <el-input :model-value="rule.sheet" placeholder="Sheet 名称" :disabled="disabled" @update:model-value="set(index, 'sheet', $event)" />
        <el-input :model-value="rule.cell" placeholder="单元格，如 B2" :disabled="disabled" @update:model-value="set(index, 'cell', $event.toUpperCase())" />
      </template>
      <el-input v-if="!['file_readable', 'cell_formula'].includes(rule.kind)" :model-value="rule.expected" :placeholder="expectedHint(rule.kind)" aria-label="产物期望值" :disabled="disabled" @update:model-value="set(index, 'expected', $event)" />
      <el-button v-if="!disabled" text type="danger" @click="$emit('update:modelValue', modelValue.filter((_, i) => i !== index))">移除</el-button>
    </div>
    <el-button v-if="!disabled" size="small" :disabled="modelValue.length >= 20" @click="$emit('update:modelValue', [...modelValue, {kind: 'file_readable', file_pattern: '*.xlsx'}])">添加产物检查</el-button>
  </div>
</template>
<script setup>
const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const kinds = { file_readable: '文件格式可解析', text_contains: '内容包含文字', sheet_exists: '包含指定 Sheet', cell_formula: '单元格有公式', cell_value: '单元格值正确', min_pages: 'PDF / PPT 最少页数' }
const expectedHint = kind => ({ text_contains: '必须包含的文字', sheet_exists: '必须包含的 Sheet 名称', cell_value: '期望的单元格值', min_pages: '最少页数（整数）' }[kind])
function set(index, field, value) {
  emit('update:modelValue', props.modelValue.map((rule, i) => i === index ? { ...rule, [field]: value } : rule))
}
</script>
<style scoped>
.artifact-rules { width: 100%; }
.hint { color: #737a86; font-size: 12px; line-height: 1.6; }
.rule-row { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 0; border-bottom: 1px solid #ebeef5; }
.rule-row :deep(.el-input), .rule-row :deep(.el-select) { width: 190px; }
</style>
