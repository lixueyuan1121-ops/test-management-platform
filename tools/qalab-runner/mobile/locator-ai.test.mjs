import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createLocatorAI } from './locator-ai.mjs';

async function environment(t, extra = {}) {
  const cacheDir = await mkdtemp(join(tmpdir(), 'locator-ai-test-'));
  t.after(() => rm(cacheDir, {recursive: true, force: true}));
  return {platform: 'android', appId: 'org.example.mobile', build: '10', cacheDir, ...extra};
}
const button = {using: 'id', value: 'org.example.mobile:id/chatSendButton'};
const backward = {using: 'accessibility id', value: 'chatBackButton'};

test('the model can only choose an observed index; proposed and unsuccessful controls stay out of verified cache', async t => {
  const cfg = await environment(t);
  let index = 1;
  const ai = createLocatorAI({...cfg, select: async () => index});
  assert.deepEqual(await ai.choose('send', [button, backward]), button);
  assert.equal(await ai.read('send'), null);
  assert.equal(await ai.commit('send', backward), false);
  assert.equal(await ai.commit('send', button), true);
  assert.deepEqual(await createLocatorAI(cfg).read('send'), button);
  for (const invalid of [0, -1, 40, '1', {...backward}, 1.5]) {
    index = invalid;
    assert.equal(await ai.choose('send', [button, backward]), null);
    assert.deepEqual(await ai.read('send'), button);
    assert.equal(await ai.commit('send', backward), false);
  }
  for (const change of [{build: '11'}, {appId: 'org.other'}, {platform: 'ios'}]) {
    assert.equal(await createLocatorAI({...cfg, ...change}).read('send'), null);
  }
  assert.equal(await ai.read('stop'), null);
  const [dir] = await readdir(cfg.cacheDir);
  const events = (await readFile(join(cfg.cacheDir, dir, 'send.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(events[0].candidates, [button, backward]);
  assert.ok(events.some(e => e.status === 'verified'));
  assert.ok(events.some(e => e.status === 'unresolved'));
});

test('send only structural terms; reject dynamic text, unrelated packages, arbitrary strategies and unsupported roles', async t => {
  const cfg = await environment(t);
  const requests = [];
  const ai = createLocatorAI({...cfg, select: async request => { requests.push(request); return 1; }});
  const entry = {using: 'id', value: 'org.example.mobile:id/privateAliceSendButton', text:'raw account message', id:'account_123'};
  const candidates = [entry, {using:'id',value:'com.other.app:id/secretSend'}, {using:'xpath', value:'//send'},
    {using:'accessibility id',value:'send to alice@example.org'}, {using:'accessibility id',value:'message from Alice'}];
  assert.deepEqual(await ai.choose('send', candidates), {using: entry.using, value: entry.value});
  assert.deepEqual(requests, [{role: 'send the composed chat message', candidates:[{index:1, using:'id',terms:['send','button']}]}]);
  for (const key of ['historyItem','question','answer','../../path']) {
    assert.equal(await ai.choose(key,candidates), null);
    assert.equal(await ai.commit(key,entry), false);
  }
  const ambiguous = [{using:'id',value:'aliceSendButton'}, {using:'id',value:'bobSendButton'}];
  assert.equal(await ai.choose('send',ambiguous), null);
  assert.equal(requests.length, 2);
});

test('the CLI runs without tools, MCP, hooks or project config in a disposable working directory', async t => {
  const cfg = await environment(t);
  const bin = join(cfg.cacheDir, 'test-claude');
  const argsFile = join(cfg.cacheDir, 'invocation.json');
  await writeFile(bin, '#!' + process.execPath + '\n' +
    'require("node:fs").writeFileSync(' + JSON.stringify(argsFile) + ',JSON.stringify({args:process.argv.slice(2),cwd:process.cwd()}));\nconsole.log("2");', {mode:0o700});
  const ai = createLocatorAI({...cfg, claudeBin: bin});
  assert.deepEqual(await ai.choose('back',[button,backward]), backward);
  const {args,cwd} = JSON.parse(await readFile(argsFile, 'utf8'));
  for (const [flag,value] of Object.entries({'--tools':'','--setting-sources':'user','--mcp-config':'{"mcpServers":{}}',
    '--settings':'{"disableAllHooks":true}','--permission-mode':'dontAsk'})) {
    assert.equal(args[args.indexOf(flag)+1],value,flag);
  }
  for (const flag of ['--safe-mode','--strict-mcp-config','--disable-slash-commands','--no-session-persistence']) assert.ok(args.includes(flag));
  assert.notEqual(cwd,process.cwd());
  await assert.rejects(stat(cwd), {code:'ENOENT'});
});

test('CLI timeout or error leaves no saved repair and reports a redacted diagnostic', async t => {
  const cfg = await environment(t);
  const bin = join(cfg.cacheDir, 'slow-claude');
  await writeFile(bin, '#!' + process.execPath + '\nsetTimeout(()=>{}, 10000);', {mode:0o700});
  const diagnostics = [];
  for (const [program, status] of [[bin,'model-timeout'],[bin + '-missing','model-unavailable']]) {
    const ai = createLocatorAI({...cfg, claudeBin:program, timeoutMs:100, onDiagnostic: value => diagnostics.push(value)});
    assert.equal(await ai.choose('send',[button]), null);
    assert.equal(await ai.read('send'), null);
    assert.deepEqual(diagnostics.at(-1), {key:'send', status});
  }
});
