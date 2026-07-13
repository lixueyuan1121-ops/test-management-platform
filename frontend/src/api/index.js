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

// ===== P2: 工作量统计 + 遗留问题 =====
export const workloadStats = (project_id, from, to) => http.get('/stats/workload', { params: { project_id, from, to } })
export const listIssues = (project_id, status) => http.get('/issues', { params: { project_id, status } })
export const updateIssue = (id, data) => http.patch(`/issues/${id}`, data)
