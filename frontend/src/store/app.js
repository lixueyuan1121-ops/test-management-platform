import { defineStore } from 'pinia'

/**
 * 全局应用状态：路由级 loading。
 * start() 延迟 80ms 才显示遮罩，避免快导航闪烁；stop() 立即隐藏。
 * 加最大显示时限 6s 兜底，防止 start 后因异常没收到 stop 导致遮罩卡死拦截点击。
 */
export const useAppStore = defineStore('app', {
  state: () => ({
    _pending: false,
    visible: false,
    _timer: null,
    _maxTimer: null,
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
  },
})
