<template>
  <!-- 统一任务下拉:可搜索 + 按状态分组 + 每项显示 状态标签/日期/任务名。四处复用。 -->
  <el-select
    :model-value="modelValue"
    :placeholder="placeholder"
    size="small"
    clearable
    filterable
    fit-input-width
    :filter-method="filterMethod"
    :style="{ width: width }"
    @update:model-value="(v) => $emit('update:modelValue', v)"
    @change="(v) => $emit('change', v)"
    @visible-change="(v) => { if (v) kw = '' }"
  >
    <el-option-group v-for="g in groups" :key="g.key" :label="g.label">
      <el-option v-for="t in g.items" :key="t.id" :label="labelOf(t)" :value="t.id" :title="labelOf(t)">
        <span class="tp-opt">
          <el-tag :type="ST[t.status]?.type || 'info'" size="small" effect="plain" class="tp-tag">{{ ST[t.status]?.label || t.status }}</el-tag>
          <span class="tp-date">{{ (t.assigned_date || '').slice(5) }}</span>
          <span class="tp-title">{{ t.description || t.title }}</span>
        </span>
      </el-option>
    </el-option-group>
  </el-select>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  modelValue: { type: [Number, String, null], default: null },
  tasks: { type: Array, default: () => [] },
  placeholder: { type: String, default: '选择任务' },
  width: { type: String, default: '240px' },
})
defineEmits(['update:modelValue', 'change'])

// 状态 → 标签(与全局口径一致)+ 分组顺序(活跃在前,已关闭殿后)
const ST = {
  testing: { label: '测试中', type: 'warning', order: 0 },
  blocked: { label: '阻塞', type: 'danger', order: 1 },
  pending: { label: '待办', type: 'info', order: 2 },
  online: { label: '已上线', type: 'success', order: 3 },
  closed: { label: '已关闭', type: 'info', order: 4 },
}
const kw = ref('')
function labelOf(t) { return t.description || t.title || `#${t.id}` }
function filterMethod(q) { kw.value = (q || '').trim().toLowerCase() }

// 按状态分组,组内按日期倒序;支持搜索过滤(匹配名称)
const groups = computed(() => {
  const q = kw.value
  const buckets = new Map()
  for (const t of props.tasks) {
    if (q && !labelOf(t).toLowerCase().includes(q)) continue
    const s = t.status || 'pending'
    if (!buckets.has(s)) buckets.set(s, [])
    buckets.get(s).push(t)
  }
  return [...buckets.entries()]
    .sort((a, b) => (ST[a[0]]?.order ?? 9) - (ST[b[0]]?.order ?? 9))
    .map(([s, items]) => ({
      key: s,
      label: ST[s]?.label || s,
      items: items.sort((a, b) => String(b.assigned_date || '').localeCompare(String(a.assigned_date || ''))),
    }))
})
</script>

<style scoped>
.tp-opt { display: flex; align-items: center; gap: 8px; }
.tp-tag { flex: none; }
.tp-date { flex: none; color: #90a4ae; font-size: 12px; font-family: ui-monospace, Menlo, monospace; }
.tp-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
