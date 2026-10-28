import * as fs from 'node:fs';
import { loadPlatformConfig, reportEntries } from './platform-report';

async function main() {
  const board = JSON.parse(fs.readFileSync('runs/board.json', 'utf8'));
  const today = '2026-08-07';
  const pending = board.entries.filter(
    (e: any) => e.assignedAt === today && !e.reportedToPlatform,
  );
  console.log('今天未上报的 entries:', pending.length);
  for (const e of pending) {
    console.log(' -', e.ticketId, '|', e.module, '|', e.assignee, '|', e.priority);
  }

  const cfg = loadPlatformConfig(process.cwd());
  console.log('platform cfg:', cfg ? '已加载' : '缺失');
  if (!cfg || pending.length === 0) return;

  const r = await reportEntries(cfg, pending);
  console.log('上报结果:', JSON.stringify(r, null, 2));

  // 仅 reportEntries 确认的条目会标记成功，包括平台已存在的情况。
  if (r.confirmed.length > 0) {
    fs.writeFileSync('runs/board.json', JSON.stringify(board, null, 2) + '\n');
    console.log('已写回 board.json，保留逐条关联和上报结果');
  }
}

main().catch((e) => {
  console.error('补报失败:', e.message);
  process.exit(1);
});
