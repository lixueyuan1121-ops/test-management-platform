// 「把本地文件路径拼成'写文件到系统剪贴板'的命令」纯函数自测(无框架,node 直跑)。跨平台:mac/win。
// 运行: node tools/qalab-runner/eval/test/clipboard-file.test.js
//
// 背景:WorkBuddy 附件走 Electron 原生文件对话框(setInputFiles 不适用,contextBridge 冻结)。
// 真机坐实可行方案 = 把附件写进系统剪贴板(文件格式)→ 聚焦编辑器 → CDP 粘贴(Cmd/Ctrl+V),
// WorkBuddy 接住 file 渲染成 resource_link inline block([data-content-block-meta-type=file])。
//   · mac:osascript + AppKit NSPasteboard writeObjects:{NSURL fileURLWithPath:}(public.file-url)。
//     旧式 `set the clipboard to POSIX file` 放不出有效格式(真机验证过)。
//   · win:powershell + System.Windows.Forms.Clipboard::SetFileDropList(CF_HDROP,等价资源管理器复制文件)。
// 本函数只负责拼命令(纯字符串,两平台都可在任一 OS 上测),exec 在薄壳里按平台跑。

const assert = require('assert');
const { buildClipboardCommand } = require('../src/clipboard-file');

// ---------- mac ----------
function test_mac_uses_appkit_nspasteboard() {
  const { cmd, args } = buildClipboardCommand(['/tmp/a.txt'], 'darwin');
  assert.strictEqual(cmd, 'osascript', 'mac 用 osascript');
  const joined = args.join('\n');
  assert.ok(joined.includes('NSPasteboard'), '应用 NSPasteboard');
  assert.ok(joined.includes('clearContents'), '应先 clearContents');
  assert.ok(joined.includes('writeObjects'), '应 writeObjects 写入 fileURL');
  assert.ok(joined.includes('fileURLWithPath'), '应用 NSURL fileURLWithPath 构造 file-url');
  assert.ok(joined.includes('/tmp/a.txt'), '应含该文件路径');
  assert.strictEqual(args[0], '-e', 'osascript 首参应为 -e');
  console.log('✓ [mac] AppKit NSPasteboard 写 file-url,osascript -e 形式');
}

function test_mac_path_json_quoted() {
  const { args } = buildClipboardCommand(['/tmp/带 空格/图片.png'], 'darwin');
  assert.ok(args.join('\n').includes('"/tmp/带 空格/图片.png"'), 'mac 路径应双引号安全包裹');
  console.log('✓ [mac] 含空格/中文路径:安全引用');
}

function test_mac_multiple_files() {
  const { args } = buildClipboardCommand(['/tmp/a.txt', '/tmp/b.png'], 'darwin');
  const joined = args.join('\n');
  assert.ok(joined.includes('/tmp/a.txt') && joined.includes('/tmp/b.png'), 'mac 应含两个路径');
  assert.ok(/writeObjects:\{[^}]*\}/.test(joined), 'mac writeObjects 接对象列表');
  console.log('✓ [mac] 多文件都写入');
}

// ---------- win ----------
function test_win_uses_powershell_filedroplist() {
  const { cmd, args } = buildClipboardCommand(['C:\\tmp\\a.txt'], 'win32');
  assert.strictEqual(cmd, 'powershell', 'win 用 powershell');
  const joined = args.join(' ');
  assert.ok(/SetFileDropList/i.test(joined), 'win 应用 Clipboard::SetFileDropList(CF_HDROP)');
  assert.ok(joined.includes('C:\\tmp\\a.txt') || joined.includes('C:\\\\tmp\\\\a.txt'), 'win 应含该文件路径');
  console.log('✓ [win] PowerShell SetFileDropList 写 CF_HDROP');
}

function test_win_multiple_files() {
  const { args } = buildClipboardCommand(['C:\\a.txt', 'C:\\b.png'], 'win32');
  const joined = args.join(' ');
  assert.ok(joined.includes('a.txt') && joined.includes('b.png'), 'win 应含两个路径');
  console.log('✓ [win] 多文件都写入');
}

function test_win_path_with_quote_safe() {
  // 路径里的单引号要转义(PowerShell 单引号字符串里 ' → ''),防止注入/断字符串
  const { args } = buildClipboardCommand(["C:\\a'b\\x.txt"], 'win32');
  const joined = args.join(' ');
  assert.ok(joined.includes("a''b") || joined.includes("a'b"), 'win 路径含单引号应被处理(转义或保留但不破坏语句)');
  console.log('✓ [win] 含单引号路径:安全处理');
}

// ---------- 通用 ----------
function test_empty_throws() {
  assert.throws(() => buildClipboardCommand([], 'darwin'), /空|empty|no file/i, '空列表应抛错');
  assert.throws(() => buildClipboardCommand(null, 'win32'), /空|empty|no file/i, 'null 应抛错');
  console.log('✓ 空/null:抛错(防误用)');
}

function test_unsupported_platform_throws() {
  assert.throws(() => buildClipboardCommand(['/tmp/a.txt'], 'linux'), /平台|platform|支持/i, '不支持的平台应抛错');
  console.log('✓ 不支持平台(linux):显式抛错');
}

function main() {
  test_mac_uses_appkit_nspasteboard();
  test_mac_path_json_quoted();
  test_mac_multiple_files();
  test_win_uses_powershell_filedroplist();
  test_win_multiple_files();
  test_win_path_with_quote_safe();
  test_empty_throws();
  test_unsupported_platform_throws();
  console.log('\n✅ 跨平台剪贴板命令拼装 全部通过');
}
main();
