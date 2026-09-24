const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const http = require('node:http');
const fetch = require('node-fetch');
const PlatformClient = require('../src/platform-client');

test('runner and OCR dependencies do not load deprecated builtin punycode', () => {
  const script = `
    const Module = require('node:module');
    const load = Module._load;
    Module._load = function (name, ...args) {
      if (name === 'punycode' || name === 'node:punycode') throw new Error('deprecated builtin punycode');
      return load.call(this, name, ...args);
    };
    require('./src/platform-client');
    require('./src/artifact-collector');
    require('./src/feishu-sheet');
    require('tesseract.js');
    console.log('modules ready');
  `;
  const result = spawnSync(process.execPath, ['--throw-deprecation', '-e', script], {
    cwd: path.resolve(__dirname, '..'), encoding: 'utf8', timeout: 10000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /modules ready/);
  assert.doesNotMatch(result.stderr, /DEP0040|DeprecationWarning/);
});

test('URL handling still supports Unicode domains, encoded paths and query values', () => {
  const request = new fetch.Request('https://例子.测试/模型?name=中文');
  const url = new URL(request.url);
  assert.equal(url.hostname, 'xn--fsqu00a.xn--0zwm56d');
  assert.equal(decodeURIComponent(url.pathname), '/模型');
  assert.equal(url.searchParams.get('name'), '中文');
});

test('relative redirects, binary downloads and multipart trace/artifact uploads still work', async () => {
  const received = [];
  const bytes = Buffer.from([0, 1, 127, 128, 255]);
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const part of req) chunks.push(part);
    const body = Buffer.concat(chunks);
    if (req.url === '/redirect') {
      res.writeHead(302, { Location: '/file' }); res.end(); return;
    }
    if (req.url === '/file') { res.end(bytes); return; }
    received.push({ url: req.url, headers: req.headers, body });
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ code: 0, data: { uploaded: true } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  try {
    assert.deepEqual(await (await fetch(`${baseUrl}/redirect`)).buffer(), bytes);
    const manual = await fetch(`${baseUrl}/redirect`, { redirect: 'manual' });
    assert.equal(manual.status, 302);
    assert.equal(manual.headers.get('location'), `${baseUrl}/file`);
    await manual.buffer();
    const client = new PlatformClient({ baseUrl, token: 'local-test', runnerId: 'mac-test' });
    client.claims.set(1, 'local-claim');
    await client.uploadTrace(1, { answer: '回填测试' });
    await client.uploadArtifact(1, 'image.png', bytes);
    assert.equal(received.length, 2);
    for (const req of received) {
      assert.match(req.headers['content-type'], /^multipart\/form-data;\s*boundary=/);
      assert.equal(req.headers.authorization, 'Bearer local-test');
      assert.match(req.url, /claim_token=local-claim/);
    }
    assert.match(received[0].body.toString(), /回填测试/);
    assert.ok(received[1].body.includes(bytes));
    assert.match(received[1].body.toString(), /filename="image.png"/);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
