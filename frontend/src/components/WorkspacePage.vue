<template>
  <section class="workspace-page">
    <div class="workspace-toolbar">
      <header class="workspace-heading">
        <h1>{{ title }}</h1>
        <div class="workspace-actions"><slot name="actions" />
          <el-button v-if="$slots.filters" class="filter-toggle" :icon="Filter" :aria-expanded="filtersOpen" @click="filtersOpen = !filtersOpen">筛选</el-button>
        </div>
      </header>
      <div v-if="$slots.filters" class="workspace-filters" :class="{ 'is-open': filtersOpen }"><slot name="filters" /></div>
      <div v-if="$slots.selection" class="workspace-selection"><slot name="selection" /></div>
    </div>
    <slot />
  </section>
</template>

<script setup>
import { ref } from 'vue'
import { Filter } from '@element-plus/icons-vue'
defineProps({ title: { type: String, required: true } })
const filtersOpen = ref(false)
</script>

<style scoped>
.workspace-page { min-width: 0; }
.workspace-toolbar { position: sticky; top: -20px; z-index: 20; background: var(--tech-bg); padding: 16px 0 12px; border-bottom: 1px solid var(--el-border-color-lighter); margin-bottom: 12px; }
.workspace-heading { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; }
h1 { margin: 0; font-size: 20px; font-weight: 600; line-height: 28px; color: var(--el-text-color-primary); letter-spacing: 0; }
.workspace-actions, .workspace-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.workspace-filters { padding-top: 12px; }
.workspace-selection { max-height: 25dvh; overflow: auto; }
.workspace-selection:empty { display: none; }
.filter-toggle { display: none; }
.workspace-actions :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 700px) {
  .workspace-page :deep(.el-table-fixed-column--right) { position: static !important; }
  .workspace-page :deep(.el-table-fixed-column--right::before) { display: none; }
  .workspace-heading { gap: 8px; }
  h1 { font-size: 18px; }
  .workspace-actions { width: 100%; }
  .filter-toggle { display: inline-flex; }
  .workspace-filters { display: none; max-height: 28dvh; overflow: auto; }
  .workspace-filters.is-open { display: flex; }
  .workspace-filters :deep(.el-select), .workspace-filters :deep(.el-input), .workspace-actions :deep(.el-select) { max-width: 100%; }
}
</style>
