/**
 * 记住用户上次选中的项目（跨页面全局共享）。
 *
 * 各带项目下拉的页面（任务/日报/统计/遗留问题/AI 生成）进入时，优先选回
 * 上次选中的项目；切换项目时写回。命中不到（首次无记录，或该项目已被移出
 * 可见列表）时由调用方回退到列表第一个。
 *
 * 存于 localStorage，key 沿用平台 tp_ 前缀。
 */
const KEY = 'tp_last_project'

export function getLastProjectId() {
  const v = localStorage.getItem(KEY)
  if (v === null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export function setLastProjectId(id) {
  if (id === null || id === undefined) return
  localStorage.setItem(KEY, String(id))
}

/**
 * 从已加载的项目列表里挑默认选中项：优先上次选中，否则第一个。
 * @param {Array<{id:number}>} projects 当前可见项目列表
 * @returns {number|null} 选中的项目 id；列表为空时 null
 */
export function pickDefaultProjectId(projects) {
  if (!projects || !projects.length) return null
  const saved = getLastProjectId()
  const hit = projects.find((p) => p.id === saved)
  return hit ? hit.id : projects[0].id
}
