import * as fs from 'node:fs';
import * as path from 'node:path';
import {
  loadPlatformConfig,
  reportEntries,
  syncEntriesStatus,
  login,
  resolveProjectId,
  apiFetch,
  reconcileEntriesWithPlatform,
} from './platform-report';

/**
 * 统一平台同步工具：在 Mac 本地（能访问内网平台）执行。
 *
 * 逻辑：
 *  1. 对 board.json 中 reportedToPlatform != true 的 entry 先补报（创建平台任务），成功后标记；
 *  2. 对已上报的 entry 做状态同步（PATCH /api/tasks/{id} {status}），幂等。
 *
 * 并发防护：用 runs/.sync.lock 目录做进程互斥锁（mkdir 原子性），
 * 防止提测 cron 第 4.3 步的即时上报与平台同步 cron 同时执行导致重复创建平台任务。
 *
 * 用法：
 *   npx tsx lib/sync-platform.ts              # 全量同步
 *   npx tsx lib/sync-platform.ts 2026-08-07   # 只同步指定日期
 */

const LOCK_DIR = path.join('runs', '.sync.lock');

function acquireLock(): boolean {
  try {
    fs.mkdirSync(LOCK_DIR, { recursive: false });
    return true;
  } catch {
    return false; // 已有其他同步进程持有锁
  }
}

function releaseLock(): void {
  try {
    fs.rmdirSync(LOCK_DIR);
  } catch {
    /* 忽略释放失败 */
  }
}

async function main() {
  if (!acquireLock()) {
    console.log('SKIP: 已有另一个同步进程在运行（runs/.sync.lock 存在），本轮跳过避免并发重复上报');
    return;
  }
  try {
    await doSync();
  } finally {
    releaseLock();
  }
}

async function doSync() {
  const args = process.argv.slice(2);
  const dateFilter = args.find((a) => !a.startsWith('--'));

  const board = JSON.parse(fs.readFileSync('runs/board.json', 'utf8'));
  let entries = (board.entries ?? []) as any[];
  if (dateFilter) {
    entries = entries.filter((e: any) => e.assignedAt === dateFilter);
  }

  const cfg = loadPlatformConfig(process.cwd());
  if (!cfg) {
    console.error('platform cfg 缺失，无法同步');
    process.exit(1);
  }
  if (entries.length === 0) {
    console.log('无待同步条目');
    return;
  }

  // 过滤掉已在平台被手动删除的条目（避免每次同步都报 task-not-found）
  entries = entries.filter((e: any) => !e.platformDeleted);

  const unreported = entries.filter((e: any) => !e.reportedToPlatform);
  const reported = entries.filter((e: any) => e.reportedToPlatform);
  console.log(
    `待处理: 未上报 ${unreported.length} 条, 状态同步 ${reported.length} 条`,
  );

  // 1) 补报未上报的 entry（创建平台任务）
  if (unreported.length > 0) {
    const r = await reportEntries(cfg, unreported);
    console.log('补报结果:', JSON.stringify(r));
    // reportEntries 只标记平台已明确确认的条目，不能用“未出现在错误列表”推断成功。
    // 新建任务保持未分配；已存在的任务先反向校准，保留管理员的状态。
    reported.push(...unreported.filter((entry: any) => entry.reportedToPlatform));
  }

  // 2) 反向校准：平台状态领先 board 时（人工在平台改了状态/关闭了任务），以平台为准回写 board
  let closedTicketIds: string[] = [];
  if (reported.length > 0) {
    try {
      const token = await login(cfg);
      const projectId = await resolveProjectId(cfg, token);
      const tasks = await apiFetch(cfg, 'GET', `/api/tasks?project_id=${projectId}`, undefined, token);
      const { reversed, closedTicketIds: closed } = reconcileEntriesWithPlatform(reported, tasks);
      closedTicketIds = closed;
      if (reversed.length > 0) {
        console.log('反向校准（平台领先，board 已回写）:', reversed.join('; '));
      }
    } catch (e) {
      console.error('[platform] 反向校准失败（不影响正向同步）:', (e as Error).message);
    }
  }

  // 3) 状态同步已上报的 entry（平台已关闭的任务不做正向覆盖，尊重人工关闭）
  const toSync = reported.filter((e: any) => !closedTicketIds.includes(e.ticketId));
  if (toSync.length > 0) {
    const rs = await syncEntriesStatus(cfg, toSync);
    console.log('状态同步:', JSON.stringify(rs));
    // 自动标记 task-not-found 的条目为 platformDeleted（下次同步跳过）
    if (rs.errors.length > 0) {
      for (const errStr of rs.errors) {
        if (errStr.includes('task-not-found')) {
          const tid = errStr.split(':')[0].trim();
          const entry = (board.entries as any[]).find((e: any) => e.ticketId === tid);
          if (entry && !entry.platformDeleted) {
            entry.platformDeleted = true;
            console.log(`[platform] 自动标记 ${tid} 为 platformDeleted（平台已手动删除）`);
          }
        }
      }
    }
  }
  if (closedTicketIds.length > 0) {
    console.log(`平台已关闭任务 ${closedTicketIds.length} 条，跳过正向同步（尊重关闭）`);
  }

  fs.writeFileSync('runs/board.json', JSON.stringify(board, null, 2) + '\n');
  console.log('board.json 已更新并回写');
}

main().catch((e) => {
  console.error('同步失败:', e.message);
  process.exit(1);
});
