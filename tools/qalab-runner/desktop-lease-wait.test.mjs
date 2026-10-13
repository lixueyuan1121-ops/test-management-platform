import test from 'node:test';
import assert from 'node:assert/strict';
import { waitForDesktopLease } from './verify-import.mjs';

test('available lease is returned without releasing it', async () => {
  let released = false;
  const release = async () => { released = true; };
  assert.equal(await waitForDesktopLease(async () => release, { waitMs: 0 }), release);
  assert.equal(released, false);
});

test('temporary contention waits and returns the acquired lease', async () => {
  let attempts = 0;
  const messages = [], release = async () => {};
  const result = await waitForDesktopLease(async () => ++attempts < 2 ? null : release,
    { waitMs: 2000, log: message => messages.push(message) });
  assert.equal(result, release);
  assert.equal(attempts, 2);
  assert.equal(messages.length, 2);
});

test('an occupied desktop times out without granting access', async () => {
  let attempts = 0;
  await assert.rejects(waitForDesktopLease(async () => { attempts++; return null; },
    { waitMs: 20 }), /桌面锁等待超时/);
  assert(attempts >= 2);
});

test('invalid wait budgets are rejected before acquiring a lease', async () => {
  for (const waitMs of [-1, NaN, Infinity, 300001]) {
    await assert.rejects(waitForDesktopLease(async () => assert.fail('must not acquire'),
      { waitMs }), /等待时间必须/);
  }
});
