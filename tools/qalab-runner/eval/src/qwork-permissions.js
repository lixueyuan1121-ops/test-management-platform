'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { idOf } = require('./qwork-trace');

// 只识别已实测的文档技能“清理临时解包目录 -> 解包 -> 查看”命令。
// 不把 bash、删除保护或 confirmation_once 当作无条件授权。
function words(command) {
  if (typeof command !== 'string' || /[`\n\r\\]/.test(command)) return null;
  const out = []; let pos = 0;
  const re = /\s+|2>&1|2>\/dev\/null|&&|[;|]|(?:[^\s"';&|<>]+|"[^"\n]*"|'[^'\n]*')+/y;
  while (pos < command.length) {
    re.lastIndex = pos;
    const match = re.exec(command);
    if (!match) return null;
    pos = re.lastIndex;
    if (!match[0].trim()) continue;
    const token = match[0].replace(/"([^"\n]*)"|'([^'\n]*)'/g, (_, a, b) => a ?? b);
    if (/[$*?(){}~]/.test(token) && !/^\$(DOCX_RW|XLSX_RW)$/.test(token)) return null;
    out.push(token);
  }
  return out;
}
const within = (root, target) => target !== root && !path.relative(root, target).startsWith('..') && !path.isAbsolute(path.relative(root, target));

async function documentCleanupPermission(request, { sessionId, cwd, exePath } = {}) {
  if (typeof request.request_id !== 'string' || !request.request_id) return false;
  if (idOf(request.session_id) !== sessionId || !cwd || path.resolve(request.cwd || '') !== path.resolve(cwd)) return false;
  if (request.tool_name !== 'bash' || request.confirmation_once !== true) return false;
  const tokens = words(request.input?.command);
  if (!tokens) return false;
  const commands = []; let current = [];
  for (const token of tokens) {
    if (['&&', ';', '|'].includes(token)) { if (!current.length) return false; commands.push(current); current = []; }
    else if (!['2>&1', '2>/dev/null'].includes(token)) current.push(token);
  }
  if (!current.length) return false;
  commands.push(current);
  if (commands[0].length !== 2 || commands[0][0] !== 'cd' || path.resolve(commands[0][1]) !== path.resolve(cwd)) return false;
  // 技能路径必须属于正在执行的 QWork 安装目录；Mac 自动发现，其他安装路径显式传入。
  const app = exePath || (process.platform === 'darwin' ? '/Applications/QWork.app' : '');
  const macApp = app.match(/^(.+\.app)(?:\/Contents\/MacOS\/[^/]+)?$/)?.[1];
  const appRoot = macApp ? path.join(macApp, 'Contents', 'Resources') : path.join(path.dirname(app), 'resources');
  if (!app) return false;
  const isSkill = value => within(path.join(appRoot, 'skills'), path.resolve(value)) && /\/(docx|xlsx)-rs\/scripts\/[^/]+\/\1-rw$/.test(value);
  const vars = {};
  let folder, inputFile, unpacked = false;
  for (const args of commands.slice(1)) {
    const assignment = args.length === 1 && args[0].match(/^(DOCX_RW|XLSX_RW)=(.+)$/);
    if (assignment && !folder && isSkill(assignment[2])) { vars[assignment[1]] = assignment[2]; continue; }
    if (args[0] === 'rm') {
      if (folder || args.length !== 3 || args[1] !== '-rf' || !/^_?unpacked$/.test(args[2])) return false;
      folder = args[2]; continue;
    }
    const executable = args[0].startsWith('$') ? vars[args[0].slice(1)] : args[0];
    if (executable && isSkill(executable)) {
      if (!folder || unpacked || args.length !== 4 || args[1] !== 'unpack' || args[3] !== folder || !/\.(docx|xlsx)$/i.test(args[2])) return false;
      inputFile = path.resolve(cwd, args[2]); unpacked = true; continue;
    }
    if (!unpacked) return false;
    const local = value => value === folder || value.startsWith(`${folder}/`) && !value.split('/').includes('..');
    const readOnly = args[0] === 'head' && ((args.length === 2 && /^-\d+$/.test(args[1])) ||
      (args.length === 4 && args[1] === '-c' && /^\d+$/.test(args[2]) && local(args[3]))) ||
      args[0] === 'ls' && args.length === 2 && local(args[1]) ||
      args[0] === 'find' && args.length === 4 && local(args[1]) && args[2] === '-type' && args[3] === 'f' ||
      args[0] === 'sort' && args.length === 1 ||
      args[0] === 'echo' && args.length === 2 && /^[-A-Z]+$/.test(args[1]);
    if (!readOnly) return false;
  }
  if (!unpacked) return false;
  try {
    const root = await fs.realpath(cwd), target = path.join(cwd, folder);
    const input = await fs.realpath(inputFile);
    const attachments = await fs.realpath(path.join(os.homedir(), '.qwork/qwork/files/prepared-task-attachments')).catch(() => null);
    if (!within(root, input) && !(attachments && within(attachments, input))) return false;
    const existing = await fs.realpath(target).catch(error => { if (error.code === 'ENOENT') return path.join(root, folder); throw error; });
    return within(root, existing);
  } catch { return false; }
}

async function allowQworkPermission(page, request) {
  const sessionId = idOf(request.session_id);
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) throw new Error('权限会话 ID 无效');
  const row = page.locator(`[data-session-id="${sessionId}"]`);
  const expand = page.getByRole('button', { name: '展开侧栏', exact: true });
  if (await expand.isVisible()) await expand.click({ timeout: 5000 });
  if (!await row.evaluate(el => el.classList.contains('bg-[#e6e6e6]')).catch(() => false))
    await row.getByRole('button').first().click({ timeout: 10000 });
  const dialog = page.getByRole('region', { name: '权限询问', exact: true });
  await dialog.waitFor({ timeout: 10000 });
  const displayed = JSON.parse(await dialog.locator('pre').innerText());
  if (displayed.command !== request.input.command || !await row.evaluate(el => el.classList.contains('bg-[#e6e6e6]')))
    throw new Error('显示的权限请求与当前会话待确认操作不一致');
  // 必须经过 UI 的 onResolved，直接调 respondPermission 会留下过期模态框挡住后续分享。
  // 此确认框未勾选记住设置，confirmation_once 在客户端也强制 scope=once。
  await dialog.getByRole('button', { name: '允许', exact: true }).click({ timeout: 5000 });
  await page.waitForFunction(async ({ sessionId, requestId }) => {
    const pending = await window.workGui.sessions.pendingPermissions(sessionId);
    return !pending.some(item => item.request_id === requestId);
  }, { sessionId, requestId: request.request_id }, { timeout: 15000 });
  await page.waitForFunction(async ({ sessionId, requestId, command }) => {
    const pre = document.querySelector('[aria-label="权限询问"] pre');
    if (!pre) return true;
    let displayed;
    try { displayed = JSON.parse(pre.textContent); } catch { return false; }
    if (displayed.command !== command) return true;
    // 紧接着出现的下一次确认不要求整个模态框隐藏。
    return (await window.workGui.sessions.pendingPermissions(sessionId))
      .some(item => item.request_id !== requestId && item.input?.command === displayed.command);
  }, { sessionId, requestId: request.request_id, command: request.input.command }, { timeout: 5000 });
  return true;
}

module.exports = { documentCleanupPermission, allowQworkPermission };
