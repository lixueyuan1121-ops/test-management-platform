import { mkdirSync, writeFileSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

// Retry only a confirmed transaction rollback, not arbitrary HTTP/network errors
// whose commit outcome is unknown. Never re-execute the application test.
export async function publishResult({ runId, result, send, directory, log = () => {}, wait = delay }) {
  if (!Number.isSafeInteger(runId) || runId < 1) throw new Error('Invalid run id');
  mkdirSync(directory, { recursive: true });
  const path = join(directory, `run-${runId}.json`);
  const payload = structuredClone(result);
  writeFileSync(path, JSON.stringify({ run_id: runId, result: payload }, null, 2), { mode: 0o600 });
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await send(payload);
      try { unlinkSync(path); } catch (e) { log(`结果已回写，本地副本清理失败: ${path}: ${e.message}`); }
      return response;
    } catch (error) {
      const deadlock = /HTTP 500\b/.test(error.message) && /(?:\b1213\b|Deadlock found when trying to get lock)/i.test(error.message);
      if (!deadlock || attempt === 2) {
        error.savedReport = path;
        throw error;
      }
      log(`run_id=${runId} 回写遇数据库死锁，重试原报告 ${attempt + 1}/2`);
      await wait(250 * (attempt + 1));
    }
  }
}
