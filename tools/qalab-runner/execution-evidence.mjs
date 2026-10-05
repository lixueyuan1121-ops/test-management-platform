import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';

// Versioned JSON fingerprint: byte-length strings, IEEE-754 numbers and UTF-8
// sorted object keys. Python and JS agree even for 1.0, exponents and emoji.
export function fingerprint(value) {
  const hash = createHash('sha256');
  const visit = v => {
    if (v === null) return hash.update('n');
    if (typeof v === 'boolean') return hash.update(v ? 't' : 'f');
    if (typeof v === 'number') {
      if (!Number.isFinite(v)) throw new Error('非有限数字不能进入执行证据');
      const b = Buffer.alloc(8); b.writeDoubleBE(Object.is(v, -0) ? 0 : v);
      return hash.update('d').update(b.toString('hex'));
    }
    if (typeof v === 'string') return hash.update(`s${Buffer.byteLength(v)}:`).update(v);
    if (Array.isArray(v)) { hash.update('['); v.forEach(visit); return hash.update(']'); }
    if (v && typeof v === 'object') {
      hash.update('{');
      Object.keys(v).sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b))).forEach(k => { visit(k); visit(v[k]); });
      return hash.update('}');
    }
    throw new Error('执行证据必须是 JSON 数据');
  };
  visit(value); return hash.digest('hex');
}

export function normalizeScript(script) {
  if (!Array.isArray(script) || !script.length) throw new Error('需要非空平台 script 数组');
  return script.map(s => ({ action: String(s.action || '').trim(), target: s.target || {}, args: s.args || {}, desc: Array.from(String(s.desc || '')).slice(0, 200).join('') }));
}

export function executionContract(payload) {
  return { script: normalizeScript(payload.script), precondition: (payload.precondition || '').trim(), selector_registry: payload.selector_registry || {} };
}

// Content version, not git HEAD: distributed runners and uncommitted checkouts
// must also identify the actual executable files. Private .env is never read.
export function runtimeFingerprint(root = new URL('./', import.meta.url)) {
  const files = {};
  for (const folder of ['', 'gui-mcp/']) for (const file of readdirSync(new URL(folder || './', root))) {
    if (!/\.(mjs|cjs)$/.test(file) || file.endsWith('.test.mjs') || file === 'playwright-runtime.mjs') continue;
    files[folder + file] = createHash('sha256').update(readFileSync(new URL(folder + file, root))).digest('hex');
  }
  let runtime;
  try { runtime = readFileSync(new URL('gui-mcp/playwright-runtime.mjs', root)); }
  catch { runtime = readFileSync(new URL('../../backend/app/services/playwright_runtime.mjs', root)); }
  files['gui-mcp/playwright-runtime.mjs'] = createHash('sha256').update(runtime).digest('hex');
  return fingerprint(files);
}

export function makeEvidence(payload, report, runtime, execution) {
  const contract = executionContract(payload);
  return { version: 'qalab-execution-v1', script_sha256: fingerprint(contract.script), contract_sha256: fingerprint(contract), report_sha256: fingerprint(report), runtime_sha256: runtime, node_version: process.version, platform: process.platform, ...execution };
}
