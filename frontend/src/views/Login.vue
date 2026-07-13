<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <div class="brand">
        <Logo :size="56" :animated="false" />
        <h2 class="title">测试管理平台</h2>
        <p class="sub">Test Management Platform</p>
      </div>
      <el-form :model="form" @submit.prevent="onLogin" label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" />
        </el-form-item>
        <el-button type="primary" :loading="loading" class="login-btn" @click="onLogin">登 录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/store/auth'
import Logo from '@/components/Logo.vue'

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
.login-wrap {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f2d3d 0%, #2c3e50 100%);
}
.login-card {
  width: 380px;
  padding: 28px 24px 18px;
  border: none;
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3);
}
.brand {
  text-align: center;
  margin-bottom: 18px;
}
.title {
  text-align: center;
  margin: 12px 0 0;
  color: #1f2d3d;
  font-weight: 700;
}
.sub {
  text-align: center;
  color: #909399;
  margin: 4px 0 0;
  font-size: 12px;
  letter-spacing: 1px;
}
.login-btn {
  width: 100%;
  height: 42px;
  font-size: 15px;
  letter-spacing: 2px;
}
</style>
