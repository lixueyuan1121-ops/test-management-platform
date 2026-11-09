// Exercise the shipped client with an ASGI transport over stdio. No test opens a device or port.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [install, evidenceDir, androidToken, iosToken] = process.argv.slice(2);
const modulePath = f => pathToFileURL(join(install, 'mobile', f));
const { processPending } = await import(modulePath('runner.mjs'));
const { platformClient } = await import(modulePath('platform.mjs'));
const { readConfig } = await import(modulePath('device.mjs'));
const replies = createInterface({input: process.stdin})[Symbol.asyncIterator]();

async function exchange(frame) {
  process.stdout.write(JSON.stringify(frame) + '\n');
  const {value, done} = await replies.next();
  if (done) throw new Error('Contract service ended unexpectedly');
  return JSON.parse(value);
}
async function request(url, init) {
  // Use Node's actual multipart encoder/headers so upload compatibility is covered.
  const req = new Request(url, init);
  const chunks = [];
  if (req.body) for await (const chunk of req.body) chunks.push(Buffer.from(chunk));
  const reply = await exchange({
    method: req.method,
    path: url.slice(new URL(url).origin.length),
    headers: Object.fromEntries(req.headers),
    body: Buffer.concat(chunks).toString('base64'),
  });
  return Response.json(reply.data, {status: reply.status});
}
const devices = [];
for (const [platform, token] of [['android', androidToken], ['ios', iosToken]]) {
  const config = readConfig({
    MOBILE_PLATFORM: platform,
    MOBILE_UDID: platform === 'android' ? 'contract-phone' : '00008100-0000000000000001',
    MOBILE_APP_ID: 'test.contract.mobile',
    MOBILE_EVIDENCE_DIR: evidenceDir,
    RUNNER_TOKEN: token,
    RUNNER_ID: 'contract-' + platform,
    BASE_URL: 'http://contract.invalid',
  });
  const api = platformClient(config, request);
  const device = {
    platform, device_id: config.deviceId, app_id: config.appId, build: '101', model: 'Fixture',
    os_version: '17', online: true, physical: true,
  };
  await api.presence(device);
  devices.push({config, api, device});
}
assert.equal((await exchange({dispatch:true})).run_count, 8);
for (const {config, api, device} of devices) {
  const observed = new Set();
  for (let i = 0; i < 4; i++) {
    const [item] = await api.pending();
    assert.ok(item);
    observed.add(item.payload.mobile_scenario);
    // Deliberately block all scenarios at app activation. No device or product was exercised.
    const driver = {
      mobile: async name => {
        if (name === 'queryAppState') return 4;
        throw new Error('Contract fixture: no product was operated');
      },
      screenshot: async () => Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64'),
      source: async () => '<fixture/>',
    };
    await processPending({config, driver, device, platform:api, item});
  }
  assert.deepEqual(observed, new Set(['send','followup','stop','history']));
  assert.deepEqual(await api.pending(), []);
}
process.stdin.destroy();
