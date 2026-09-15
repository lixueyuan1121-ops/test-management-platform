<template>
  <div class="selector-target-notes">
    <div v-for="(note, i) in notes" :key="i" class="target-note">
      <strong>{{ note.title }}</strong>
      <div v-if="note.description && note.description !== note.title">对应操作：{{ note.description }}</div>
      <div v-if="note.before" class="target-before">先操作：{{ note.before }}</div>
      <div v-if="note.step" class="target-meta">脚本第 {{ note.step }} 步<span v-if="note.expected"> · 预期文案：{{ note.expected }}</span></div>
    </div>
    <div class="target-key">选择器：{{ selectorKey }}</div>
  </div>
</template>
<script setup>
defineProps({ selectorKey: String, notes: { type: Array, default: () => [] } })
</script>
<style scoped>
.selector-target-notes { text-align: left; line-height: 1.65; font-size: 13px; overflow-wrap: anywhere; white-space: normal; }
.target-note + .target-note { margin-top: 8px; }
.target-note strong { color: var(--el-text-color-primary); font-weight: 600; }
.target-before { color: var(--el-text-color-regular); }
.target-meta, .target-key { color: var(--el-text-color-secondary); font-size: 12px; }
.target-key { margin-top: 4px; }
</style>
