/**
 * 清理 Element Plus 残留遮罩（修复「页面失焦、点击无反应、刷新恢复」）。
 *
 * 仅在路由切换后调用（router.afterEach），不对正在显示或过渡中的遮罩下手，
 * 避免误删刚打开的 el-dialog（其 enter-from 阶段 opacity 也可能为 0）。
 *
 * 只清理「确定已损坏」的：
 *  - v-loading mask：display 非 none 且 opacity=0（v-loading 是指令挂载，关闭失败才残留）
 *  - 对话框/抽屉遮罩：完全无内容（孤儿），或内部已是 display:none/visibility:hidden
 *  - body 上卡死的 el-popup-parent--hidden（无可见 dialog 时清除）
 *
 * 注意：不依赖"opacity=0"判断 dialog 遮罩，因为正常打开瞬间 opacity 也为 0。
 */
export function cleanupResidue() {
  try {
    // 1) v-loading 表格遮罩（指令管理）→ 直接删损坏态
    document.querySelectorAll('.el-loading-mask').forEach((el) => {
      const cs = getComputedStyle(el)
      if (cs.display !== 'none' && parseFloat(cs.opacity || '1') === 0) el.remove()
    })

    // 2) 对话框/抽屉遮罩：只删"无可见内容"的确定孤儿，不动 opacity
    let hasVisibleDialog = false
    document.querySelectorAll('.el-overlay-dialog, .el-overlay-drawer, .el-overlay').forEach((el) => {
      const cs = getComputedStyle(el)
      if (cs.display === 'none') return
      const inner = el.querySelector('.el-dialog, .el-drawer')
      if (!inner) { el.remove(); return } // 完全无内容 → 孤儿遮罩
      const ics = getComputedStyle(inner)
      if (ics.display === 'none' || ics.visibility === 'hidden') {
        // 内部已隐藏但遮罩还显示 = 残留
        el.remove()
        return
      }
      hasVisibleDialog = true
    })

    // 3) body 上卡死的 el-popup-parent--hidden（无可见 dialog 时清除）
    if (!hasVisibleDialog) {
      document.body.classList.remove('el-popup-parent--hidden')
    }
  } catch (e) {
    // 忽略，不影响主流程
  }
}
