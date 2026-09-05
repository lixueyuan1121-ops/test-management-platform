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

function main() {
  test_input_value_first();
  test_href_when_no_input();
  test_text_last();
  test_none();
  test_trims_trailing_junk();
  console.log('\n✅ 分享 URL 提取 全部通过');
}
main();
