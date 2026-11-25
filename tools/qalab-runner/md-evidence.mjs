import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const MiB = 1024 * 1024;
const maxFile = 3 * MiB;
const logBytes = 512 * 1024;

function dailyLog(date) {
  const y = String(date.getFullYear());
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return path.join(os.homedir(), '.namiwork', 'logs', y, m, d, [y, m, d].join('-') + '.log');
}

export function startClientLog(configuredFile, date = new Date()) {
  const file = configuredFile || dailyLog(date);
  let stat;
  try { stat = fs.statSync(file); } catch { /* The client may create its log on startup. */ }
  return { file, configuredFile, stat };
}

function newLogBytes(file, before) {
  const fd = fs.openSync(file, 'r');
  try {
    const st = fs.fstatSync(fd);
    if (!st.isFile()) return Buffer.alloc(0);
    const same = before && st.dev === before.dev && st.ino === before.ino && st.size >= before.size;
    const offset = Math.max(same ? before.size : 0, st.size - logBytes);
    const buffer = Buffer.alloc(st.size - offset);
    return buffer.subarray(0, fs.readSync(fd, buffer, 0, buffer.length, offset));
  } finally { fs.closeSync(fd); }
}

function redactLog(bytes) {
  const text = bytes.toString('utf8');
  if (text.includes('\0')) throw new Error('客户端日志不是 UTF-8 文本');
  return Buffer.from(text.replace(/((?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+/gi, '$1[已隐藏]')
    .replace(/(\bBearer\s+)[\w.+/=~-]+/gi, '$1[已隐藏]')
    .replace(/(["']?(?:access_token|refresh_token|api_key|password|userToken)["']?\s*[:=]\s*["']?)[^"'&,;\s}]+/gi, '$1[已隐藏]'));
}

function readEvidence(dir, name, limit) {
  if (typeof name !== 'string' || !/^[\p{L}\p{N}_ .（）()-]{1,140}$/u.test(name) || name.startsWith('.'))
    throw new Error('附件需要使用 evidence 目录内的普通文件名');
  const file = path.join(dir, name);
  if (fs.realpathSync(dir) !== fs.realpathSync(path.dirname(dir)) + path.sep + path.basename(dir)
      || fs.lstatSync(file).isSymbolicLink()) throw new Error('不接受链接附件');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.size <= 0 || stat.size > limit) throw new Error('附件为空、超限或不是普通文件');
    const data = Buffer.alloc(stat.size);
    let offset = 0;
    while (offset < data.length) {
      const n = fs.readSync(fd, data, offset, data.length - offset, offset);
      if (!n) throw new Error('附件在读取时发生变化');
      offset += n;
    }
    return data;
  } finally { fs.closeSync(fd); }
}

export async function collectMdEvidence({ workDir, logStart, captureScreenshot, clientStarted }) {
  const files = [], notes = [];
  let bytes = 0;
  const declared = new Set();
  const dir = path.join(workDir, 'evidence');
  function add(name, label, data) {
    if (!data?.length || data.length > maxFile) throw new Error('附件为空或超过 3 MiB');
    const base64 = data.toString('base64');
    if (files.some(f => f.name === name && f.label === label && f.base64 === base64)) return;
    if (files.length >= 32 || bytes + data.length > 32 * MiB) throw new Error('任务附件超过 32 份或 32 MiB');
    if (/\.png$/i.test(name) && !data.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) throw new Error('无效 PNG');
    if (/\.jpe?g$/i.test(name) && !data.subarray(0, 3).equals(Buffer.from('ffd8ff', 'hex'))) throw new Error('无效 JPEG');
    bytes += data.length;
    files.push({ name, label, base64 });
  }
  if (fs.existsSync(path.join(dir, 'manifest.json'))) {
    try {
      const manifest = JSON.parse(readEvidence(dir, 'manifest.json', 64 * 1024).toString());
      if (!Array.isArray(manifest)) throw new Error('附件清单须为数组');
      for (const row of manifest.slice(0, 32)) {
        try {
          declared.add(row?.file);
          if (typeof row?.case !== 'string' || !row.case.trim() || row.case.length > 200
              || /[\x00-\x1f]/.test(row.case) || !/\.(png|jpe?g|log|txt)$/i.test(row.file))
            throw new Error('用例名或文件类型无效');
          const data = readEvidence(dir, row.file, maxFile);
          add(row.file, row.case.trim(), /\.(log|txt)$/i.test(row.file) ? redactLog(data) : data);
        } catch (e) { notes.push('用例附件采集失败：' + e.message.slice(0, 180)); }
      }
      if (manifest.length > 32) notes.push('用例附件超过 32 份上限');
    } catch (e) { notes.push('读取附件清单失败：' + e.message.slice(0, 160)); }
  }
  // Some clients save evidence even if the agent exits before finishing the manifest.
  // Recover only ordinary image/log files explicitly placed in this run's evidence directory.
  try {
    const entries = fs.existsSync(dir) && fs.lstatSync(dir).isDirectory() ? fs.readdirSync(dir, { withFileTypes: true }) : [];
    const loose = entries.filter(entry => entry.isFile()
      && !declared.has(entry.name) && /\.(png|jpe?g|log|txt)$/i.test(entry.name));
    for (const { name } of loose.slice(0, 32)) {
      try {
        const label = /^(?:[a-z]+[-_]\d+(?:[-_]\d+)*|(?:tc|case|c)\d+)(?=[. _-]|$)/i.exec(name)?.[0] || '';
        const data = readEvidence(dir, name, maxFile);
        add(name, label, /\.(log|txt)$/i.test(name) ? redactLog(data) : data);
      } catch (error) { notes.push('回收任务附件失败：' + error.message.slice(0, 180)); }
    }
  } catch { notes.push('无法列出执行附件目录'); }
  if (clientStarted && !files.some(f => /\.jpe?g$|\.png$/i.test(f.name))) {
    try { add('任务结束时客户端.png', '', await captureScreenshot?.()); }
    catch { notes.push('未取得客户端窗口截图'); }
  }
  if (logStart) {
    const endFile = logStart.configuredFile || dailyLog(new Date());
    const chunks = [];
    for (const file of new Set([logStart.file, endFile])) {
      try { chunks.push(newLogBytes(file, file === logStart.file ? logStart.stat : null)); }
      catch (e) { if (e.code !== 'ENOENT') notes.push('读取客户端日志失败：' + e.code); }
    }
    try {
      const data = Buffer.concat(chunks);
      if (data.length) add('任务时段客户端.log', '', redactLog(data));
      else if (!files.some(f => /\.(log|txt)$/i.test(f.name))) notes.push('任务时段未取得新增客户端日志');
    } catch (e) { notes.push('客户端日志采集失败：' + e.message); }
  }
  return { files, notes: notes.slice(0, 35) };
}

// Store the immutable payload outside the agent working directory. File and result endpoints
// are independently idempotent, so a lost response can be retried without rerunning the task.
export function queueMdReport(directory, runId, report, files) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const file = path.join(directory, `md-${runId}.json`);
  writePending(file, { runId, report, files });
  return file;
}

function writePending(file, packet) {
  fs.writeFileSync(file + '.tmp', JSON.stringify(packet), { mode: 0o600 });
  fs.renameSync(file + '.tmp', file);
}

export async function sendMdReport(file, api, runner, log) {
  const packet = JSON.parse(fs.readFileSync(file, 'utf8'));
  const route = '/api/md-runs/queue/' + packet.runId;
  const query = '?runner=' + encodeURIComponent(runner);
  const pending = [];
  for (const item of packet.files) {
    const form = new FormData();
    form.append('file', new Blob([Buffer.from(item.base64, 'base64')]), item.name);
    form.append('case_label', item.label);
    try {
      const receipt = await api('POST', route + '/evidence' + query, form);
      if (!Number.isInteger(receipt?.id)) throw new Error('平台未确认保存附件');
    } catch (e) {
      if (/HTTP (400|404|409|413|422)\b/.test(e.message)) {
        packet.report.evidence_summary.notes.push((item.name + ' 回传失败：' + e.message).slice(0, 260));
        packet.report.evidence_summary.notes = packet.report.evidence_summary.notes.slice(-35);
      } else {
        pending.push(item);
        log(`run_id=${packet.runId} ${item.name} 等待重传：${e.message}`);
      }
    }
  }
  packet.files = pending;
  writePending(file, packet);
  let saved;
  try { saved = await api('PATCH', route + query, packet.report); }
  catch (error) {
    if (/HTTP (404|410)\b/.test(error.message)) fs.unlinkSync(file);
    throw error;
  }
  if (saved?.id !== packet.runId && saved?.run_id !== packet.runId) throw new Error('平台未确认保存提测结果');
  if (!pending.length || saved?.status === 'canceled') fs.unlinkSync(file);
  return saved;
}
