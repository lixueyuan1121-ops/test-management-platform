import { defineStore } from 'pinia'
import { login as apiLogin, getMe } from '@/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('tp_token') || '',
    user: null,
    memberships: [],
  }),
  getters: {
    isPlatformAdmin: (s) => !!s.user?.is_platform_admin,
    isLoggedIn: (s) => !!s.token,
    // 当前选中项目的角色
    roleIn: (s) => (projectId) => {
      if (!s.user) return null
      if (s.user.is_platform_admin) return 'admin'
      const m = s.memberships.find((x) => x.project_id === projectId)
      return m ? m.role : null
    },
  },
  actions: {
    async login(username, password) {
      const data = await apiLogin(username, password)
      this.token = data.access_token
      localStorage.setItem('tp_token', this.token)
      await this.fetchMe()
    },
    async fetchMe() {
      const data = await getMe()
      this.user = data.user
      this.user.is_platform_admin = data.is_platform_admin
      this.memberships = data.memberships || []
      return data
    },
    logout() {
      this.token = ''
      this.user = null
      this.memberships = []
      localStorage.removeItem('tp_token')
    },
  },
})
