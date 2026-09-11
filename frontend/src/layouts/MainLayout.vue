<template>
  <el-container class="layout">
    <el-aside :width="collapsed ? '64px' : '226px'" class="aside">
      <div class="logo" :class="{ 'logo-collapsed': collapsed }">
        <TargetMark :size="32" :animated="false" class="brand-mark" />
        <span v-show="!collapsed" class="logo-text">测试管理平台</span>
      </div>
      <div v-if="!collapsed" class="nav-search"><el-input v-model="navSearch" :prefix-icon="Search" placeholder="查找功能" aria-label="查找功能" clearable /></div>
      <el-menu :key="`${collapsed}-${!!navSearch}`" ref="menuRef" :default-active="activeMenu"
        :default-openeds="openSubs" :unique-opened="!navSearch" :collapse="collapsed" :collapse-transition="false"
        router class="menu" aria-label="平台导航" background-color="var(--tech-sidebar)" text-color="#bfcbd9" active-text-color="var(--tech-on-dark-accent)">
        <el-sub-menu v-for="group in groups" :key="group.id" :index="group.id">
          <template #title><el-icon><component :is="icons[group.icon]" /></el-icon><span>{{ group.label }}</span></template>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path"><el-icon><component :is="icons[item.icon]" /></el-icon><span>{{ item.label }}</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
      <div v-if="!groups.length && !collapsed" class="nav-empty">无匹配功能</div>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-button class="collapse-btn" text :icon="collapsed ? Expand : Fold" :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" :title="collapsed ? '展开侧栏' : '收起侧栏'" @click="toggleCollapse" />
          <div class="location" aria-label="当前位置"><span class="location-group">{{ currentGroup?.label }}</span><span v-if="currentGroup" class="location-divider">/</span><span class="location-page">{{ pageTitle }}</span></div>
        </div>
        <div class="header-right">
          <span class="role-tag">{{ roleLabel }}</span>
          <el-dropdown @command="onCommand">
            <button class="user" aria-label="账户菜单"><el-avatar :size="28" class="avatar">{{ avatarText }}</el-avatar><span class="uname">{{ auth.user?.name || auth.user?.username }}</span><el-icon><CaretBottom /></el-icon></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="logout">退出登录</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main ref="mainRef" class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/store/auth'
import { visibleNavigation } from '@/utils/navigation'
import { Monitor, Files, List, User, EditPen, DataLine, TrendCharts, Warning, DataAnalysis, CaretBottom, Grid,
  Histogram, Setting, Fold, Expand, MagicStick, Trophy, Collection, Select, Finished, Odometer, Stopwatch,
  Promotion, Aim, Connection, RefreshRight, ChatDotRound, ChatLineSquare, UploadFilled, Cpu, DataBoard,
  Checked, Tickets, Calendar, Link, Filter, Search } from '@element-plus/icons-vue'
import TargetMark from '@/components/TargetMark.vue'

const icons = { Monitor, Files, List, User, EditPen, DataLine, TrendCharts, Warning, DataAnalysis, Grid, Histogram,
  Setting, MagicStick, Trophy, Collection, Select, Finished, Odometer, Stopwatch, Promotion, Aim, Connection,
  RefreshRight, ChatDotRound, ChatLineSquare, UploadFilled, Cpu, DataBoard, Checked, Tickets, Calendar, Link, Filter }
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const narrowQuery = window.matchMedia('(max-width: 700px)')
const narrow = ref(narrowQuery.matches)
const desktopCollapsed = ref(localStorage.getItem('tp_sidebar_collapsed') === '1')
const mobileCollapsed = ref(true)
const collapsed = computed(() => narrow.value ? mobileCollapsed.value : desktopCollapsed.value)
function onViewportChange(event) {
  narrow.value = event.matches
  if (event.matches) mobileCollapsed.value = true
}
narrowQuery.addEventListener('change', onViewportChange)
onBeforeUnmount(() => narrowQuery.removeEventListener('change', onViewportChange))
const navSearch = ref('')
const menuRef = ref(null)
const mainRef = ref(null)
const activeMenu = computed(() => route.path.startsWith('/perf-collect/') ? '/perf-report' : '/' + (route.path.split('/')[1] || 'dashboard'))
const showMyReports = computed(() => !!auth.user && (auth.isPlatformAdmin || auth.memberships.some(m => m.role !== 'guest')))
const permittedGroups = computed(() => visibleNavigation({ admin: auth.isPlatformAdmin, reports: showMyReports.value }))
const groups = computed(() => visibleNavigation({ admin: auth.isPlatformAdmin, reports: showMyReports.value, search: navSearch.value }))
const currentGroup = computed(() => permittedGroups.value.find(g => g.items.some(item => item.path === activeMenu.value)))
const pageTitle = computed(() => route.meta.title || currentGroup.value?.items.find(item => item.path === activeMenu.value)?.label || '工作台')
const openSubs = computed(() => navSearch.value ? groups.value.map(g => g.id) : (currentGroup.value ? [currentGroup.value.id] : []))
function toggleCollapse() {
  if (narrow.value) mobileCollapsed.value = !mobileCollapsed.value
  else {
    desktopCollapsed.value = !desktopCollapsed.value
    localStorage.setItem('tp_sidebar_collapsed', desktopCollapsed.value ? '1' : '0')
  }
  navSearch.value = ''
}
watch([openSubs, collapsed], async () => {
  await nextTick()
  if (!collapsed.value) for (const id of openSubs.value) menuRef.value?.open(id)
})
watch(() => route.path, async () => {
  navSearch.value = ''
  if (narrow.value) mobileCollapsed.value = true
  await nextTick()
  mainRef.value?.$el?.scrollTo({ top: 0, left: 0 })
})
const avatarText = computed(() => (auth.user?.name || auth.user?.username || '?').slice(0, 1).toUpperCase())
const roleLabel = computed(() => !auth.user ? '' : auth.isPlatformAdmin ? '平台管理员' : '项目成员')
function onCommand(cmd) {
  if (cmd === 'logout') { auth.logout(); router.push('/login') }
}
</script>

<style scoped>
.layout { height: 100vh; height: 100dvh; }
.aside { background: var(--tech-sidebar); border-right: 1px solid #30343b; transition: width .25s ease; height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.logo { flex: none; height: 60px; display: flex; align-items: center; gap: 8px; padding: 0 14px; color: #fff; white-space: nowrap; }
.logo-collapsed { padding: 0; justify-content: center; }
.logo-text { font-size: 15px; font-weight: 600; letter-spacing: 0; }
.brand-mark { --tm-line: #668ff1; --tm-dim: #92b1f5; --tm-signal: var(--tech-on-dark-accent); }
.nav-search { padding: 8px 12px 16px; }
/* Keep the dark sidebar input independent of global light form backgrounds. */
.nav-search :deep(.el-input__wrapper),
.nav-search :deep(.el-input__wrapper:hover),
.nav-search :deep(.el-input__wrapper:focus-within) {
  background-color: #2b2f36 !important;
  box-shadow: 0 0 0 1px #59616e inset;
}
.nav-search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #92b1f5 inset, 0 0 0 2px rgba(146, 177, 245, .2) !important;
}
.nav-search :deep(.el-input__inner) { color: #f3f5f7; caret-color: #f3f5f7; }
.nav-search :deep(.el-input__inner::placeholder) { color: #b8c1ce; opacity: 1; }
.nav-search :deep(.el-input__prefix), .nav-search :deep(.el-input__suffix) { color: #b8c1ce; }
.nav-search :deep(.el-input__clear:hover) { color: #fff; }
.menu { border-right: none; flex: 1; overflow-y: auto; overflow-x: hidden; }
.menu:not(.el-menu--collapse) { width: 226px; }
.menu.el-menu--collapse { width: 64px; }
.menu :deep(.el-sub-menu__title), .menu :deep(.el-menu-item) { height: 44px; line-height: 44px; font-size: 13px; }
.menu :deep(.el-sub-menu__title:hover), .menu :deep(.el-menu-item:hover) { background-color: var(--tech-sidebar-hover) !important; }
.menu :deep(.el-menu-item.is-active) { background-color: var(--tech-sidebar-hover) !important; border-left: 3px solid var(--tech-on-dark-accent); }
.menu :deep(.el-menu-item.is-active .el-icon) { color: var(--tech-on-dark-accent); }
.nav-empty { color: #aeb8c4; font-size: 12px; padding: 16px; }
.header { display: flex; align-items: center; justify-content: space-between; gap: 16px; background: #fff; border-bottom: 1px solid #ebeef5; }
.header-left, .header-right { display: flex; align-items: center; gap: 14px; min-width: 0; }
.header-left { flex: 1; }
.collapse-btn { font-size: 20px; flex: none; padding: 6px; }
.location { display: flex; align-items: center; gap: 12px; font-size: 13px; min-width: 0; }
.location-group, .location-divider { color: #68717d; white-space: nowrap; }
.location-page { color: #202329; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.role-tag { font-size: 12px; color: #68717d; white-space: nowrap; }
.user { border: 0; padding: 0; background: none; cursor: pointer; color: #303133; display: flex; align-items: center; gap: 8px; }
.avatar { background: var(--tech-signal); color: #fff; font-size: 13px; font-weight: 600; }
.uname { font-size: 14px; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.main { background: var(--tech-bg); padding: 20px; }
@media (max-width: 700px) { .header { padding: 0 12px; gap: 8px; } .header-left { gap: 8px; } .location-group, .location-divider, .role-tag, .uname { display: none; } }
</style>
