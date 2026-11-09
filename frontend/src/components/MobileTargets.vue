<template>
  <div>
    <el-form-item v-for="(label, platform) in labels" :key="platform" :label="label">
      <el-select :model-value="modelValue[platform]" :aria-label="label + '真机'" placeholder="选择已登记的真机" @update:model-value="setTarget(platform, $event)">
        <el-option v-for="d in options(platform)" :key="d.runner_id" :value="d.runner_id"
          :label="[d.name, d.mobile_target?.model, d.mobile_target?.build, d.mobile_target?.online ? '在线' : '待连接'].filter(Boolean).join(' · ')" />
      </el-select>
    </el-form-item>
    <details>
      <summary>指定 APP 构建号（可选）</summary>
      <el-form-item v-for="(label, platform) in labels" :key="platform" :label="label">
        <el-input :aria-label="label + '构建号'" :model-value="builds[platform]" placeholder="默认使用当前安装的构建"
          @update:model-value="$emit('update:builds', { ...builds, [platform]: $event.trim() })" />
      </el-form-item>
    </details>
  </div>
</template>

<script setup>
import { watch } from 'vue'
const props = defineProps({
  devices: { type: Array, required: true },
  modelValue: { type: Object, required: true },
  builds: { type: Object, required: true },
})
const emit = defineEmits(['update:modelValue', 'update:builds'])
const labels = { android: 'Android', ios: 'iPhone' }
const options = platform => props.devices.filter(d => d.platform === platform)
function setTarget(platform, value) {
  emit('update:modelValue', { ...props.modelValue, [platform]: value })
}
watch(() => [props.devices, props.modelValue], () => {
  const targets = { ...props.modelValue }
  let changed = false
  for (const platform of Object.keys(labels)) {
    if (targets[platform]) continue
    const ready = options(platform).filter(d => d.mobile_target?.online)
    if (ready.length === 1) { targets[platform] = ready[0].runner_id; changed = true }
  }
  if (changed) emit('update:modelValue', targets)
}, { immediate: true })
</script>
<style scoped>
details{margin-bottom:12px}summary{cursor:pointer;color:var(--el-text-color-secondary);margin-bottom:8px}
</style>
