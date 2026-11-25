// Locate a desktop client's process tree by executable path. Keep other installations running.
import { execFile } from 'node:child_process';
import { readlinkSync, realpathSync } from 'node:fs';
import { promisify } from 'node:util';
import { setTimeout as sleep } from 'node:timers/promises';

const execFileAsync = promisify(execFile);

export function clientProcessIds(rows, executable, previous = new Set()) {
  const bundle = executable.match(/^(.+\.app\/Contents\/)MacOS\/[^/]+$/)?.[1];
  const ids = new Set();
  for (const row of rows) {
    if (row.command === executable || (bundle && row.command.startsWith(bundle)) || previous.has(row.pid)) ids.add(row.pid);
  }
  // Include children of the previous generation even if the parent just exited.
  let size;
  do {
    size = ids.size;
    for (const row of rows) if (ids.has(row.parent) || previous.has(row.parent)) ids.add(row.pid);
  } while (ids.size !== size);
  return ids;
}

async function processes() {
  const { stdout } = await execFileAsync('ps', ['-axww', '-o', 'pid=,ppid=,comm='], { maxBuffer: 8 * 1024 * 1024 });
  const rows = [];
  for (const line of stdout.split('\n')) {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(.+)$/);
    if (!match) continue;
    const pid = Number(match[1]);
    let command = match[3];
    if (process.platform === 'linux') {
      try { command = readlinkSync(`/proc/${pid}/exe`); } catch { /* inaccessible or already exited */ }
    }
    rows.push({ pid, parent: Number(match[2]), command });
  }
  return rows;
}

export async function stopPosixClient(executable) {
  const target = realpathSync(executable);
  let ids = new Set();
  for (let attempt = 0; attempt < 24; attempt++) {
    ids = clientProcessIds(await processes(), target, ids);
    if (!ids.size) return;
    for (const pid of ids) {
      try { process.kill(pid, attempt < 12 ? 'SIGTERM' : 'SIGKILL'); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    await sleep(250);
  }
  if (clientProcessIds(await processes(), target, ids).size) throw new Error('目标客户端进程未完全退出');
}
