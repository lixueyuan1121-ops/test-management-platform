<template>
  <el-container class="layout">
    <el-aside :width="collapsed ? '64px' : '226px'" class="aside">
      <div class="logo" :class="{ 'logo-collapsed': collapsed }">
        <TargetMark :size="32" :animated="false" class="brand-mark" />
        <span v-show="!collapsed" class="logo-text">测试管理平台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :default-openeds="openSubs"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        class="menu"
        background-color="#1f2d3d"
        text-color="#bfcbd9"
        active-text-color="#00e5a0"
      >
        <!-- 概览 -->
        <el-menu-item index="/dashboard">
          <el-icon><Monitor /></el-icon><span>工作台</span>
        </el-menu-item>

        <!-- QA Copilot：AI 测试助手（展示位，全员可见） -->
        <el-menu-item index="/ai-testgen" class="ai-entry">
          <el-icon><MagicStick /></el-icon><span>AI 测试助手</span>
        </el-menu-item>

        <!-- 用例库：跨批次回溯已生成 / 已采纳测试点（全员可见，只读） -->
        <el-menu-item index="/case-library">
          <el-icon><Collection /></el-icon><span>用例库</span>
        </el-menu-item>

        <el-menu-item index="/adopted-cases">
          <el-icon><Select /></el-icon><span>已采纳用例</span>
        </el-menu-item>

        <!-- 组织管理 -->
        <el-sub-menu v-if="auth.isPlatformAdmin" index="org">
          <template #title><el-icon><OfficeBuilding /></el-icon><span>组织管理</span></template>
          <el-menu-item index="/projects"><el-icon><Files /></el-icon><span>项目管理</span></el-menu-item>
          <el-menu-item index="/users"><el-icon><User /></el-icon><span>用户管理</span></el-menu-item>
        </el-sub-menu>

        <!-- 任务执行 -->
        <el-sub-menu index="exec">
          <template #title><el-icon><Checked /></el-icon><span>任务执行</span></template>
          <el-menu-item v-if="auth.isPlatformAdmin" index="/tasks"><el-icon><List /></el-icon><span>任务分配</span></el-menu-item>
          <el-menu-item v-if="showMyReports" index="/my-reports"><el-icon><EditPen /></el-icon><span>我的日报</span></el-menu-item>
          <el-menu-item index="/my-devices"><el-icon><Monitor /></el-icon><span>我的设备</span></el-menu-item>
          <el-menu-item index="/exec-results"><el-icon><Finished /></el-icon><span>执行结果</span></el-menu-item>
        </el-sub-menu>

        <!-- 数据统计 -->
        <el-sub-menu index="stats">
          <template #title><el-icon><DataLine /></el-icon><span>数据统计</span></template>
          <el-menu-item index="/stats"><el-icon><DataAnalysis /></el-icon><span>日报统计</span></el-menu-item>
          <el-menu-item index="/workload"><el-icon><TrendCharts /></el-icon><span>工作量统计</span></el-menu-item>
          <el-menu-item index="/ai-wall" class="ai-entry"><el-icon><Trophy /></el-icon><span>AI 战绩墙</span></el-menu-item>
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
          <el-icon class="collapse-btn" :title="collapsed ? '展开侧栏' : '收起侧栏'" @click="toggleCollapse">
            <component :is="collapsed ? Expand : Fold" />
          </el-icon>
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
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/auth'
import {
  Monitor, Files, List, User, EditPen, DataLine, TrendCharts, Warning,
  OfficeBuilding, Checked, DataAnalysis, CaretBottom, Grid, Histogram, Setting,
  Fold, Expand, MagicStick, Trophy, Collection, Select, Finished,
} from '@element-plus/icons-vue'
import TargetMark from '@/components/TargetMark.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// 侧栏折叠：状态持久化，刷新保持
const collapsed = ref(localStorage.getItem('tp_sidebar_collapsed') === '1')
function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem('tp_sidebar_collapsed', collapsed.value ? '1' : '0')
}

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
.aside {
  background: #1f2d3d; box-shadow: 2px 0 8px rgba(0,0,0,0.08);
  transition: width 0.25s ease;
  overflow: hidden;
}
.logo {
  height: 60px; display: flex; align-items: center; gap: 8px;
  padding: 0 14px; color: #fff; white-space: nowrap;
  animation: fadeInUp 0.5s ease-out both;
}
.logo-collapsed { padding: 0; justify-content: center; }
.logo-text { font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }
/* 靶心图标：深色侧栏上提亮 + 上色，保证清晰 */
.brand-mark {
  --tm-line: #4fd8c4;     /* 外靶环/取景框：亮青 */
  --tm-dim: #3b9ad9;      /* 内环/准星：亮蓝 */
  --tm-signal: #00e5a0;   /* 扫描环 + 对勾：信号青绿 */
  filter: drop-shadow(0 0 5px rgba(0, 229, 160, 0.35));
}
.menu { border-right: none; }
/* 折叠态菜单宽度对齐 aside(64px)，消除默认 200px 造成的横向溢出 */
.menu:not(.el-menu--collapse) { width: 226px; }
.menu.el-menu--collapse { width: 64px; }
.menu :deep(.el-sub-menu__title:hover),
.menu :deep(.el-menu-item:hover) { background-color: #263445 !important; }
.menu :deep(.el-menu-item.is-active) {
  background-color: #263445 !important;
  border-left: 3px solid #00e5a0;
}
/* 激活态图标也染青绿 */
.menu :deep(.el-menu-item.is-active .el-icon) { color: #00e5a0; }
/* AI 入口：微微高亮，暗示这是"秀肌肉"的能力位 */
.menu :deep(.ai-entry span) {
  background: linear-gradient(90deg, #00e5a0, #3b9ad9);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  font-weight: 600;
}

.header {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #ebeef5;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.header-left { display: flex; align-items: center; gap: 14px; }
.collapse-btn {
  font-size: 20px; color: #606266; cursor: pointer;
  padding: 6px; border-radius: 6px;
  transition: color 0.15s ease, background 0.15s ease;
}
.collapse-btn:hover { color: #00b386; background: rgba(0,179,134,.08); }
.role-tag {
  font-size: 12px; color: #00926e; letter-spacing: .5px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  padding: 3px 12px; border: 1px solid rgba(0,179,134,.3); border-radius: 4px;
  background: rgba(0,179,134,.06);
}
.user { cursor: pointer; color: #303133; display: flex; align-items: center; gap: 8px; }
.avatar { background: #00b386; color: #fff; font-size: 13px; font-weight: 600; }
.uname { font-size: 14px; }
.main { background: #f0f2f5; padding: 20px; }
</style>
