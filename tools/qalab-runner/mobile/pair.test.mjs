import test from 'node:test';
import assert from 'node:assert/strict';
import {discoverPhone} from './pair.mjs';

test('paired launcher selects one authorized USB phone per platform', async () => {
  const fake = async (tool, args) => tool === 'idevice_id' ? '00008150-001259222200401C\n' :
    args[0] === 'devices' ? 'List of devices attached\nA1 device usb:7 model:X\nemulator-5554 device product:sdk\nB2 unauthorized usb:8\n' : '';
  assert.equal(await discoverPhone('android', fake), 'A1');
  assert.equal(await discoverPhone('ios', fake), '00008150-001259222200401C');
  await assert.rejects(discoverPhone('android', async ()=>'List of devices attached\nA device usb:1\nB device usb:2\n'),/2/);
});
