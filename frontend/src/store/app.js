import { defineStore } from 'pinia'
import { listProjects } from '@/api'

/**
 * 全局应用状态：路由级 loading + 项目列表进程内缓存。
 * start() 延迟 80ms 才显示遮罩，避免快导航闪烁；stop() 立即隐藏。
 * 加最大显示时限 6s 兜底，防止 start 后因异常没收到 stop 导致遮罩卡死拦截点击。
 *
 * 项目列表在多页 onMounted 反复拉取且基本不变,这里做进程内缓存:
 * fetchProjects() 命中缓存直接返回;写操作(增/改项目)后调 invalidateProjects() 失效。
 */
export const useAppStore = defineStore('app', {
  state: () => ({
    _pending: false,
    visible: false,
    _timer: null,
    _maxTimer: null,
    projects: [],
    _projectsLoaded: false,
    _projectsPromise: null,   // 并发去重:多页同时首拉只发一个请求
  }),
  actions: {
    start() {
      this._pending = true
      if (this._timer) clearTimeout(this._timer)
      this._timer = setTimeout(() => {
        if (this._pending) this.visible = true
      }, 80)
      // 兜底：6s 后若仍 pending，强制隐藏，避免遮罩永久卡住拦截点击
      if (this._maxTimer) clearTimeout(this._maxTimer)
      this._maxTimer = setTimeout(() => {
        if (this._pending) { this._pending = false; this.visible = false }
      }, 6000)
    },
    stop() {
      this._pending = false
      if (this._timer) { clearTimeout(this._timer); this._timer = null }
      if (this._maxTimer) { clearTimeout(this._maxTimer); this._maxTimer = null }
      this.visible = false
    },

    /**
     * 取项目列表(带缓存)。force=true 强制重取。
     * 返回数组;失败时抛出(与直接调 listProjects 行为一致,交调用方 try/catch)。
     */
    async fetchProjects(force = false) {
      if (!force && this._projectsLoaded) return this.projects
      if (this._projectsPromise) return this._projectsPromise   // 并发首拉复用
      this._projectsPromise = listProjects()
        .then((list) => {
          this.projects = list || []
          this._projectsLoaded = true
          return this.projects
        })
        .finally(() => { this._projectsPromise = null })
      return this._projectsPromise
    },

    /** 项目增/改后调用:清缓存,下次 fetchProjects 会重取。 */
    invalidateProjects() {
      this._projectsLoaded = false
      this._projectsPromise = null
    },
  },
})
