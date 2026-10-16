import { validateNetworkScript } from './network-contract.mjs';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// qalab-execution-v1; shared test vectors with the Runner and Python server.
export function fingerprint(value) {
  const hash = createHash('sha256');
  const visit = v => {
    if (v === null) return hash.update('n');
    if (typeof v === 'boolean') return hash.update(v ? 't' : 'f');
    if (typeof v === 'number') { if (!Number.isFinite(v)) throw new Error('非有限数字'); const b = Buffer.alloc(8); b.writeDoubleBE(Object.is(v, -0) ? 0 : v); return hash.update('d').update(b.toString('hex')); }
    if (typeof v === 'string') return hash.update(`s${Buffer.byteLength(v)}:`).update(v);
    if (Array.isArray(v)) { hash.update('['); v.forEach(visit); return hash.update(']'); }
    if (v && typeof v === 'object') { hash.update('{'); Object.keys(v).sort((a,b) => Buffer.compare(Buffer.from(a), Buffer.from(b))).forEach(k => { visit(k); visit(v[k]); }); return hash.update('}'); }
    throw new Error('执行证据必须为 JSON');
  };
  visit(value); return hash.digest('hex');
}

export function validateEvidence(c, {strict = false} = {}) {
    const e = c.execution_evidence;
    if (strict && (!e || e.repeat_count !== 2 || e.strict_replay !== true || e.mode !== 'script' || e.reset !== true || e.precondition !== false || e.fallback_reason))
      throw new Error(`${c.title}: 需要 Runner --verify-import 的两次完整严格实测证据`);
    if (!e) return;
    const hash = v => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
    if (!hash(e.runtime_sha256) || !e.node_version || !e.platform)
      throw new Error(`${c.title}: 执行器版本证据不完整`);
    if (strict && (!Array.isArray(e.repeat_report_sha256) || e.repeat_report_sha256.length !== 2 || !e.repeat_report_sha256.every(hash) || e.repeat_report_sha256[1] !== e.report_sha256))
      throw new Error(`${c.title}: 缺少两次报告指纹或最后一次指纹不一致`);
    const script = c.script.map(s => ({ action: String(s.action || '').trim(), target: s.target || {}, args: s.args || {}, desc: Array.from(String(s.desc || '')).slice(0,200).join('') }));
    const contract = { script, precondition: (c.precondition || '').trim(), selector_registry: c.selector_registry || {} };
    if (e.version !== 'qalab-execution-v1' || e.script_sha256 !== fingerprint(script) || e.contract_sha256 !== fingerprint(contract) || e.report_sha256 !== fingerprint(c.report))
      throw new Error(`${c.title}: 脚本、选择器快照或报告与实测证据不一致，请重跑最终脚本；不要手改 Runner 回填包`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) try {
  const payload = JSON.parse(readFileSync(0, 'utf8').replace(/^\uFEFF/, ''));
  for (const c of payload.cases || []) {
    if (['gui', 'e2e'].includes(c.exec_kind || 'e2e')) validateNetworkScript(c.script || []);
    validateEvidence(c, {strict:['gui','e2e'].includes(c.exec_kind || 'e2e')});
  }
  process.stdout.write('执行证据指纹一致\n');
} catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
