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
// 跨批次查询测试点（用例库 / 日报已采纳用例共用）；params: project_id, task_id?, review_status?, category?, keyword?, limit?, offset?
// 返回 { items, total }（列表不含 script，需 script 走 getTestcase）
export const listCases = (params) => http.get('/ai/cases', { params })
// 取单条测试点完整信息(含 script)——列表已瘦身不含 script,详情/编辑按需取
export const getTestcase = (id) => http.get(`/ai/testcases/${id}`)
// 评审测试点：review_status ∈ 'adopted' | 'rejected' | 'pending'（返回已解包的测试点 data）
export const reviewTestcase = (id, review_status) => http.patch(`/ai/testcases/${id}`, { review_status })
// 设置测试点的自动化执行类型：exec_kind ∈ 'gui' | 'api' | 'cli'（下发到 runner 时决定怎么跑）
export const setCaseExecKind = (id, exec_kind) => http.patch(`/ai/testcases/${id}`, { exec_kind })
// 编辑测试点正文（title/steps/expected/category/priority,任意子集）
export const updateTestcase = (id, patch) => http.patch(`/ai/testcases/${id}`, patch)
// 删除测试点(级联清其清单项)
export const deleteTestcase = (id) => http.delete(`/ai/testcases/${id}`)
// 按用例当前 steps/expected 重新生成结构化 script(仅 gui/e2e)
export const genTestcaseScript = (id) => http.post(`/ai/testcases/${id}/gen-script`)

// ===== 验收清单（测试点回流任务）=====
export const getChecklistSummary = (project_id, date) => http.get('/tasks/checklist-summary', { params: { project_id, date } })
export const getTaskChecklist = (tid) => http.get(`/tasks/${tid}/checklist`)
export const attachChecklist = (tid, testCaseIds) => http.post(`/tasks/${tid}/checklist`, { test_case_ids: testCaseIds })
export const updateChecklistItem = (itemId, exec_status) => http.patch(`/checklist/${itemId}`, { exec_status })
export const checklistItemToIssue = (itemId, payload) => http.post(`/checklist/${itemId}/to-issue`, payload)
export const listAdoptableCases = (tid) => http.get(`/tasks/${tid}/adoptable-cases`)

// ===== 本地执行（勾选清单项下发目标机 → Claude Code 执行 → 回写）=====
// 前端只调 enqueue；拉取/认领/回写由 runner 用独立 token 完成。回写后 checklist_item.exec_status 自动更新。
export const enqueueExec = (project_id, runner, checklistItemIds) =>
  http.post('/exec-queue/enqueue', { project_id, runner, checklist_item_ids: checklistItemIds })
// 执行历史(独立"执行结果"页):按项目/任务/设备/verdict/status 筛,最新在前,不覆盖。
export const listExecHistory = (params) => http.get('/exec-queue/history', { params })

// 我的执行设备(成员登记自有 runner,拿专属 token)。token 仅注册/重置时返回明文。
export const listReleases = (params) => http.get('/releases', { params })
export const releaseStats = (project_id) => http.get('/releases/stats', { params: { project_id } })
export const getRelease = (id) => http.get(`/releases/${id}`)
export const createRelease = (data) => http.post('/releases', data)
export const updateRelease = (id, data) => http.patch(`/releases/${id}`, data)
export const deleteRelease = (id) => http.delete(`/releases/${id}`)
// ===== 选择器注册表（语义选择器单源）=====
// listSelectors 返回 { shared:[...], by_sub:{ 子产品: [...] } }；每个 key_out 含 candidates(数组)。
export const listSelectors = (project_id) => http.get('/selectors/manage', { params: { project_id } })
export const createSelector = (body) => http.post('/selectors', body)
export const patchSelector = (id, body) => http.patch(`/selectors/${id}`, body)
export const deleteSelector = (id) => http.delete(`/selectors/${id}`)
export const setSelectorScope = (body) => http.put('/selectors/scope', body)
// 导入内置纳米Work注册表（仅项目 admin）；返回 { imported, skipped }
export const importLegacySelectors = (project_id) => http.post('/selectors/import-legacy', null, { params: { project_id } })

// ===== 项目级 api 测试环境（base_url/鉴权/接口契约，供 api 用例生成与执行）=====
// readApiEnv 返回 { base_url, auth_type, auth, contract } 或 null（仅项目 admin，含被测系统凭据）。
export const readApiEnv = (project_id) => http.get('/api-env', { params: { project_id } })
// upsertApiEnv body: { project_id, base_url?, auth_type?, auth?(对象), contract?(字符串) }
export const upsertApiEnv = (body) => http.put('/api-env', body)
// parseCurl body: { curl } → { parsed, contract_line, script_seed }（鉴权头已剥离，不回真实 token）
export const parseCurl = (curl) => http.post('/api-env/parse-curl', { curl })
// importOpenapi body: { project_id, spec(对象或JSON串) } → { base_url, contract, count }（仅粘贴，不服务端拉 URL）
export const importOpenapi = (project_id, spec) => http.post('/api-env/import-openapi', { project_id, spec })

// 设备探测：网页发起一次探测（discover/verify）→ 轮询查状态/结果。
// startProbe body: { project_id, sub_product, runner, params:{contains?, mode?} } → { id }
// getProbe(id) → { id, status(pending/running/done/failed), params, result, error, ... }
//   discover result: { groups:[{frame,url,total,elements:[{tag,type,text,candidates,best}]}] }
//   verify   result: { verify:{ key:bool } }
export const startProbe = (body) => http.post('/probe', body)
export const getProbe = (id) => http.get(`/probe/${id}`)

export const listMyDevices = () => http.get('/devices')
export const registerDevice = (runner_id, name) => http.post('/devices', { runner_id, name })
export const resetDeviceToken = (id) => http.post(`/devices/${id}/reset-token`)
export const deleteDevice = (id) => http.delete(`/devices/${id}`)
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
