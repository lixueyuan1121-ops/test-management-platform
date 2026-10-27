import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { validatePayload, validateReadableSteps } from '../../tools/skills/qalab-requirement-test/scripts/validate-contract.mjs';
import { fingerprint } from '../../tools/skills/qalab-requirement-test/scripts/validate-evidence.mjs';
import { normalizeScript } from '../../tools/skills/qalab-requirement-test/scripts/codex-observation.mjs';
const root=new URL('../../',import.meta.url);
const generated=spawnSync(process.env.QALAB_TEST_PYTHON || 'python',['-X','utf8','-m','scripts.test_script_flow','--dump'],{cwd:fileURLToPath(new URL('backend/',root)),encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
const packet=JSON.parse(generated.stdout);
const reseal=p=>{for(const c of p.cases){c.codex_evidence.script_sha256=fingerprint(normalizeScript(c.script));c.codex_evidence.report_sha256=fingerprint(c.report)}return p};
test('Python-sealed flow payload accepted unchanged by Windows/macOS offline JS contract',()=>validatePayload(packet));
test('Codex import rejects unreadable UI steps even when script and evidence are valid',()=>{
  for(const steps of ['1. flow_start\n2. connect\n3. click','1. 点击','1. 打开文件页\n2.','第1步：press','- assert_visible','1. 点击「文件」\n2. 待补充','']){
    const p=structuredClone(packet);p.cases[0].steps=steps;
    assert.throws(()=>validatePayload(p),/步骤/);
  }
});
test('business steps may group DSL operations and retain execution evidence unchanged',()=>{
  const p=structuredClone(packet),before=structuredClone(p.cases[0]);
  p.cases[0].steps='1. 复用已完成回答；不存在时创建测试会话。\n2. 给该回答点赞并检查选中态。';
  validatePayload(p);
  for(const field of ['script','report','codex_evidence'])assert.deepEqual(p.cases[0][field],before[field]);
  for(const steps of ['Click the Share button and verify the copied link.','1. 在命令输入框输入 click，检查提示文本。'])validateReadableSteps(steps);
});
test('standalone contracts stay identical to execution/server contracts',()=>{
  for(const [a,b] of [
    ['tools/qalab-runner/script-flow.mjs','tools/skills/qalab-requirement-test/scripts/script-flow.mjs'],
    ['backend/app/services/script_flow.py','tools/skills/qalab-requirement-test/scripts/script_flow.py'],
  ])assert.equal(readFileSync(new URL(a,root),'utf8'),readFileSync(new URL(b,root),'utf8'));
});
for(const [label,mutate] of [
  ['forged skipped assertion',c=>c.report[4].skipped=true],
  ['missing capture',c=>delete c.report[3].value],
  ['unsupported selector interpolation',c=>c.script[4].target.selector={$var:'id'}],
  ['guard tampering',c=>c.script[2].when.equals=true],
  ['cleanup failure',c=>c.report[5].ok=false],
  ['target depends on skipped capture',c=>{
    c.script[3].when={var:'found',equals:false};
    delete c.report[3].value;
    Object.assign(c.report[3],{skipped:true,skip_reason:'condition_false'});
  }],
])test('rejects '+label,()=>{const p=structuredClone(packet);mutate(p.cases[0]);assert.throws(()=>validatePayload(reseal(p)))});
