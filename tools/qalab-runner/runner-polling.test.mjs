import test from 'node:test';
import assert from 'node:assert/strict';
import { createPollSchedule } from './runner-polling.mjs';
test('queue pickup every second without increasing maintenance frequency', () => {
  let now = 0;
  const schedule = createPollSchedule({ pollMs: 5000, now: () => now });
  const queue = [], maintenance = [];
  while (now <= 10000) {
    queue.push(now);
    if (schedule.maintenanceDue()) maintenance.push(now);
    now += schedule.delay(0);
  }
  assert.equal(queue.length, 11);
  assert.deepEqual(maintenance, [0, 5000, 10000]);
  assert.equal(queue.find(time => time >= 250) - 250, 750);
});
test('completed batches drain immediately; idle or failed claims cannot spin', () => {
  const schedule = createPollSchedule();
  assert.equal(schedule.delay(5), 0);
  assert.equal(schedule.delay(0), 1000);
  assert.equal(schedule.delay(undefined), 1000);
});
test('explicit slower cadence and invalid intervals are bounded', () => {
  assert.equal(createPollSchedule({ execPollMs: 3000 }).executionMs, 3000);
  assert.equal(createPollSchedule({ pollMs: 500 }).executionMs, 500);
  for (const value of [0, -1, NaN, Infinity, 'bad']) {
    const schedule = createPollSchedule({ pollMs: value, execPollMs: value });
    assert.equal(schedule.executionMs, 1000);
    assert.equal(schedule.maintenanceMs, 5000);
  }
});
