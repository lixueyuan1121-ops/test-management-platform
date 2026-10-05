<template>
  <div v-if="value" class="replay-readiness">
    <el-tag :type="value.state === 'verified' ? 'success' : 'info'" size="small">{{ value.label }}</el-tag>
    <p>当前版本连续通过 {{ value.consecutive_passes }} / {{ value.required_passes }} 次严格回归。脚本或选择器变化后需要重新验证。</p>
    <el-button v-if="canVerify && value.state !== 'verified'" size="small" :loading="sending" @click="verify">验证回归（2 次）</el-button>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { verifyCaseReplay } from '../api'
const props = defineProps({ value: Object, caseId: Number, projectId: Number })
const emit = defineEmits(['submitted'])
const auth = useAuthStore(), router = useRouter(), sending = ref(false)
const canVerify = computed(() => props.caseId && props.projectId && ['admin', 'member'].includes(auth.roleIn(props.projectId)))
async function verify() {
  try {
    await ElMessageBox.confirm('将选择可用执行设备，从初始状态执行当前用例两次。用例中的业务操作也会执行两次。', '验证当前回归版本', { confirmButtonText: '下发验证', cancelButtonText: '取消' })
    sending.value = true
    const result = await verifyCaseReplay(props.projectId, props.caseId)
    ElMessage.success('已下发两次回归验证，完成后刷新查看认证状态')
    emit('submitted', result)
    router.push({ path: '/exec-results', query: { project_id: props.projectId, batch_id: result.batch_id } })
  } catch { /* cancellation or API interceptor */ }
  finally { sending.value = false }
}
</script>
<style scoped>
.replay-readiness { padding: 12px; margin: 12px 0; border: 1px solid var(--el-border-color); border-radius: 8px; }
.replay-readiness p { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; margin: 8px 0; }
</style>
