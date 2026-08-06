<template>
  <div class="login-page">
    <!-- 左：控制台品牌区 -->
    <section class="console-side" aria-hidden="true">
      <div class="grid-bg"></div>

      <div class="console-top">
        <span class="sys-tag">TMP / TEST MANAGEMENT</span>
        <span class="sys-status"><i class="dot"></i>SYSTEM // READY</span>
      </div>

      <div class="console-core">
        <TargetMark :size="180" :animated="true" />
        <h2 class="console-title">质量在此校准</h2>
        <p class="console-sub">CALIBRATE · EXECUTE · VERIFY</p>
      </div>

      <div class="console-foot">
        <span>v0.1.0</span>
        <span>SECURE CHANNEL</span>
      </div>
    </section>

    <!-- 右：登录表单 -->
    <section class="form-side">
      <div class="form-inner">
        <div class="form-eyebrow">// AUTHENTICATION</div>
        <h1 class="form-title">登录</h1>
        <p class="form-sub">输入凭证以接入平台</p>

        <el-form :model="form" @submit.prevent="onLogin" label-position="top" class="login-form">
          <el-form-item label="用户名 / USERNAME">
            <el-input
              v-model="form.username"
              size="large"
              placeholder="username"
              autocomplete="username"
              @keyup.enter="onLogin"
            />
          </el-form-item>
          <el-form-item label="密码 / PASSWORD">
            <el-input
              v-model="form.password"
              type="password"
              size="large"
              show-password
              placeholder="password"
              autocomplete="current-password"
              @keyup.enter="onLogin"
            />
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            class="login-btn"
            @click="onLogin"
          >{{ loading ? '验证中…' : '接入平台 →' }}</el-button>
        </el-form>
      </div>
    </section>
  </div>
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
const form = reactive({ username: '', password: '' })

async function onLogin() {
  if (!form.username || !form.password) { ElMessage.warning('请输入用户名和密码'); return }
  loading.value = true
  try { await auth.login(form.username, form.password); router.push('/dashboard') }
  catch (e) {}
  finally { loading.value = false }
}
</script>

<style scoped>
/* 工业风 token：近黑 + 单色 + 信号绿点缀。仅作用于本页 */
.login-page {
  --ink: #0d0f12;
  --panel: #14171c;
  --line: #2a2f37;
  --dim: #4a525c;
  --fg: #e6e8ea;
  --muted: #7d858f;
  --signal: #00e5a0;

  /* 传给 TargetMark 的 SVG 配色变量 */
  --tm-line: var(--line);
  --tm-dim: var(--dim);
  --tm-signal: var(--signal);

  height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  background: var(--ink);
  color: var(--fg);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  overflow: hidden;
}

/* ---------- 左：控制台 ---------- */
.console-side {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 32px 36px;
  background: var(--panel);
  border-right: 1px solid var(--line);
  overflow: hidden;
}
/* 网格底纹 */
.grid-bg {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 44px 44px;
  opacity: 0.22;
  mask-image: radial-gradient(120% 90% at 50% 45%, #000 40%, transparent 85%);
  -webkit-mask-image: radial-gradient(120% 90% at 50% 45%, #000 40%, transparent 85%);
}
.console-top, .console-foot {
  position: relative;
  z-index: 2;
  display: flex;
  justify-content: space-between;
  font-family: 'JetBrains Mono', 'SFMono-Regular', ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: 1.5px;
  color: var(--muted);
}
.sys-status { display: inline-flex; align-items: center; gap: 7px; color: var(--fg); }
.dot {
  width: 7px; height: 7px;
  background: var(--signal);
  box-shadow: 0 0 8px var(--signal);
  animation: pulse 1.8s ease-in-out infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }

.console-core {
  position: relative;
  z-index: 2;
  text-align: center;
  animation: rise 0.6s ease-out both;
}
.console-title {
  margin: 26px 0 0;
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--fg);
}
.console-sub {
  margin: 12px 0 0;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  letter-spacing: 5px;
  color: var(--signal);
}
.console-foot span:last-child { color: var(--dim); }

/* ---------- 右：表单 ---------- */
.form-side {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background: var(--ink);
}
.form-inner {
  width: 100%;
  max-width: 348px;
  animation: rise 0.6s ease-out 0.08s both;
}
.form-eyebrow {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  letter-spacing: 3px;
  color: var(--signal);
  margin-bottom: 14px;
}
.form-title {
  margin: 0;
  font-size: 30px;
  font-weight: 700;
  letter-spacing: 3px;
  color: var(--fg);
}
.form-sub {
  margin: 10px 0 30px;
  font-size: 13px;
  color: var(--muted);
}

/* 工业风表单：直角、等宽 label、下划线式输入 */
.login-form :deep(.el-form-item__label) {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: 1.5px;
  color: var(--muted);
}
.login-form :deep(.el-input__wrapper) {
  background-color: transparent !important;
  box-shadow: none !important;
  border: 1px solid var(--line);
  border-radius: 0;
  padding: 4px 12px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.login-form :deep(.el-input__inner) {
  color: var(--fg);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  letter-spacing: 0.5px;
}
.login-form :deep(.el-input__inner::placeholder) { color: #565e68; }
.login-form :deep(.el-input__wrapper.is-focus),
.login-form :deep(.el-input__wrapper:focus-within) {
  border-color: var(--signal) !important;
  box-shadow: 0 0 0 3px rgba(0, 229, 160, 0.12) !important;
}
.login-form :deep(.el-input__password) { color: var(--muted); }

/* 接入按钮：锐角、信号绿描边、hover 反白 */
.login-btn {
  width: 100%;
  height: 48px;
  margin-top: 10px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 14px;
  letter-spacing: 3px;
  border-radius: 0;
  color: var(--ink);
  background: var(--signal);
  border: 1px solid var(--signal);
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.2s ease;
}
.login-btn:hover {
  background: transparent;
  color: var(--signal);
  box-shadow: 0 0 18px rgba(0, 229, 160, 0.25);
}
.login-btn:active { transform: translateY(1px); }

@keyframes rise {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ---------- 移动端：单列 ---------- */
@media (max-width: 860px) {
  .login-page { grid-template-columns: 1fr; }
  .console-side {
    min-height: 260px;
    max-height: 42dvh;
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
  .console-title { font-size: 22px; margin-top: 16px; }
  .console-sub { letter-spacing: 3px; }
}

@media (prefers-reduced-motion: reduce) {
  .console-core, .form-inner { animation: none; }
  .dot { animation: none; }
}
</style>
