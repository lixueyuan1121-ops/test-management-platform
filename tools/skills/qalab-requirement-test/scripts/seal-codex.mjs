// Seals existing observations only; does not execute, infer results or generate evidence.
import {readFileSync,writeFileSync} from 'node:fs';
import {validatePayload} from './validate-contract.mjs';
import {fingerprint} from './validate-evidence.mjs';
import {normalizeScript} from './codex-observation.mjs';
try{
 const args=process.argv.slice(2), input=args[args.indexOf('--input')+1],output=args[args.indexOf('--output')+1];
 if(!args.includes('--input')||!args.includes('--output')||!input||!output)throw Error('使用 --input observed.json --output import.json');
 const p=JSON.parse(readFileSync(input,'utf8').replace(/^\uFEFF/,''));
 if(p.import_mode!=='codex')throw Error('只封存 import_mode=codex 的真实观察记录');
 for(const c of p.cases||[]){
   if(!c.codex_evidence || !Array.isArray(c.report))throw Error('缺原始观察或证据，不能封存');
   if(c.codex_evidence.script_sha256 || c.codex_evidence.report_sha256)throw Error('已封存包不可重新盖章；流程改变后重新实测并保存新的观察记录');
   Object.assign(c.codex_evidence,{script_sha256:fingerprint(normalizeScript(c.script)),report_sha256:fingerprint(c.report)});
 }
 validatePayload(p);
 writeFileSync(output,JSON.stringify(p,null,2),{flag:'wx'});
 console.log('已封存 Codex 实测包；未执行应用、未上传，也不代表平台 Runner 已回归。');
}catch(e){console.error(e.message);process.exitCode=1;}
