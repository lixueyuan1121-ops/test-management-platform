<template>
  <el-button size="small" @click="open">{{ account.bound ? `极库云：${account.account_name}` : '绑定我的极库云账号' }}</el-button>
  <el-dialog v-model="visible" title="我的极库云账号" width="520px" :close-on-click-modal="false" :before-close="close">
    <p>当前平台账号：{{ auth.user?.name }}（{{ auth.user?.username }}）</p>
    <el-alert v-if="!account.enabled || !account.configured" type="warning" :closable="false"
      title="请管理员先启用极库云通道并配置个人 SSO 授权。" />
    <p v-if="account.bound">已绑定：{{ account.account_name }}。手动报送及状态同步将使用此账号。</p>
    <p>点击授权后，在 SSO 页面登录你自己的账号，再将页面返回的授权码粘贴到这里。授权会话 5 分钟内有效。</p>
    <el-button :disabled="!account.enabled || !account.configured" :loading="busy" @click="start">{{ account.bound ? '重新授权 / 更换账号' : '开始个人授权' }}</el-button>
    <div v-if="flow" class="auth-flow">
      <el-link :href="flow.authorization_url" target="_blank" rel="noopener noreferrer" type="primary">打开 SSO 授权页面</el-link>
      <el-input v-model="code" type="password" show-password autocomplete="off" placeholder="粘贴本次授权码" :disabled="busy" />
      <el-button type="primary" :loading="busy" :disabled="!code.trim()" @click="complete">完成绑定</el-button>
    </div>
    <template #footer>
      <el-button v-if="account.bound" type="danger" plain :disabled="busy" @click="disconnect">解除绑定</el-button>
      <el-button :disabled="busy" @click="close()">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import { getGeelibAccount, authorizeGeelibAccount, completeGeelibAccount, disconnectGeelibAccount } from '@/api'

const auth = useAuthStore()
const account = ref({ bound: false, configured: false, enabled: false })
const visible = ref(false), busy = ref(false), flow = ref(null), code = ref('')
async function refresh() { account.value = await getGeelibAccount() }
onMounted(() => { refresh().catch(() => {}) })
async function open() {
  visible.value = true
  try { await refresh() } catch { /* 拦截器提示 */ }
}
function close(done) {
  if (busy.value) return
  code.value = ''; flow.value = null; visible.value = false
  if (typeof done === 'function') done()
}
async function start() {
  if (busy.value) return
  busy.value = true; code.value = ''; flow.value = null
  try { flow.value = await authorizeGeelibAccount() }
  catch { /* 拦截器提示 */ } finally { busy.value = false }
}
async function complete() {
  if (busy.value || !flow.value || !code.value.trim()) return
  busy.value = true
  try {
    account.value = await completeGeelibAccount({ state: flow.value.state, code: code.value.trim() })
    flow.value = null; ElMessage.success('已绑定个人极库云账号')
  } catch { flow.value = null /* 授权码一次性消费，失败需重新开始 */ }
  finally { code.value = ''; busy.value = false }
}
async function disconnect() {
  try { await ElMessageBox.confirm('解除当前平台账号的极库云绑定？后续报送需要重新授权。', '解除绑定', { confirmButtonText: '确认解绑', cancelButtonText: '取消' }) }
  catch { return }
  busy.value = true
  try {
    await disconnectGeelibAccount(); flow.value = null; code.value = ''
    await refresh(); ElMessage.success('已解除绑定')
  } catch { /* 拦截器提示 */ } finally { busy.value = false }
}
</script>

<style scoped>
.auth-flow { display: flex; flex-direction: column; align-items: flex-start; gap: 14px; margin-top: 18px; }
p { line-height: 1.7; }
</style>
