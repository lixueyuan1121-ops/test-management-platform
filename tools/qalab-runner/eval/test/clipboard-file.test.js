// 「把本地文件路径拼成写 public.file-url 到系统剪贴板的 AppleScript」纯函数自测(无框架,node 直跑)。
// 运行: node tools/qalab-runner/eval/test/clipboard-file.test.js
//
// 背景:WorkBuddy 附件走 Electron 原生文件对话框(setInputFiles 不适用,contextBridge 冻结)。
// 真机坐实可行方案 = 把附件写进系统剪贴板(public.file-url 格式)→ 聚焦编辑器 → CDP Meta+V 粘贴,
// WorkBuddy 接住 file 渲染成 resource_link inline block([data-content-block-meta-type=file])。
// 系统剪贴板须用 AppKit NSPasteboard writeObjects:{fileURL}(NSURL fileURLWithPath) —— 旧式
// `set the clipboard to POSIX file` 放不出有效格式(真机验证过)。本函数只负责拼命令,exec 在薄壳里。

const assert = require('assert');
const { buildSetClipboardScript } = require('../src/clipboard-file');

function test_single_file_uses_appkit_nspasteboard() {
  const args = buildSetClipboardScript(['/tmp/a.txt']);
  const joined = args.join('\n');
  assert.ok(joined.includes('NSPasteboard'), '应用 NSPasteboard');
  assert.ok(joined.includes('clearContents'), '应先 clearContents');
  assert.ok(joined.includes('writeObjects'), '应 writeObjects 写入 fileURL');
  assert.ok(joined.includes('fileURLWithPath'), '应用 NSURL fileURLWithPath 构造 file-url');
  assert.ok(joined.includes('/tmp/a.txt'), '应含该文件路径');
  console.log('✓ 单文件:用 AppKit NSPasteboard 写 file-url');
}

function test_path_is_json_quoted() {
  // 路径含空格/中文,须被安全引用(JSON.stringify 加双引号转义),不能裸拼进 AppleScript 字符串
  const args = buildSetClipboardScript(['/tmp/带 空格/图片.png']);
  const joined = args.join('\n');
  assert.ok(joined.includes('"/tmp/带 空格/图片.png"'), '路径应被双引号安全包裹');
  console.log('✓ 含空格/中文路径:安全引用');
}

function test_multiple_files() {
  const args = buildSetClipboardScript(['/tmp/a.txt', '/tmp/b.png']);
  const joined = args.join('\n');
  assert.ok(joined.includes('/tmp/a.txt') && joined.includes('/tmp/b.png'), '应含两个文件路径');
  // 两个 URL 都要进 writeObjects 的列表
  assert.ok(/writeObjects:\{[^}]*\}/.test(joined), 'writeObjects 应接一个对象列表');
  console.log('✓ 多文件:都写入剪贴板');
}

function test_empty_throws() {
  assert.throws(() => buildSetClipboardScript([]), /空|empty|no file/i, '空列表应抛错(无附件不该调用)');
  assert.throws(() => buildSetClipboardScript(null), /空|empty|no file/i, 'null 应抛错');
  console.log('✓ 空/null:抛错(防误用)');
}

function test_returns_osascript_e_args() {
  // 返回的应是可直接喂给 execFileSync('osascript', args) 的参数数组:每条语句前带 -e
  const args = buildSetClipboardScript(['/tmp/a.txt']);
  assert.ok(Array.isArray(args), '应返回数组');
  assert.strictEqual(args[0], '-e', '首元素应为 -e(osascript 逐句参数形式)');
  const eCount = args.filter(x => x === '-e').length;
  assert.ok(eCount >= 4, `应有多条 -e 语句(实际 ${eCount})`);
  console.log('✓ 返回 osascript -e 参数数组');
}

function main() {
  test_single_file_uses_appkit_nspasteboard();
  test_path_is_json_quoted();
  test_multiple_files();
  test_empty_throws();
  test_returns_osascript_e_args();
  console.log('\n✅ 剪贴板 file-url 命令拼装 全部通过');
}
main();
