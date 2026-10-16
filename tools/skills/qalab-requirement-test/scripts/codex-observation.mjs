import { fingerprint } from './validate-evidence.mjs';
const need=(v,m)=>{if(!v)throw new Error(m);};
const text=v=>typeof v==='string' && !!v.trim();
export const normalizeScript=script=>script.map(s=>({action:String(s.action||'').trim(),target:s.target||{},args:s.args||{},desc:Array.from(String(s.desc||'')).slice(0,200).join('')}));

export function validateCodexObservation(c) {
  need(['pass','fail'].includes(c.verdict) && c.executor==='codex','Codex 实测须明确 pass/fail 及 executor=codex');
  need(!c.execution_evidence && !c.selector_registry,'Codex 实测不能冒充 Runner 验证或绑定本机注册表');
  need(['steps','expected','environment','scope'].every(k=>text(c[k])),`${c.title} 缺完整步骤、预期、环境或验证范围`);
  need(text(c.finished_at) && /(?:Z|[+-]\d\d:\d\d)$/.test(c.finished_at) && Number.isFinite(Date.parse(c.finished_at)) && Date.parse(c.finished_at)<=Date.now()+300000,'finished_at 必须包含时区且不是未来执行');
  need(Number.isInteger(c.duration_ms) && c.duration_ms>=0 && c.duration_ms<=2147483647,'duration_ms 无效');
  const e=c.codex_evidence;
  need(e?.version==='qalab-codex-v1' && text(e.tool) && Array.isArray(e.artifacts) && e.artifacts.length>0 && e.artifacts.length<=200,'缺 Codex 工具和原始证据引用');
  const refs=new Set();
  for(const a of e.artifacts){need(text(a.id) && !refs.has(a.id) && ['log','trace','screenshot'].includes(a.kind) && text(a.reference),'证据引用须有唯一 id、kind 和 reference');refs.add(a.id);}
  need(e.script_sha256===fingerprint(normalizeScript(c.script)) && e.report_sha256===fingerprint(c.report),'脚本或报告与 Codex 实测包不一致；请核对真实操作并重验改动后的流程');
  need(Array.isArray(c.report) && c.report.length>0 && c.report.length<=c.script.length && (c.verdict!=='pass' || c.report.length===c.script.length),'通过须覆盖全部步骤；失败只记录执行到失败处的前缀');
  const captures=new Map();
  c.report.forEach((r,i)=>{
    const s=c.script[i], a=s.args||{}, failed=c.verdict==='fail' && i===c.report.length-1, label=`${c.title} 第${i+1}步`;
    need(r.no===i+1 && r.action===s.action && r.ok===!failed,`${label} 报告须按脚本顺序对应，失败只能在报告最后一步`);
    need(text(r.observation) && refs.has(r.evidence_ref),`${label} 缺真实观察或证据引用`);
    if(failed)need(s.action.startsWith('assert') && r.fail_kind==='business' && text(r.error),`${label} 仅导入有断言证据的业务失败，连接/定位阻塞不得冒充业务失败`);
    if(s.action==='get_text' && a.save_as){need(typeof r.text==='string',`${label} 缺实际捕获值`);captures.set(a.save_as,r.text);}
    if(!s.action.startsWith('assert'))return;
    const k=r.check;
    need(k && Object.hasOwn(k,'actual') && Object.hasOwn(k,'expected') && k.pass===!failed,`${label} 缺 actual/expected 或 pass 与结果矛盾`);
    let matched=k.pass;
    if(s.action==='assert_text'){
      const wanted=Object.hasOwn(a,'expected_from')?captures.get(a.expected_from):a.expected;
      need(typeof k.actual==='string' && typeof k.expected==='string' && k.expected===wanted,`${label} 预期须对应脚本或实际捕获值`);
      matched=a.contains?k.actual.includes(k.expected):k.actual===k.expected;
      if(a.negate)matched=!matched;
    }else if(['assert_visible','assert_absent'].includes(s.action)){
      need(typeof k.actual==='boolean' && k.expected===(s.action==='assert_visible'),`${label} 可见性实际值/预期无效`);
      matched=k.actual===k.expected;
    }else if(['assert_network_count','assert_fault_hits'].includes(s.action)){
      need(Number.isInteger(k.actual) && k.actual>=0 && k.expected===a.expected,`${label} 计数实际值/预期无效`);
      matched=k.actual===k.expected;
    }
    need(matched===k.pass,`${label} 实际值与通过/失败结论矛盾`);
  });
}
