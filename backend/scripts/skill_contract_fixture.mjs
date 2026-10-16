// Synthetic offline test fixture only. Never submit this output to a live platform.
import { readFileSync } from 'node:fs';
import { fingerprint } from '../../tools/skills/qalab-requirement-test/scripts/validate-evidence.mjs';
export function sealFixture(p) {
  p.requirement ||= 'OFFLINE TEST FIXTURE — DO NOT IMPORT';
  for(const c of p.cases){
    const script=c.script.map(s=>({action:s.action,target:s.target||{},args:s.args||{},desc:s.desc||''}));
    const reportHash=fingerprint(c.report);
    c.execution_evidence={version:'qalab-execution-v1',script_sha256:fingerprint(script),contract_sha256:fingerprint({script,precondition:c.precondition||'',selector_registry:c.selector_registry||{}}),report_sha256:reportHash,runtime_sha256:'a'.repeat(64),node_version:process.version,platform:process.platform,mode:'script',strict_replay:true,reset:true,precondition:false,repeat_count:2,repeat_report_sha256:[reportHash,reportHash]};
  }
  return p;
}
if(process.argv[1]?.endsWith('skill_contract_fixture.mjs'))console.log(JSON.stringify(sealFixture(JSON.parse(readFileSync(0,'utf8').replace(/^\uFEFF/,'')))));
