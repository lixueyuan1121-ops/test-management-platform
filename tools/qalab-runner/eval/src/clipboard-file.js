// 把本地文件写进 macOS 系统剪贴板(public.file-url 格式),供 CDP Meta+V 粘贴到 WorkBuddy 输入框。
//
// 为什么这样做:WorkBuddy(Electron)附件走原生文件对话框,Playwright setInputFiles 不适用、
// contextBridge 冻结无法注入 stub。真机坐实的可行方案 = 系统剪贴板放 file-url + 粘贴,WorkBuddy
// 的 Slate 编辑器接住 file 渲染成 resource_link inline block。
//
// 剪贴板必须用 AppKit NSPasteboard writeObjects:{NSURL fileURLWithPath:...} —— 旧式
// `set the clipboard to POSIX file` 放不出有效格式(真机验证:clipboard info 为空,粘贴无效)。
// 仅 macOS(WorkBuddy 是 mac 客户端,执行机即 mac)。
const { execFileSync } = require('child_process');

// 拼「写 file-url 到剪贴板」的 osascript 参数数组(纯函数,可单测)。
// 返回形如 ['-e','use framework "AppKit"','-e',...],可直接 execFileSync('osascript', args)。
// 路径用 JSON.stringify 安全引用(含空格/中文/双引号均转义),避免注入 AppleScript 字符串。
function buildSetClipboardScript(paths) {
  const list = Array.isArray(paths) ? paths.filter((p) => p) : [];
  if (list.length === 0) throw new Error('buildSetClipboardScript: 空文件列表(无附件不应调用)');
  // 每个路径构造一个 NSURL,收集到 AppleScript list,再一次性 writeObjects
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

// 薄壳:执行上面的脚本,把文件写进剪贴板。返回 true(AppKit writeObjects 成功)。失败抛错。
function setClipboardFiles(paths) {
  const args = buildSetClipboardScript(paths);
  const out = execFileSync('osascript', args, { encoding: 'utf8', timeout: 8000 }).trim();
  if (out !== 'true') throw new Error(`写剪贴板失败(osascript 返回 "${out}")`);
  return true;
}

module.exports = { buildSetClipboardScript, setClipboardFiles };
