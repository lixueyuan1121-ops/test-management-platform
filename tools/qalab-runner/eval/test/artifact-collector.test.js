const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const { collectArtifacts, candidatesFromPage, safeUrl, runnerMetadata } = require('../src/artifact-collector');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

test('only the current answer contributes files; actual bytes are uploaded and hashed', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="chat-group assistant"><a href="https://assets.example.com/old.md">旧文件.md</a></div><div class="chat-group assistant"><a href="https://assets.example.com/result.md" download="报告.md">下载</a></div>');
    const candidates = await candidatesFromPage(page);
    assert.equal(candidates.length, 1);
    assert.equal(candidates[0].name, '报告.md');
    const bytes = Buffer.from('实际报告内容');
    const trace = { artifacts: [] };
    let uploads = 0;
    await collectArtifacts({page, trace, rules:[{kind:'file_readable',file_pattern:'*.md'}],runId:7,
      downloadFile:async()=>({data:bytes,contentType:'text/markdown'}),
      client:{uploadArtifact:async(id,name,data)=>{uploads++;assert.equal(id,7);assert.equal(name,'报告.md');assert(data.equals(bytes));return {artifact_id:3,sha256:crypto.createHash('sha256').update(data).digest('hex')};}}});
    assert.equal(uploads, 1);
    assert.equal(trace.captured_artifacts[0].artifact_id, 3);
    assert.equal(trace.artifact_capture.status, 'captured');
  } finally { await page.close(); }
});
test('normal single execution skips file collection and capture errors stay unknown', async () => {
  let calls = 0;
  await collectArtifacts({rules:[],page:{frames:()=>{calls++;}}});
  assert.equal(calls,0);
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="chat-group assistant"><a href="https://assets.example.com/result.xlsx">结果.xlsx</a></div>');
    const trace = {};
    await collectArtifacts({page,trace,rules:[{}],runId:8,client:{uploadArtifact:()=>{throw Error('must not upload')}},downloadFile:async()=>{throw Error('下载超时')}});
    assert.equal(trace.artifact_capture.status,'unknown');
    assert.match(trace.artifact_capture.diagnostics[0].reason,/下载超时/);
  } finally { await page.close(); }
});
test('file URLs cannot access loopback, private addresses, metadata or credentials', async () => {
  for (const url of ['file:///etc/passwd','http://127.0.0.1/x','http://169.254.169.254/x','http://10.0.0.1/x','http://[::1]/x','https://user:password@example.com/a']) {
    await assert.rejects(safeUrl(url));
  }
  assert.match(runnerMetadata().runner_hash,/^[0-9a-f]{64}$/);
});

test('images are uploaded without verification rules, including extensionless displayed images', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="chat-group assistant"><a href="https://assets.example.com/old.png">旧图片</a></div><div class="chat-group assistant"><a href="https://assets.example.com/result.png">下载图片</a><img src="https://assets.example.com/preview?id=1"><a href="https://assets.example.com/report.xlsx">报告</a></div>');
    const trace = {}, uploads = [];
    await collectArtifacts({ page, trace, runId: 9, rules: [],
      downloadFile: async (_page, url) => { assert(!url.includes('old') && !url.includes('xlsx')); return { data: Buffer.from('image bytes'), contentType: 'image/png' }; },
      client: { uploadArtifact: async (id, name, bytes) => { uploads.push(name); assert.equal(id, 9); assert.equal(bytes.toString(), 'image bytes'); return { name }; } } });
    assert.deepEqual(uploads, ['result.png', 'preview.png']);
    assert.equal(trace.captured_artifacts.length, 2);
  } finally { await page.close(); }
});

test('ordinary text run without rules does not download non-image files', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="chat-group assistant"><a href="https://assets.example.com/report.xlsx">报告</a></div>');
    await collectArtifacts({ page, trace: {}, runId: 9, rules: [], client: {}, downloadFile: async () => assert.fail('no image to fetch') });
  } finally { await page.close(); }
});
