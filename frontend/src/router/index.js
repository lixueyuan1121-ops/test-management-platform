import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/store/auth'
import MainLayout from '@/layouts/MainLayout.vue'

const routes = [
  { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
      { path: 'ai-testgen', name: 'ai-testgen', component: () => import('@/views/AITestGen.vue') },
      { path: 'case-library', name: 'case-library', component: () => import('@/views/CaseLibrary.vue') },
      { path: 'adopted-cases', name: 'adopted-cases', component: () => import('@/views/AdoptedCases.vue') },
      { path: 'projects', name: 'projects', component: () => import('@/views/Projects.vue'), meta: { platformAdmin: true } },
      { path: 'users', name: 'users', component: () => import('@/views/Users.vue'), meta: { platformAdmin: true } },
      { path: 'projects/:id/members', name: 'project-members', component: () => import('@/views/Members.vue') },
      { path: 'tasks', name: 'tasks', component: () => import('@/views/Tasks.vue') },
      { path: 'my-reports', name: 'my-reports', component: () => import('@/views/MyReports.vue') },
      { path: 'my-devices', name: 'my-devices', component: () => import('@/views/MyDevices.vue') },
      { path: 'exec-results', name: 'exec-results', component: () => import('@/views/ExecResults.vue') },
      { path: 'stats', name: 'stats', component: () => import('@/views/DailyStats.vue') },
      { path: 'workload', name: 'workload', component: () => import('@/views/WorkloadStats.vue') },
      { path: 'ai-wall', name: 'ai-wall', component: () => import('@/views/AIWall.vue'), meta: { title: 'AI 战绩墙' } },
      { path: 'issues', name: 'issues', component: () => import('@/views/Issues.vue') },
      { path: 'tool-plaza', name: 'tool-plaza', component: () => import('@/views/ToolPlaza.vue') },
      { path: 'tool-admin', name: 'tool-admin', component: () => import('@/views/ToolAdmin.vue'), meta: { platformAdmin: true } },
      { path: 'releases', name: 'releases', component: () => import('@/views/ReleaseNotes.vue'), meta: { title: '发版记录' } },
      { path: 'selectors', name: 'selectors', component: () => import('@/views/SelectorAdmin.vue'), meta: { title: '选择器管理' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  if (!auth.isLoggedIn) return { path: '/login' }
  if (!auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      auth.logout()
      return { path: '/login' }
    }
  }
  if (to.meta.platformAdmin && !auth.isPlatformAdmin) {
    return { path: '/dashboard' }
  }
  return true
})

export default router
