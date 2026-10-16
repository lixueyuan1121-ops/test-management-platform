// A local, resumable ledger. Failed/blocked cases are never sent as successful imports.
import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { validatePayload } from './validate-contract.mjs';
import { fingerprint } from './validate-evidence.mjs';
const read = p => JSON.parse(readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const need = (v,m) => {if(!v) throw new Error(m);};
const now = () => new Date().toISOString();
const save = (path,m) => {const temp=path+'.tmp'; writeFileSync(temp,JSON.stringify(m,null,2));renameSync(temp,path);};
export function init(plan, path) {
  need(!existsSync(path),'清单已存在，继续使用原清单，不覆盖场景');
  const codex=plan.import_mode==='codex';
  need(plan.application && plan.requirement && Number.isInteger(plan.project_id) && plan.project_id > 0 && (codex?plan.runner_device_id==null:Number.isInteger(plan.runner_device_id) && plan.runner_device_id > 0),'计划缺应用/需求/真实项目；Codex 模式不需要设备ID');
  const u=new URL(plan.platform);
  need((u.protocol==='https:' || (u.protocol==='http:' && ['localhost','127.0.0.1','[::1]'].includes(u.hostname))) && u.pathname==='/' && !u.username && !u.password && !u.search && !u.hash,'platform 必须为平台源地址');
  need(Array.isArray(plan.cases) && plan.cases.length,'需要场景清单');
  const ids=new Set(), titles=new Set();
  const cases=plan.cases.map(c=>{
    need(typeof c.id==='string' && c.id.trim() && !ids.has(c.id) && typeof c.title==='string' && c.title.trim() && !titles.has(c.title),'场景id/title不能为空或重复');
    need(Array.isArray(c.acceptance) && c.acceptance.length && c.acceptance.every(x=>typeof x==='string' && x.trim()),`${c.id} 缺具体验收点`);
    ids.add(c.id);titles.add(c.title);
    return {id:c.id,title:c.title,acceptance:c.acceptance,status:'planned',history:[]};
  });
  const m={version:'qalab-delivery-v1',...(codex?{import_mode:'codex'}:{}),platform:u.origin,project_id:plan.project_id,runner_device_id:plan.runner_device_id,application:plan.application,requirement:plan.requirement,created_at:now(),cases};
  writeFileSync(path,JSON.stringify(m,null,2),{flag:'wx'});return m;
}
export function record(path, id, status, {packet,reason}={}) {
  const m=read(path), c=m.cases.find(c=>c.id===id);need(c,'场景不存在；不能隐式丢弃或替换原场景');
  need(['draft','verified','tested','failed','blocked'].includes(status),'状态只支持 draft/verified/tested/failed/blocked');
  need(c.status!=='submitted','已提交场景不可覆盖；新一轮执行请建立新批次');
  const update={status,updated_at:now()};
  if (['failed','blocked'].includes(status)) {need(typeof reason==='string' && reason.trim(),'失败/阻塞必须填写真实原因');update.reason=reason;}
  else {
    need(packet,'需要 --packet 文件');const p=read(packet);validatePayload(p,{draft:status==='draft'});
    need(p.project_id===m.project_id && (p.import_mode||'verified')===(m.import_mode||'verified') && (p.runner_device_id??null)===(m.runner_device_id??null),'包与清单项目/来源/设备不同');
    if(status!=='draft')need(status===(p.import_mode==='codex'?'tested':'verified'),'Codex 实测使用 tested；Runner 双次实测使用 verified');
    need(p.cases.some(x=>x.title===c.title),'包中没有对应标题的场景');
    update.packet=resolve(packet);update.packet_sha256=fingerprint(p);update.external_id=p.external_id || null;
    update.verdict=p.cases.find(x=>x.title===c.title).verdict || null;
  }
  c.history.push({...update});delete c.reason;delete c.packet;delete c.packet_sha256;delete c.external_id;Object.assign(c,update);save(path,m);return m;
}
export function ready(path, packet, platform) {
  const m=read(path),p=read(packet);validatePayload(p);
  need(!platform || new URL(platform).origin===m.platform,'回填平台与清单不一致');
  need(p.project_id===m.project_id && (p.import_mode||'verified')===(m.import_mode||'verified') && (p.runner_device_id??null)===(m.runner_device_id??null),'项目/来源/设备与清单不一致');
  need(m.cases.every(c=>['tested','verified','failed','blocked','submitted'].includes(c.status)),'仍有 planned/draft 场景；先完成实测或明确失败/阻塞原因，不能静默漏例');
  for(const c of p.cases){const row=m.cases.find(x=>x.title===c.title);need(row && [p.import_mode==='codex'?'tested':'verified','submitted'].includes(row.status) && row.packet_sha256===fingerprint(p),`${c.title} 尚未登记该原始实测包或包已改变`);}
  return {manifest:m,payload:p};
}
export function submitted(path, packet, receiptPath, platform) {
  const {manifest:m,payload:p}=ready(path,packet,platform),r=read(receiptPath);
  need(r.accepted===true && Number.isInteger(r.job_id) && r.job_id>0 && r.project_id===p.project_id && r.external_id===p.external_id && r.case_count===p.cases.length,'回执与原始包不一致');
  for(const c of m.cases.filter(c=>p.cases.some(p=>p.title===c.title))){
    if(c.status==='submitted') {need(c.job_id===r.job_id,'重复回执job不同');continue;}
    c.status='submitted';c.job_id=r.job_id;c.receipt=resolve(receiptPath);c.history.push({status:'submitted',job_id:r.job_id,updated_at:now()});
  }
  save(path,m);return m;
}
export function report(path) {
  const m=read(path),counts={};for(const c of m.cases)counts[c.status]=(counts[c.status]||0)+1;
  return {platform:m.platform,application:m.application,total:m.cases.length,counts,cases:m.cases.map(c=>({id:c.id,title:c.title,acceptance:c.acceptance,status:c.status,verdict:c.verdict,reason:c.reason,job_id:c.job_id})),note:'submitted 仅代表平台接收，不代表已入库或 Runner 回归。Codex 业务失败保留 fail 原样提交；不可执行/未实测/环境阻塞保留本地原因。'};
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  try {
    const [action,...args]=process.argv.slice(2),o={};
    for(let i=0;i<args.length;i+=2){need(args[i]?.startsWith('--') && args[i+1] && !args[i+1].startsWith('--'),'参数必须为 --name value');o[args[i].slice(2)]=args[i+1];}
    need(o.manifest,'需要 --manifest 文件');let result;
    if(action==='init')result=init(read(o.plan),o.manifest);
    else if(action==='record')result=record(o.manifest,o.id,o.status,o);
    else if(action==='ready'){ready(o.manifest,o.packet,o.platform);result={ready:true};}
    else if(action==='submitted')result=submitted(o.manifest,o.packet,o.receipt,o.platform);
    else if(action==='report')result=report(o.manifest);
    else throw new Error('使用 init/record/ready/submitted/report');
    console.log(JSON.stringify(result,null,2));
  } catch(e){console.error(e.message);process.exitCode=1;}
}
