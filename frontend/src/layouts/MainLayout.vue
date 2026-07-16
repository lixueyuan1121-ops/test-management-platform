<template>
  <el-container class="layout">
    <el-aside width="226px" class="aside">
      <div class="logo">
        <Logo :size="34" :animated="false" />
        <span class="logo-text">测试管理平台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :default-openeds="openSubs"
        router
        class="menu"
        background-color="#1f2d3d"
        text-color="#bfcbd9"
        active-text-color="#409eff"
      >
        <!-- 概览 -->
        <el-menu-item index="/dashboard">
          <el-icon><Monitor /></el-icon><span>工作台</span>
        </el-menu-item>

        <!-- 组织管理 -->
        <el-sub-menu v-if="auth.isPlatformAdmin" index="org">
          <template #title><el-icon><OfficeBuilding /></el-icon><span>组织管理</span></template>
          <el-menu-item index="/projects"><el-icon><Files /></el-icon><span>项目管理</span></el-menu-item>
          <el-menu-item index="/users"><el-icon><User /></el-icon><span>用户管理</span></el-menu-item>
        </el-sub-menu>

        <!-- 任务执行 -->
        <el-sub-menu v-if="auth.isPlatformAdmin || showMyReports" index="exec">
          <template #title><el-icon><Checked /></el-icon><span>任务执行</span></template>
          <el-menu-item v-if="auth.isPlatformAdmin" index="/tasks"><el-icon><List /></el-icon><span>任务分配</span></el-menu-item>
          <el-menu-item v-if="showMyReports" index="/my-reports"><el-icon><EditPen /></el-icon><span>我的日报</span></el-menu-item>
        </el-sub-menu>

        <!-- 数据统计 -->
        <el-sub-menu index="stats">
          <template #title><el-icon><DataLine /></el-icon><span>数据统计</span></template>
          <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon><span>日报统计</span></el-menu-item>
          <el-menu-item index="/workload"><el-icon><TrendCharts /></el-icon><span>工作量统计</span></el-menu-item>
          <el-menu-item index="/issues"><el-icon><Warning /></el-icon><span>遗留问题</span></el-menu-item>
        </el-sub-menu>

        <!-- 测试工具广场 -->
        <el-sub-menu index="tools">
          <template #title><el-icon><Grid /></el-icon><span>测试工具广场</span></template>
          <el-menu-item index="/tool-plaza"><el-icon><Histogram /></el-icon><span>工具广场</span></el-menu-item>
          <el-menu-item v-if="auth.isPlatformAdmin" index="/tool-admin"><el-icon><Setting /></el-icon><span>工具配置</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <span class="role-tag">{{ roleLabel }}</span>
        </div>
        <div class="header-right">
          <el-dropdown @command="onCommand">
            <span class="user">
              <el-avatar :size="28" class="avatar">{{ avatarText }}</el-avatar>
              <span class="uname">{{ auth.user?.name || auth.user?.username }}</span>
              <el-icon><CaretBottom /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/auth'
import {
  Monitor, Files, List, User, EditPen, DataLine, TrendCharts, Warning,
  OfficeBuilding, Checked, DataAnalysis, CaretBottom, Grid, Histogram, Setting,
} from '@element-plus/icons-vue'
import Logo from '@/components/Logo.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const activeMenu = computed(() => '/' + (route.path.split('/')[1] || 'dashboard'))
const openSubs = ['org', 'exec', 'stats', 'tools']

const avatarText = computed(() => {
  const n = auth.user?.name || auth.user?.username || '?'
  return n.slice(0, 1).toUpperCase()
})
const roleLabel = computed(() => {
  if (!auth.user) return ''
  if (auth.user.is_platform_admin) return '平台管理员'
  return '项目成员'
})
const showMyReports = computed(() => {
  if (!auth.user) return false
  if (auth.user.is_platform_admin) return true
  return auth.memberships.some((m) => m.role !== 'guest')
})

function onCommand(cmd) {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1f2d3d; box-shadow: 2px 0 8px rgba(0,0,0,0.08); }
.logo {
  height: 60px; display: flex; align-items: center; gap: 8px;
  padding: 0 14px; color: #fff;
  animation: fadeInUp 0.5s ease-out both;
}
.logo-text { font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }
.menu { border-right: none; }
.menu :deep(.el-sub-menu__title:hover),
.menu :deep(.el-menu-item:hover) { background-color: #263445 !important; }
.menu :deep(.el-menu-item.is-active) { background-color: #263445 !important; }

.header {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #ebeef5;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.role-tag {
  font-size: 13px; color: #606266;
  padding: 3px 12px; border: 1px solid #dcdfe6; border-radius: 12px;
  background: #f4f4f5;
}
.user { cursor: pointer; color: #303133; display: flex; align-items: center; gap: 8px; }
.avatar { background: #409eff; color: #fff; font-size: 13px; font-weight: 600; }
.uname { font-size: 14px; }
.main { background: #f0f2f5; padding: 20px; }
</style>
