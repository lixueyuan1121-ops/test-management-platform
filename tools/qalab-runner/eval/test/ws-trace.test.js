const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { attachWsTrace } = require('../src/ws-trace');

function setup() {
  const page = new EventEmitter();
  const collector = attachWsTrace(page);
  const ws = new EventEmitter();
  page.emit('websocket', ws);
  return { page, collector, ws };
}

test('reset keeps a live socket and clears only turn data', () => {
  const { ws, collector } = setup();
  ws.emit('framereceived', { payload: JSON.stringify({ type: 'event', event: 'agent', payload: { stream: 'thinking', data: { text: 'first' } } }) });
  assert.equal(collector.buildTrace(1).thinking, 'first');
  collector.reset();
  assert.equal(collector.buildTrace(2).ws_connected, true);
  assert.equal(collector.buildTrace(2).thinking, '');
  assert.equal(collector.buildTrace(2).capture_diagnostics.frames, 0);
  ws.emit('close');
  assert.equal(collector.buildTrace(2).ws_connected, false);
});

test('binary JSON and leading whitespace are not silently discarded', () => {
  const { ws, collector } = setup();
  const payload = ' \n' + JSON.stringify({ type: 'event', event: 'agent', payload: { stream: 'tool', data: { toolCallId: 't1', name: 'bash', phase: 'result', result: 'ok' } } });
  ws.emit('framereceived', { payload: Buffer.from(payload) });
  const trace = collector.buildTrace(1);
  assert.equal(trace.tool_calls[0].result_text, 'ok');
  assert.equal(trace.capture_diagnostics.binary_frames, 1);
  assert.equal(trace.capture_diagnostics.streams.tool, 1);
});

test('diagnostics distinguish malformed frames, ignored traffic and unknown streams', () => {
  const { ws, collector } = setup();
  ws.emit('framereceived', { payload: '{bad' });
  ws.emit('framereceived', { payload: 'ping' });
  ws.emit('framereceived', { payload: JSON.stringify({ type: 'event', event: 'agent', payload: { stream: 'new-protocol', data: { text: 'private content' } } }) });
  const diag = collector.buildTrace(1).capture_diagnostics;
  assert.equal(diag.frames, 3);
  assert.equal(diag.parse_errors, 1);
  assert.equal(diag.ignored_frames, 1);
  assert.equal(diag.streams['new-protocol'], 1);
  assert.equal(JSON.stringify(diag).includes('private content'), false);
});

test('disposing collector removes listeners before switching devices', () => {
  const { page, ws, collector } = setup();
  collector.dispose();
  assert.equal(page.listenerCount('websocket'), 0);
  assert.equal(ws.listenerCount('framereceived'), 0);
  assert.equal(ws.listenerCount('close'), 0);
});
