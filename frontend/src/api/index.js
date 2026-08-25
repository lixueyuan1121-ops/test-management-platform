import http from './http'

export const login = (username, password) =>
  http.post('/auth/login', { username, password })

export const getMe = () => http.get('/auth/me')

export const listProjects = (includeInternal = false) =>
  http.get('/projects', { params: includeInternal ? { include_internal: true } : {} })
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
// AI 价值漏斗：生成→采纳→可自动化→执行→通过 + 真bug/选择器卡点/省时
export const aiFunnel = (days = 30) => http.get('/stats/ai-funnel', { params: { days } })
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
// 批量标记/取消回归(只改本项目用例;跨项目 id 后端忽略)。
export const bulkSetRegression = (project_id, ids, is_regression) =>
  http.patch('/ai/testcases/regression', { ids, is_regression }, { params: { project_id } })
// 按用例当前 steps/expected 重新生成结构化 script(仅 gui/e2e)
// 重生单条 script:同步调 AI 生成,较慢(常 30-60s),单独放宽超时到 60s(覆盖全局 15s 默认)。
// 重生 script：单条同步调 AI，前端放宽超时到 60s。
// opts.silent=true 时抑制 http 拦截器的错误 toast（批量重生逐条调用时用，改由汇总清单展示）。
export const genTestcaseScript = (id, opts = {}) =>
  http.post(`/ai/testcases/${id}/gen-script`, null, { timeout: 60000, silent: !!opts.silent })

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
// 回归执行:直接按用例 id 下发(不依赖任务/采纳,不挂清单项)。
export const enqueueCases = (project_id, runner, testCaseIds) =>
  http.post('/exec-queue/enqueue-cases', { project_id, runner, test_case_ids: testCaseIds })
// 对话测评:下发勾选的 query 到执行机(eval-queue,独立于 exec-queue 功能测试点执行)。
// payload: { project_id, runner, target_engine:'namiwork', eval_query_ids:[...] } → { run_ids, batch_id }
export const enqueueEvalQueries = (payload) => http.post('/eval-queue/enqueue', payload)
// 对话测评:某执行机上报的客户端设备(vm)列表,供下发时选目标设备。
export const listEvalDevices = (runner) => http.get('/eval-devices', { params: { runner } })
// 对话测评用例库:某项目历史生成的 eval_query 列表(再次触发验证用)。
export const listEvalQueries = (projectId) => http.get('/ai/eval-queries', { params: { project_id: projectId } })

// ===== 对话测评判定（读 trace + 引擎判三维：思考/工具/产物）=====
// 单条触发判定；provider 可空（后端按默认引擎）。返回已解包的 _run_out（含 verdict/verdict_dims/is_abnormal）。
// 判定同步调 AI 引擎，较慢（常 30-60s），单独放宽超时到 90s（覆盖全局 15s 默认）。
export const judgeEvalRun = (runId, provider) => http.post(`/eval-judge/${runId}`, { provider }, { timeout: 90000 })
// 批量判定；payload: { project_id, run_ids?（空则判该项目所有 done）, provider? } → { judged, results:[...] }
// 批量逐条同步判定，可能判很多条，放宽到 5 分钟。
export const judgeEvalBatch = (payload) => http.post('/eval-judge/batch', payload, { timeout: 300000 })
// 异常会话（is_abnormal=判定 fail）列表，供复核/推送
export const listAbnormalEvalRuns = (projectId) => http.get('/eval-judge/abnormal', { params: { project_id: projectId } })
// eval 执行历史（子项2 端点 /eval-queue/history；此前未在 api 封装，新增）；返回 _to_out 列表（含 payload/status/verdict）
export const listEvalRuns = (projectId) => http.get('/eval-queue/history', { params: { project_id: projectId } })

// ===== 对话测评导出/推送 =====
// 导出到飞书表；payload: { project_id, sheet_url, abnormal_only?, batch_id?, start_row? } → { exported, sheet_url }
// 批量导出为顺序网络循环(N 行 × 飞书 PUT)，放宽超时到 300s（覆盖全局 15s 默认，参照 judge 端点）。
export const exportEvalFeishu = (payload) => http.post('/eval-export/feishu', payload, { timeout: 300000 })
// 推送异常会话到 multica；payload: { project_id, batch_id? } → { pushed, candidates, results }
// 逐条推送(multica CLI/HTTP，每条可达 60s)，放宽超时到 300s（覆盖全局 15s 默认，参照 judge 端点）。
export const pushEvalMultica = (payload) => http.post('/eval-export/multica', payload, { timeout: 300000 })
// 待推 multica 的异常数（is_abnormal 且未 pushed）→ { pending }
export const evalMulticaPending = (projectId) => http.get('/eval-export/multica-pending', { params: { project_id: projectId } })

// ===== 导出 Playwright 脚本（回归用例库 → 开发本地自测）=====
// 下载类接口用 responseType:'blob' + returnResponse:true —— 拿到完整响应（含头），
// 绕开 http.js 对 {code,msg,data} 的解包；错误 body 也成了 Blob，需手动读回 JSON 取 msg。

// 触发浏览器保存一个 Blob；文件名优先取响应头 Content-Disposition。
function _saveBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.URL.revokeObjectURL(url)
}

function _filenameFromResp(resp, fallback) {
  const cd = resp.headers?.['content-disposition'] || ''
  // 优先 RFC 5987 的 filename*（含 UTF-8 中文），回落普通 filename。
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  if (star) { try { return decodeURIComponent(star[1]) } catch { /* 落到下面 */ } }
  const m = /filename="?([^";]+)"?/.exec(cd)
  return m ? m[1] : fallback
}

// blob 请求出错时后端返回的是 JSON 信封（被当 Blob 收），读回文本解析 msg。
async function _blobErrorMsg(error) {
  const data = error.response?.data
  if (data instanceof Blob) {
    try { const j = JSON.parse(await data.text()); return j.msg || '导出失败' } catch { return '导出失败' }
  }
  return error.message || '导出失败'
}

// 单条导出：下载一个 .spec.mjs。成功返回 true。
export async function exportPlaywrightOne(cid) {
  const resp = await http.get(`/ai/testcases/${cid}/export-playwright`, {
    responseType: 'blob', returnResponse: true,
  })
  _saveBlob(resp.data, _filenameFromResp(resp, `case-${cid}.spec.mjs`))
  return true
}

// 批量导出：下载 zip。返回被跳过的用例 id 数组（后端经 X-Export-Skipped 头告知）。
export async function exportPlaywrightBulk(ids) {
  const resp = await http.post('/ai/testcases/export-playwright', { ids }, {
    responseType: 'blob', returnResponse: true,
  })
  _saveBlob(resp.data, _filenameFromResp(resp, 'playwright-cases.zip'))
  const skipped = resp.headers?.['x-export-skipped']
  return skipped ? skipped.split(',').filter(Boolean) : []
}

export { _blobErrorMsg }
// 执行历史(独立"执行结果"页):按项目/任务/设备/verdict/status 筛,最新在前,不覆盖。
export const listExecHistory = (params) => http.get('/exec-queue/history', { params })
// 人工纠偏执行结果:verdict 三态 pass/fail/blocked + 可选备注;后端打「[人工纠偏]」前缀并同步清单。
export const correctExecVerdict = (runId, verdict, reason) =>
  http.patch(`/exec-queue/${runId}/verdict`, { verdict, reason })

// 我的执行设备(成员登记自有 runner,拿专属 token)。token 仅注册/重置时返回明文。
export const listReleases = (params) => http.get('/releases', { params })
export const releaseStats = (project_id) => http.get('/releases/stats', { params: { project_id } })
// 版本质量档案：每版一张记分卡(通过率/真bug/遗留问题/红黄绿)
export const releaseQuality = (project_id, limit = 6) =>
  http.get('/releases/quality', { params: { project_id, limit } })
export const getRelease = (id) => http.get(`/releases/${id}`)
export const createRelease = (data) => http.post('/releases', data)
export const updateRelease = (id, data) => http.patch(`/releases/${id}`, data)
export const deleteRelease = (id) => http.delete(`/releases/${id}`)
// 对话测评维度通过率(能力画像雷达)
export const evalDimensionStats = (project_id, days = 30) =>
  http.get('/eval-judge/dimension-stats', { params: { project_id, days } })
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
// getApiContract：成员可读的接口清单（不含鉴权凭据），供生成页展示辅助圈定。→ { base_url, contract, has_contract }
export const getApiContract = (project_id) => http.get('/api-env/contract', { params: { project_id } })

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
// 设备看板只读聚合(平台管理员)：全平台设备 + 在线状态 + 各状态执行计数 + 执行中明细
export const getDeviceOverview = () => http.get('/devices/overview')

// ===== 性能测试（nami-perfdog 采集结果的下发 / 回传 / 在线报告）=====
export const dispatchPerfJob = (data) => http.post('/perf/jobs', data)
export const listPerfRuns = (params) => http.get('/perf/runs', { params })
export const getPerfRun = (id) => http.get(`/perf/runs/${id}`)
export const perfReport = (params) => http.get('/perf/report', { params })
export const deletePerfRun = (id) => http.delete(`/perf/runs/${id}`)
// 报告集：把多次采集归入一个可命名、独立展示的报告
export const listPerfSets = () => http.get('/perf/report-sets')
export const createPerfSet = (name) => http.post('/perf/report-sets', { name })
export const renamePerfSet = (id, name) => http.patch(`/perf/report-sets/${id}`, { name })
export const deletePerfSet = (id) => http.delete(`/perf/report-sets/${id}`)
// 交互采集控制：轮询提示/状态、点继续推进、取消采集
export const getPerfPrompt = (id) => http.get(`/perf/runs/${id}/prompt`)
export const signalPerfRun = (id) => http.post(`/perf/runs/${id}/signal`)
export const cancelPerfRun = (id) => http.post(`/perf/runs/${id}/cancel`)
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

// SSE 流式生成对话测评 query：与 streamTestcases 同构（有意复制，两条 SSE 消费链路隔离，
// 与后端 ai_eval.py 独立于 api/ai.py 一致），仅 URL 不同（/api/ai/eval-queries）。
// done 帧字段为 queries（而非 testcases 的 cases）；onDone 收到整包 evt，由调用方读 evt.queries。
export async function streamEvalQueries(payload, { onDelta, onDone, onError, signal } = {}) {
  let resp
  try {
    resp = await fetch('/api/ai/eval-queries', {
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

// ===== 反馈测试模块 =====
// 导入记录（机器人 ingest 批次；手动上传兜底见页面 fetch multipart）
export const feedbackImports = () => http.get('/feedback/imports')
// 重新触发某批次补 script（续补中断的）
export const refillScripts = (iid) => http.post(`/feedback/imports/${iid}/refill`)
// 反馈用例
export const feedbackCases = (params) => http.get('/feedback/cases', { params })
export const feedbackCase = (cid) => http.get(`/feedback/cases/${cid}`)
export const updateFeedbackCase = (cid, data) => http.patch(`/feedback/cases/${cid}`, data)
// 重补 script 同步调 claude CLI，耗时数十秒~数分钟，单独放大超时（全局默认 15s 不够）
export const regenFeedbackScript = (cid) => http.post(`/feedback/cases/${cid}/gen-script`, null, { timeout: 300000 })
export const deleteFeedbackCase = (cid) => http.delete(`/feedback/cases/${cid}`)
export const runFeedbackCases = (case_ids, runner) => http.post('/feedback/cases/run', { case_ids, runner })
// 回归用例集
export const feedbackSets = () => http.get('/feedback/sets')
export const createFeedbackSet = (data) => http.post('/feedback/sets', data)
export const updateFeedbackSet = (sid, data) => http.patch(`/feedback/sets/${sid}`, data)
export const deleteFeedbackSet = (sid) => http.delete(`/feedback/sets/${sid}`)
export const feedbackSetCases = (sid) => http.get(`/feedback/sets/${sid}/cases`)
export const addFeedbackSetCases = (sid, case_ids) => http.post(`/feedback/sets/${sid}/cases`, { case_ids })
export const removeFeedbackSetCases = (sid, case_ids) => http.delete(`/feedback/sets/${sid}/cases`, { data: { case_ids } })
export const runFeedbackSet = (sid) => http.post(`/feedback/sets/${sid}/run`)
export const setFeedbackSchedule = (sid, cron, enabled) => http.patch(`/feedback/sets/${sid}/schedule`, { cron, enabled })
// 回归结果
export const feedbackRuns = (set_id) => http.get('/feedback/runs', { params: { set_id } })
export const feedbackRunDetail = (rid) => http.get(`/feedback/runs/${rid}`)
