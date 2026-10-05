import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runCodex, codexEvent, codexConfigArgs } from './codex-engine.mjs';

const toolEvent = (pass = true) => ({ type: 'item.completed', item: { type: 'mcp_tool_call', server: 'qalab_gui', tool: 'gui_assert_text', status: 'completed', arguments: { expected: '完成' }, result: { content: [{ type: 'text', text: JSON.stringify({ pass, actual: pass ? '完成' : '失败' }) }] } } });
test('Codex tool results require actual assertion success, not just transport success', () => {
  assert.equal(codexEvent(toolEvent()).check.pass, true);
  assert.equal(codexEvent(toolEvent(false)).ok, false);
  const missing = toolEvent(); missing.item.result = {};
  assert.equal(codexEvent(missing).check.pass, false);
  const unrelated = toolEvent(); unrelated.item.server = 'other'; assert.equal(codexEvent(unrelated), null);
});
test('runner config narrows MCP to local GUI; judges have no GUI server; paths stay data', () => {
  const opts = { directory: '/tmp/space "& dir', registryFile: '/tmp/registry.json', cdpUrl: 'http://127.0.0.1:9222', servers: [{ name: 'other-server' }] };
  const args = codexConfigArgs(opts);
  assert(args.includes('features.shell_tool=false')); assert(args.includes('features.hooks=false'));
  assert(args.some(a => a.startsWith('mcp_servers.qalab_gui.enabled_tools=') && a.includes('gui_assert_text')));
  const enabled = JSON.parse(args.find(a => a.startsWith('mcp_servers.qalab_gui.enabled_tools=')).split('=')[1]);
  const approvals = args.filter(a => a.endsWith('.approval_mode="approve"'));
  assert.equal(approvals.length, enabled.length);
  for (const tool of enabled) assert(approvals.includes(`mcp_servers.qalab_gui.tools.${tool}.approval_mode="approve"`));
  assert(!enabled.includes('gui_screenshot'), 'no model-selected filesystem writes');
  assert(!args.some(a => a.includes('default_tools_approval_mode')));
  assert(!codexConfigArgs({ ...opts, gui: false }).some(a => a.startsWith('mcp_servers.qalab_gui.')));
});
test('noninteractive adapter preserves stdin, handles chunked events and cleans temporary files', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'qalab-codex-fixture-'));
  try {
    const bin = join(dir, 'fake-cli.mjs'), capture = join(dir, 'capture.json');
    writeFileSync(bin, `import {writeFileSync} from 'node:fs';
if(process.argv.includes('mcp')) { console.log(JSON.stringify([{name:'unrelated'}])); }
else { let input=''; for await (const c of process.stdin) input+=c;
writeFileSync(${JSON.stringify(capture)},JSON.stringify({input,args:process.argv,cwd:process.cwd(),home:process.env.CODEX_HOME,token:process.env.RUNNER_TOKEN}));
const event=${JSON.stringify(toolEvent())};const text=JSON.stringify(event);process.stdout.write(text.slice(0,30));process.stdout.write(text.slice(30)+'\\n');
console.log(JSON.stringify({type:'error',message:'Reconnecting (fixture)'}));
console.log(JSON.stringify({type:'item.completed',item:{type:'agent_message',text:'{"verdict":"pass","reason":"实测"}'}}));
console.log(JSON.stringify({type:'turn.completed',usage:{}})); }
`);
    const prompt = '测试 " & $(touch should-not-exist) \n视觉';
    const result = await runCodex({ prompt, systemPrompt: 'test', home: dir, bin, cdpUrl: 'http://127.0.0.1:9222', registry: {}, timeoutMs: 5000 });
    assert.equal(result.code, 0); assert.equal(result.failed, false, 'a recovered stream error is not a failed turn'); assert.equal(result.observed[0].check.pass, true); assert.equal(JSON.parse(result.text).verdict, 'pass');
    const sent = JSON.parse(readFileSync(capture));
    assert.equal(sent.input, prompt); assert(!sent.args.includes(prompt)); assert.equal(sent.home, dir); assert.equal(sent.token, undefined);
    assert(sent.args.includes('--ignore-user-config'));
    assert(!existsSync(sent.cwd), 'per-call config removed');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('missing installation/config, failed turn and timeout cannot masquerade as success', async () => {
  assert((await runCodex({ prompt: '', systemPrompt: '', home: 'relative' })).spawnError);
  const dir = mkdtempSync(join(tmpdir(), 'qalab-codex-failure-'));
  try {
    assert((await runCodex({ prompt: '', systemPrompt: '', home: dir, bin: join(dir, 'missing') })).spawnError);
    const bin = join(dir, 'failure.mjs');
    writeFileSync(bin, `if(process.argv.includes('mcp')) console.log('[]'); else console.log(JSON.stringify({type:'turn.failed',error:{message:'quota'}}));`);
    const result = await runCodex({ prompt: '', systemPrompt: '', home: dir, bin, gui: false });
    assert.equal(result.failed, true);
    writeFileSync(bin, `if(process.argv.includes('mcp')) console.log('[]'); else setInterval(()=>{},1000);`);
    const timeout = await runCodex({ prompt: '', systemPrompt: '', home: dir, bin, gui: false, timeoutMs: 200 });
    assert.equal(timeout.timedOut, true); assert(timeout.duration_ms < 5000);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
