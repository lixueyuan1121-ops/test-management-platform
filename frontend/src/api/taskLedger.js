import http from './http'
import { pollAiJob } from './index'

export async function generateTaskDigest(data, { onTick, signal } = {}) {
  const queued = await http.post('/task-ledger/digests/generate', data, { signal })
  if (!queued.job_id) return queued
  const result = await pollAiJob(queued.job_id, { onTick, signal })
  return http.get(`/task-ledger/digests/generations/${result.generation_id}`, { signal })
}
export const saveTaskDigest = data => http.post('/task-ledger/digests', data)
export const listTaskDigests = project_id => http.get('/task-ledger/digests', { params: { project_id } })
export const fillTaskRelease = data => http.post('/task-ledger/releases', data)

// Expired Nami authentication can require one browser login and a verified retry.
export const publishTaskDigest = data => http.post('/task-ledger/digests/publish', data, { timeout: 210000 })
export const getSharedTaskDigest = code => http.get(`/task-ledger/digests/shared/${encodeURIComponent(code)}`, { silent: true })
