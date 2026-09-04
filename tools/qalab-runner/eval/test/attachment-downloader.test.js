// 平台模式附件下载器自测(无测试框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/attachment-downloader.test.js
//
// 背景:平台模式拉到的 pending,其 payload.attachments 是 [{name,url}](公开 CDN 链接)。
// 执行前须下载到本地、以本地路径喂给桌面执行器上传;下载失败即抛错(上层 fail-closed 整组,
// 绝不缺附件裸跑污染判定)。文件名须防路径遍历(name 来自平台数据,可能含 ../)。

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { downloadAttachments } = require('../src/attachment-downloader');

function fakeFetch(map) {
  // map: url -> { ok?:bool, status?:int, body?:Buffer }
  return async (url) => {
    const e = map[url];
    if (!e) return { ok: false, status: 404, buffer: async () => Buffer.alloc(0) };
    return { ok: e.ok !== false, status: e.status || 200, buffer: async () => e.body || Buffer.from('x') };
  };
}
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'att-'));

async function test_downloads_to_local_paths() {
  const dir = tmp();
  const paths = await downloadAttachments(
    [{ name: 'input_lowres.jpg', url: 'https://cdn/x.jpg' }], dir,
    { fetchImpl: fakeFetch({ 'https://cdn/x.jpg': { body: Buffer.from('IMGDATA') } }) });
  assert.strictEqual(paths.length, 1, '应返回 1 个本地路径');
  assert.ok(fs.existsSync(paths[0]), '文件应真正落地');
  assert.strictEqual(fs.readFileSync(paths[0], 'utf8'), 'IMGDATA', '内容应为下载的字节');
  assert.strictEqual(path.basename(paths[0]), 'input_lowres.jpg', '文件名应取自附件 name');
  console.log('✓ 下载附件到本地路径,内容与文件名正确');
}

async function test_http_error_throws() {
  const dir = tmp();
  await assert.rejects(
    downloadAttachments([{ name: 'a', url: 'https://cdn/bad' }], dir,
      { fetchImpl: fakeFetch({ 'https://cdn/bad': { ok: false, status: 500 } }) }),
    /下载附件失败.*500/, 'HTTP 非 2xx 应抛错(供上层 fail-closed)');
  console.log('✓ HTTP 错误抛出,不静默');
}

async function test_missing_url_throws() {
  const dir = tmp();
  await assert.rejects(
    downloadAttachments([{ name: 'a' }], dir, { fetchImpl: fakeFetch({}) }),
    /缺少可下载 url/, '附件无 url 应抛错');
  console.log('✓ 附件缺 url 抛出');
}

async function test_path_traversal_contained() {
  const dir = tmp();
  const paths = await downloadAttachments(
    [{ name: '../../evil.js', url: 'https://cdn/e' }], dir,
    { fetchImpl: fakeFetch({ 'https://cdn/e': { body: Buffer.from('x') } }) });
  const rel = path.relative(dir, paths[0]);
  assert.ok(!rel.startsWith('..') && !path.isAbsolute(rel),
    `附件必须落在 destDir 内(防路径遍历),实际 ${paths[0]}`);
  console.log('✓ 恶意文件名被约束在目标目录内(防路径遍历)');
}

async function main() {
  await test_downloads_to_local_paths();
  await test_http_error_throws();
  await test_missing_url_throws();
  await test_path_traversal_contained();
  console.log('\n✅ 附件下载器 全部通过');
}
main().catch((e) => { console.error('✗ 测试失败:', e.message); process.exit(1); });
