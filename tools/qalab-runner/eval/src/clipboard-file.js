// 把本地文件写进系统剪贴板(文件格式),供 CDP 粘贴(Cmd/Ctrl+V)到 WorkBuddy 输入框。跨平台。
//
// 为什么这样做:WorkBuddy(Electron)附件走原生文件对话框,Playwright setInputFiles 不适用、
// contextBridge 冻结无法注入 stub。真机坐实的可行方案 = 系统剪贴板放文件 + 粘贴,WorkBuddy 的
// Slate 编辑器接住 file 渲染成 resource_link inline block。
//   · mac(darwin):osascript + AppKit NSPasteboard writeObjects:{NSURL fileURLWithPath:}(public.file-url)。
//     旧式 `set the clipboard to POSIX file` 放不出有效格式(真机验证:clipboard info 为空,粘贴无效)。
//   · win(win32):powershell + [Windows.Forms.Clipboard]::SetFileDropList(CF_HDROP,等价资源管理器复制文件)。
// 平台由 process.platform 自动判定。exec 在薄壳里按平台跑对应命令。
const { execFileSync } = require('child_process');

// 拼 mac 的 osascript 参数数组:['-e','use framework...','-e',...]。路径用 JSON.stringify 安全引用。
function _macArgs(list) {
  const urlVars = list.map((_, i) => `u${i}`);
  const lines = [
    'use framework "AppKit"',
    'use scripting additions',
    "set pb to (current application's NSPasteboard's generalPasteboard())",
    "pb's clearContents()",
  ];
  list.forEach((p, i) => {
    lines.push(`set u${i} to (current application's NSURL's fileURLWithPath:${JSON.stringify(p)})`);
  });
  lines.push(`return (pb's writeObjects:{${urlVars.join(', ')}}) as text`);
  return lines.flatMap((l) => ['-e', l]);
}

// 拼 win 的 powershell 参数:用 SetFileDropList 写 CF_HDROP。单引号路径转义(' → '')防断字符串。
// STA 模式(-Sta)是剪贴板 API 要求;成功输出 "true" 供薄壳校验。
function _winArgs(list) {
  const psPaths = list.map((p) => `'${String(p).replace(/'/g, "''")}'`).join(', ');
  const script = [
    'Add-Type -AssemblyName System.Windows.Forms;',
    '$col = New-Object System.Collections.Specialized.StringCollection;',
    `$col.AddRange([string[]]@(${psPaths}));`,
    '[System.Windows.Forms.Clipboard]::Clear();',
    '[System.Windows.Forms.Clipboard]::SetFileDropList($col);',
    "Write-Output 'true'",
  ].join(' ');
  return ['-NoProfile', '-Sta', '-Command', script];
}

// 拼「写文件到剪贴板」的命令(纯函数,可单测)。返回 {cmd, args},cmd 是可执行文件、args 是参数数组。
// platform 缺省 process.platform;含空格/中文/引号的路径均安全引用。
function buildClipboardCommand(paths, platform = process.platform) {
  const list = Array.isArray(paths) ? paths.filter((p) => p) : [];
  if (list.length === 0) throw new Error('buildClipboardCommand: 空文件列表(无附件不应调用)');
  if (platform === 'darwin') return { cmd: 'osascript', args: _macArgs(list) };
  if (platform === 'win32') return { cmd: 'powershell', args: _winArgs(list) };
  throw new Error(`buildClipboardCommand: 不支持的平台 ${platform}(仅 darwin/win32)`);
}

// 薄壳:执行命令把文件写进剪贴板。成功返回 true;失败抛错。
function setClipboardFiles(paths, platform = process.platform) {
  const { cmd, args } = buildClipboardCommand(paths, platform);
  const out = execFileSync(cmd, args, { encoding: 'utf8', timeout: 10000 }).trim();
  if (!/true/i.test(out)) throw new Error(`写剪贴板失败(${cmd} 返回 "${out}")`);
  return true;
}

module.exports = { buildClipboardCommand, setClipboardFiles };
