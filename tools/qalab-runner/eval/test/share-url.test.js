// 从分享面板 DOM 候选里挑出分享 URL 的纯逻辑自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/share-url.test.js
//
// 背景:会话分享链接原只走系统剪贴板(navigator.clipboard.readText),失焦/权限/并发下概率读不到
// (真机 run-300:生成后剪贴板无 URL)。彻底解:点生成后优先直读面板 DOM——扫描面板内 input value /
// a[href] / 文本里的 http URL(优先级 input>href>text),不碰剪贴板;读不到再剪贴板兜底。

const assert = require('assert');
const { pickShareUrl } = require('../src/share-url');

function test_input_value_first() {
  const u = pickShareUrl({ inputs: ['https://work.n.cn/share/abc'], hrefs: ['https://help.x/other'], text: '' });
  assert.strictEqual(u, 'https://work.n.cn/share/abc', 'input value 的链接优先');
  console.log('✓ 优先直读 input value 里的链接');
}

function test_href_when_no_input() {
  const u = pickShareUrl({ inputs: ['', '  '], hrefs: ['https://work.n.cn/share/def'], text: '' });
  assert.strictEqual(u, 'https://work.n.cn/share/def', 'input 无 URL 时取 a[href]');
  console.log('✓ input 无链接时取 a[href]');
}

function test_text_last() {
  const u = pickShareUrl({ inputs: [], hrefs: [], text: '链接已生成: https://work.n.cn/share/ghi 请复制' });
  assert.strictEqual(u, 'https://work.n.cn/share/ghi', '兜底从可见文本提取');
  console.log('✓ 兜底从文本提取链接');
}

function test_none() {
  assert.strictEqual(pickShareUrl({ inputs: ['无链接'], hrefs: ['#'], text: '暂无' }), '', '无 URL 返回空');
  assert.strictEqual(pickShareUrl({}), '', '空输入返回空');
  console.log('✓ 无链接返回空(交剪贴板兜底)');
}

function test_trims_trailing_junk() {
  // URL 后跟引号/空白/尖括号不应带入
  const u = pickShareUrl({ inputs: ['"https://work.n.cn/share/xyz"'], hrefs: [], text: '' });
  assert.strictEqual(u, 'https://work.n.cn/share/xyz', '不带尾部引号');
  console.log('✓ 提取干净 URL(不含引号/尖括号)');
}

// 真机坐实(2026-09-07):分享面板 DOM 的 a[href] 全是产物文件 URL(ns.chat.360.cn/zhaomi-so/client-up/...),
// 对话分享链接(work.n.cn/share/...)只在剪贴板。原逻辑挑面板首个 http URL → 抓回产物链接(bug)。
// 修复:只认含 /share/ 的对话分享链接;产物链接不含 /share/ → 直读返回空 → 上层走剪贴板兜底拿真链接。
function test_ignores_artifact_hrefs() {
  const artifactHrefs = [
    'https://ns.chat.360.cn/zhaomi-so/client-up/98396d3d/做旧老照片.jpg',
    'https://ns.chat.360.cn/zhaomi-so/client-up/f061587d/人像.jpg?nm_preview=true',
  ];
  // 面板只有产物 href、无对话链接 → 应返回空(交剪贴板兜底),而非抓中产物
  assert.strictEqual(pickShareUrl({ inputs: [], hrefs: artifactHrefs, text: '' }), '', '产物链接不应被当分享链接');
  console.log('✓ 忽略面板里的产物文件链接(不含 /share/)');
}

function test_picks_share_among_artifacts() {
  // 产物 href 中混有真正的对话分享链接 → 精准挑出 /share/ 那个,不受产物顺序影响
  const mixed = [
    'https://ns.chat.360.cn/zhaomi-so/client-up/98396d3d/做旧老照片.jpg',
    'https://work.n.cn/share/pl5s7ggezss9iald?showReturn=0',
    'https://ns.chat.360.cn/zhaomi-so/client-up/f061587d/人像.jpg',
  ];
  assert.strictEqual(pickShareUrl({ inputs: [], hrefs: mixed, text: '' }),
    'https://work.n.cn/share/pl5s7ggezss9iald?showReturn=0', '混杂中精准挑出 /share/ 链接');
  console.log('✓ 产物混杂中精准挑出对话分享链接');
}

function test_text_share_among_artifact_text() {
  // 剪贴板/文本兜底同理:文本里既有产物 URL 又有分享 URL,挑 /share/
  const text = '产物 https://ns.chat.360.cn/zhaomi-so/client-up/x/a.jpg 分享 https://work.n.cn/share/ghi 完';
  assert.strictEqual(pickShareUrl({ inputs: [], hrefs: [], text }), 'https://work.n.cn/share/ghi', '文本兜底也只认 /share/');
  console.log('✓ 文本兜底只认 /share/');
}

function main() {
  test_input_value_first();
  test_href_when_no_input();
  test_text_last();
  test_none();
  test_trims_trailing_junk();
  test_ignores_artifact_hrefs();
  test_picks_share_among_artifacts();
  test_text_share_among_artifact_text();
  console.log('\n✅ 分享 URL 提取 全部通过');
}
main();
