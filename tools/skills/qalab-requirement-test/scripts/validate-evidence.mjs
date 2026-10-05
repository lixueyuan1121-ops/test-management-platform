import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

// qalab-execution-v1; shared test vectors with the Runner and Python server.
function fingerprint(value) {
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

try {
  const payload = JSON.parse(readFileSync(0, 'utf8').replace(/^\uFEFF/, ''));
  for (const c of payload.cases || []) {
    const e = c.execution_evidence;
    if (!e) continue;
    const script = c.script.map(s => ({ action: String(s.action || '').trim(), target: s.target || {}, args: s.args || {}, desc: Array.from(String(s.desc || '')).slice(0,200).join('') }));
    const contract = { script, precondition: (c.precondition || '').trim(), selector_registry: c.selector_registry || {} };
    if (e.version !== 'qalab-execution-v1' || e.script_sha256 !== fingerprint(script) || e.contract_sha256 !== fingerprint(contract) || e.report_sha256 !== fingerprint(c.report))
      throw new Error(`${c.title}: 脚本、选择器快照或报告与实测证据不一致，请重跑最终脚本；不要手改 Runner 回填包`);
  }
  process.stdout.write('执行证据指纹一致\n');
} catch (error) { process.stderr.write(error.message + '\n'); process.exitCode = 1; }
