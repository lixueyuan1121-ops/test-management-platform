import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import net from 'node:net';
const require = createRequire(import.meta.url);
const { acquireDesktopLease } = require('./desktop-lease.cjs');

async function freePort() {
  const s = net.createServer();
  s.listen(0, '127.0.0.1');
  await once(s, 'listening');
  const port = s.address().port;
  await new Promise(resolve => s.close(resolve));
  return port;
}

test('two processes cannot own desktop; OS releases lease after crash', async () => {
  const port = await freePort();
  const child = spawn(process.execPath, ['-e', `
    const {acquireDesktopLease} = require(${JSON.stringify(require.resolve('./desktop-lease.cjs'))});
    acquireDesktopLease({port:${port}}).then(() => console.log('locked'));
  `], { stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    await once(child.stdout, 'data');
    assert.equal(await acquireDesktopLease({ port }), null);
    const exited = once(child, 'exit');
    child.kill('SIGKILL');
    await exited;
    const release = await acquireDesktopLease({ port });
    assert.equal(typeof release, 'function');
    await release();
    await release();
    const again = await acquireDesktopLease({ port });
    await again();
  } finally { child.kill(); }
});
