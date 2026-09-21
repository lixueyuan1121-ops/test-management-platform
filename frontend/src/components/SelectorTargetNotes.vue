<template>
  <div class="selector-target-notes">
    <div v-for="(note, i) in notes" :key="i" class="target-note">
      <div class="target-step">
        <span v-if="note.step" class="step-badge">第 {{ note.step }} 步</span>
        <span class="step-action">{{ note.description || note.title }}</span>
      </div>
      <div v-if="note.title && note.title !== note.description" class="target-hint">待补元素：{{ note.title }}</div>
      <div v-if="note.expected" class="target-meta">该步预期文案：{{ note.expected }}</div>
    </div>
    <div class="target-key">选择器 key：{{ selectorKey }}</div>
  </div>
</template>
<script setup>
defineProps({ selectorKey: String, notes: { type: Array, default: () => [] } })
</script>
<style scoped>
.selector-target-notes { text-align: left; line-height: 1.65; font-size: 13px; overflow-wrap: anywhere; white-space: normal; }
.target-note + .target-note { margin-top: 8px; }
.target-step { display: flex; align-items: baseline; gap: 6px; }
.step-badge { flex: none; background: var(--el-color-warning-light-8); color: var(--el-color-warning); border-radius: 4px; padding: 0 6px; font-size: 12px; font-weight: 600; }
.step-action { color: var(--el-text-color-primary); font-weight: 600; }
.target-hint { color: var(--el-text-color-regular); }
.target-meta, .target-key { color: var(--el-text-color-secondary); font-size: 12px; }
.target-key { margin-top: 4px; }
</style>
