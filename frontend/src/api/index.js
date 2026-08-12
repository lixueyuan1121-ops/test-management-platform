import http from './http'

export const login = (username, password) =>
  http.post('/auth/login', { username, password })

export const getMe = () => http.get('/auth/me')

export const listProjects = () => http.get('/projects')
export const createProject = (data) => http.post('/projects', data)
export const updateProject = (id, data) => http.patch(`/projects/${id}`, data)

export const listMembers = (pid) => http.get(`/projects/${pid}/members`)
export const addMember = (pid, data) => http.post(`/projects/${pid}/members`, data)
export const updateMember = (pid, uid, data) => http.patch(`/projects/${pid}/members/${uid}`, data)
export const removeMember = (pid, uid) => http.delete(`/projects/${pid}/members/${uid}`)

export const listUsers = (keyword) => http.get('/users', { params: { keyword } })
export const createUser = (data) => http.post('/users', data)
export const updateUser = (id, data) => http.patch(`/users/${id}`, data)
export const resetPassword = (id, data) => http.patch(`/users/${id}/password`, data)

// ===== P1: 任务 / 日报 / 统计 =====
export const listTasks = (params) => http.get('/tasks', { params })
export const createTask = (data) => http.post('/tasks', data)
export const updateTask = (id, data) => http.patch(`/tasks/${id}`, data)
export const deleteTask = (id) => http.delete(`/tasks/${id}`)
export const copyYesterday = (project_id, target_date) => http.post('/tasks/copy', null, { params: { project_id, target_date } })

export const listReports = (project_id, date) => http.get('/daily-reports', { params: { project_id, date } })
export const upsertReport = (data) => http.post('/daily-reports', data)

export const dailyStats = (project_id, date) => http.get('/stats/daily', { params: { project_id, date } })

// 首页跨项目汇总：今日 KPI + 近 7 天趋势（平台管理员看全部/成员看参与项目）
export const overviewStats = (date) => http.get('/stats/overview', { params: { date } })

// ===== P2: 工作量统计 + 遗留问题 =====
export const workloadStats = (project_id, from, to) => http.get('/stats/workload', { params: { project_id, from, to } })

// AI 战绩墙聚合（返回已解包 data）；params: { from: 'YYYY-MM-DD', to: 'YYYY-MM-DD' }
export const aiStats = (params) => http.get('/stats/ai', { params })
export const listIssues = (project_id, status) => http.get('/issues', { params: { project_id, status } })
export const updateIssue = (id, data) => http.patch(`/issues/${id}`, data)

// ===== 测试工具广场 =====
export const listCategories = (include_inactive) => {
  const params = {}
  if (include_inactive) params.include_inactive = 'true'
  return http.get('/tools/categories', { params })
}
export const createCategory = (data) => http.post('/tools/categories', data)
export const updateCategory = (id, data) => http.patch(`/tools/categories/${id}`, data)
export const deleteCategory = (id) => http.delete(`/tools/categories/${id}`)
export const listTools = (params = {}) => {
  const p = {}
  if (params.category_id) p.category_id = params.category_id
  if (params.online_only) p.online_only = 'true'
  if (params.include_inactive) p.include_inactive = 'true'
  return http.get('/tools', { params: p })
}
export const createTool = (data) => http.post('/tools', data)
export const updateTool = (id, data) => http.patch(`/tools/${id}`, data)
export const deleteTool = (id) => http.delete(`/tools/${id}`)
export const toggleTool = (id) => http.patch(`/tools/${id}/toggle`)

// ===== QA Copilot：AI 生成测试点 =====
export const aiStatus = () => http.get('/ai/status')
export const listAiTasks = (project_id, limit = 20) => http.get('/ai/tasks', { params: { project_id, limit } })
export const listAiCases = (aid) => http.get(`/ai/tasks/${aid}/cases`)
// 跨批次查询测试点（用例库 / 日报已采纳用例共用）；params: project_id, task_id?, review_status?, category?, keyword?
export const listCases = (params) => http.get('/ai/cases', { params })
// 评审测试点：review_status ∈ 'adopted' | 'rejected' | 'pending'（返回已解包的测试点 data）
export const reviewTestcase = (id, review_status) => http.patch(`/ai/testcases/${id}`, { review_status })

// ===== 验收清单（测试点回流任务）=====
export const getChecklistSummary = (project_id, date) => http.get('/tasks/checklist-summary', { params: { project_id, date } })
export const getTaskChecklist = (tid) => http.get(`/tasks/${tid}/checklist`)
export const attachChecklist = (tid, testCaseIds) => http.post(`/tasks/${tid}/checklist`, { test_case_ids: testCaseIds })
export const updateChecklistItem = (itemId, exec_status) => http.patch(`/checklist/${itemId}`, { exec_status })
export const checklistItemToIssue = (itemId, payload) => http.post(`/checklist/${itemId}/to-issue`, payload)
export const listAdoptableCases = (tid) => http.get(`/tasks/${tid}/adoptable-cases`)
export const extractUrl = (url) => http.post('/ai/extract-url', { url })
export const extractFile = (file) => {
  const fd = new FormData()
  fd.append('file', file)
  return http.post('/ai/extract-file', fd)
}

// SSE 流式生成：axios 不支持流式读取，改用原生 fetch 读 text/event-stream。
// token 直接取 localStorage（与 auth store 同源键 tp_token），避免与 store 循环依赖。
// 回调：onDelta(增量文本) / onDone(落库结果 {cases,meta,status}) / onError(msg)。
export async function streamTestcases(payload, { onDelta, onDone, onError, signal } = {}) {
  let resp
  try {
    resp = await fetch('/api/ai/testcases', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('tp_token') || ''}`,
      },
      body: JSON.stringify(payload),
      signal,
    })
  } catch (e) {
    onError?.(e.name === 'AbortError' ? '已取消' : '网络错误，无法连接生成服务')
    return
  }
  if (!resp.ok || !resp.body) {
    let msg = `生成请求失败（${resp.status}）`
    try { const j = await resp.json(); if (j?.msg) msg = j.msg } catch { /* 非 JSON 忽略 */ }
    onError?.(msg)
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const dataLine = frame.split('\n').find((l) => l.startsWith('data:'))
        if (!dataLine) continue
        const jsonStr = dataLine.slice(5).trim()
        if (!jsonStr) continue
        let evt
        try { evt = JSON.parse(jsonStr) } catch { continue }
        if (evt.type === 'delta') onDelta?.(evt.text)
        else if (evt.type === 'done') onDone?.(evt)
        else if (evt.type === 'error') onError?.(evt.msg)
      }
    }
  } catch (e) {
    onError?.(e.name === 'AbortError' ? '已取消' : '读取流失败')
  }
}
