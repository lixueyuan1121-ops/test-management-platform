const assert = require('node:assert/strict');
const http = require('node:http');
const PlatformClient = require('../src/platform-client');

async function main() {
  const requests = [];
  let rejectReport = false;
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    requests.push({ method: req.method, url });
    req.resume();
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'PATCH' && rejectReport) {
      res.statusCode = 409;
      res.end(JSON.stringify({ detail: 'expired claim' }));
      return;
    }
    const data = url.pathname.endsWith('/claim')
      ? { run_ids: [1, 2], claim_token: 'test-claim' } : [];
    res.end(JSON.stringify({ code: 0, data }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const client = new PlatformClient({ baseUrl: `http://127.0.0.1:${server.address().port}`, token: 'test', runnerId: 'r2' });
  try {
    await client.fetchPending(1);
    assert.equal(requests.at(-1).url.searchParams.get('dynamic'), 'true');
    assert.equal(requests.at(-1).url.searchParams.get('limit'), '1');
    await client.claimGroup([{ run_id: 1 }, { run_id: 2 }]);
    assert.equal(requests.at(-1).url.searchParams.get('whole_group'), 'true');
    assert.equal(client.claims.size, 2);
    await client.uploadTrace(1, {});
    assert.equal(requests.at(-1).url.searchParams.get('claim_token'), 'test-claim');
    rejectReport = true;
    await assert.rejects(client.report(1, { status: 'done' }), /expired claim/);
    assert.equal(client.claims.size, 2);
    rejectReport = false;
    await client.report(1, { status: 'done' });
    assert.equal(requests.at(-1).url.searchParams.get('claim_token'), 'test-claim');
    assert.equal(client.claims.size, 1);
    await assert.rejects(client.report(1, { status: 'done' }), /未认领/);
    client.stopHeartbeat();
    assert.equal(client.claims.size, 0);
    assert.equal(client.heartbeatTimer, null);
    console.log('OK platform-client dynamic queue, token forwarding, HTTP 409, cleanup');
  } finally {
    client.stopHeartbeat();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
