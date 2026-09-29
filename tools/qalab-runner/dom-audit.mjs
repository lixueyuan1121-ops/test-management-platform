export function auditTarget(candidate) {
  if (!candidate || !['testid','css','xpath'].includes(candidate.by) || typeof candidate.value !== 'string') throw Error('无效的巡检导航定位');
  return {selector:candidate.by==='testid' ? `[data-testid=${JSON.stringify(candidate.value)}]` : candidate.by==='xpath' ? `xpath=${candidate.value}` : candidate.value,
    ...(candidate.has_text ? {has_text:candidate.has_text} : {}), ...(Number.isInteger(candidate.nth) ? {nth:candidate.nth} : {})};
}
// Traverse declared navigation only; no generic click crawler or business submissions.
export async function runDomAudit(core, params, heartbeat = async () => ({})) {
  if (params.version !== 1 || !Array.isArray(params.pages) || !params.pages.length) throw Error('不支持的 DOM 巡检配置');
  const results=[];
  let cancelled=false, heartbeatError=null;
  async function pulse() {
    try { const r=await heartbeat(); if (r?.cancel_requested) cancelled=true; }
    catch(e) { heartbeatError=e; }
  }
  const timer=setInterval(pulse,10000);
  const check=()=>{if(cancelled)throw Error('用户已终止巡检');if(heartbeatError)throw Error('巡检心跳失败：'+heartbeatError.message)};
  try {
    for(const spec of params.pages.slice(0,80)) {
      await pulse(); check();
      const result={id:spec.id,label:spec.label,ready:false,complete:false,elements:[]};
      try {
        await core.pressEscapePage();
        for(const selector of spec.nav) {
          check();
          if(selector.if_visible && !(await core.assertVisible({...auditTarget(selector),timeout_ms:500})).pass)continue;
          if(selector.action && selector.action!=='hover') throw Error('不支持的巡检导航动作');
          await core[selector.action==='hover'?'hover':'click']({...auditTarget(selector),timeout_ms:10000});
        }
        await core.waitFor({...auditTarget(spec.ready),timeout_ms:8000});
        await new Promise(r=>setTimeout(r,700)); check();
        const out=await core.probe({audit:true,auditRoot:spec.root||"",limit:2000,screenshot:false});
        result.ready=true;
        result.frameAliases=out.frameAliases||{};
        result.complete=out.groups.length>0 && out.groups.every(g=>!g.error && (g.total??g.elements.length)<=g.elements.length);
        result.elements=out.groups.flatMap(g=>(g.elements||[]).map(e=>({frame:g.frameMatch||g.frame,tag:e.tag,control_kind:e.control_kind,text:e.accessibleName||e.tooltipText||e.text,verified:e.verified||[],collections:e.collections||[],member_xpath:e.collections?.length ? e.uniqueXPath : undefined,observed:(e.candidates||[]).filter(c=>['testid','css'].includes(c.by))})));
        result.count=result.elements.length;
        if(!result.complete)result.error='部分 frame 未读取或元素被截断，本页不判废弃';
      }catch(e){check();result.error=e.message.slice(0,400)}
      await core.pressEscapePage();
      // Explicit close controls only, including when navigation/readiness failed.
      for (const close of spec.close || []) {
        try { if ((await core.assertVisible({...auditTarget(close),timeout_ms:500})).pass) await core.click({...auditTarget(close),timeout_ms:5000}); }
        catch(e) { result.complete=false;result.error='关闭当前面板失败：'+e.message.slice(0,200); }
      }
      await core.pressEscapePage();
      console.info(`[dom-audit] ${spec.id} ready=${result.ready} complete=${result.complete} elements=${result.count||0}${result.error ? ' '+result.error : ''}`);
      results.push(result);
    }
    check();
    if(!results.some(r=>r.ready))throw Error('巡检页面未到达：'+results.slice(0,3).map(r=>r.label+'：'+r.error).join('；').slice(0,450));
    return {audit_version:1,pages:results};
  }finally{clearInterval(timer);}
}
