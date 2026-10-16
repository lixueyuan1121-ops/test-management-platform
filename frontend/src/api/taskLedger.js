import http from './http'

export const generateTaskDigest = data => http.post('/task-ledger/digests/generate', data)
export const saveTaskDigest = data => http.post('/task-ledger/digests', data)
export const listTaskDigests = project_id => http.get('/task-ledger/digests', { params: { project_id } })
export const fillTaskRelease = data => http.post('/task-ledger/releases', data)

export const publishTaskDigest = data => http.post('/task-ledger/digests/publish', data, { timeout: 30000 })
export const getSharedTaskDigest = code => http.get(`/task-ledger/digests/shared/${encodeURIComponent(code)}`, { silent: true })
