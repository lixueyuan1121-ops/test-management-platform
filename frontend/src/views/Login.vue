<template>
  <main class="login-page">
    <header class="login-brand">
      <TargetMark :size="36" :animated="false" aria-hidden="true" />
      <span class="brand-wordmark">QALAB</span>
    </header>
    <section class="login-content" aria-labelledby="login-title">
      <div class="login-identity">
        <TargetMark :size="52" :animated="false" aria-hidden="true" />
        <h1>测试管理平台</h1>
      </div>
      <h2 id="login-title">登录</h2>
      <el-alert v-if="loginError" title="登录未成功，请核对账号密码或稍后重试" type="error" show-icon :closable="false" class="login-error" />
      <el-form :model="form" @submit.prevent="onLogin" label-position="top" class="login-form">
        <el-form-item label="用户名">
          <el-input v-model="form.username" size="large" placeholder="请输入用户名" autocomplete="username" :disabled="loading" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" size="large" show-password placeholder="请输入密码" autocomplete="current-password" :disabled="loading" />
        </el-form-item>
        <el-button type="primary" native-type="submit" size="large" :loading="loading" class="login-btn">{{ loading ? '登录中…' : '登录' }}</el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import TargetMark from '@/components/TargetMark.vue'

const auth = useAuthStore()
const router = useRouter()
const loading = ref(false)
const loginError = ref(false)
const form = reactive({ username: '', password: '' })

async function onLogin() {
  if (loading.value) return
  if (!form.username || !form.password) { ElMessage.warning('请输入用户名和密码'); return }
  loading.value = true
  loginError.value = false
  try {
    await auth.login(form.username, form.password)
    await router.push('/dashboard')
  } catch {
    loginError.value = true
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { min-height:100dvh; box-sizing:border-box; background:#f5f6f8; color:#242830; font-family:system-ui,-apple-system,'Segoe UI',sans-serif; padding:0 24px 48px; }
.login-brand { display:flex; align-items:center; gap:12px; min-height:80px; border-bottom:1px solid #e0e3e8; --tm-line:#9ba3b0; --tm-dim:#6b7280; --tm-signal:var(--el-color-primary); }
.brand-wordmark { font-size:20px; font-weight:650; letter-spacing:0; }
.login-content { width:100%; max-width:380px; margin:64px auto 0; }
.login-identity { display:flex; flex-direction:column; align-items:center; gap:16px; margin-bottom:36px; --tm-line:#9ba3b0; --tm-dim:#6b7280; --tm-signal:var(--el-color-primary); }
.login-identity h1 { margin:0; font-size:28px; font-weight:600; letter-spacing:0; }
h2 { margin:0 0 24px; font-size:20px; font-weight:600; letter-spacing:0; }
.login-form :deep(.el-form-item__label) { color:#444b57; font-size:14px; }
.login-form :deep(.el-input__wrapper) { min-height:46px; border-radius:6px; background:#fff !important; }
/* Keep autofill aligned with the wrapper and password visibility control. */
.login-form :deep(input:autofill),
.login-form :deep(input:-webkit-autofill) { -webkit-box-shadow:0 0 0 1000px #fff inset !important; box-shadow:0 0 0 1000px #fff inset !important; -webkit-text-fill-color:#242830 !important; caret-color:#242830; }
.login-btn { width:100%; height:46px; margin-top:8px; border-radius:6px; }
.login-error { margin-bottom:20px; }
@media (max-width:600px) { .login-content { margin-top:32px; } .login-brand { min-height:64px; } .login-identity h1 { font-size:26px; } }
@media (max-height:550px) { .login-content { margin-top:24px; } .login-identity { gap:12px; margin-bottom:24px; } }
</style>
