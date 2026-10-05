import { readFileSync, writeFileSync, existsSync, mkdirSync, mkdtempSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { executionContract, fingerprint, normalizeScript } from './execution-evidence.mjs';
import { compileReplayDraft } from './replay-draft.mjs';

export function saveLocalReport(report, directory) {
  return report.map((step, i) => {
    const { shotBuf, ...rest } = step;
    if (shotBuf?.length) {
      const name = `step-${i + 1}.png`;
      writeFileSync(join(directory, name), Buffer.from(shotBuf));
      rest.local_shot = name;
    }
    return rest;
  });
}

export async function verifyImport({ input, output, execute, fetchRegistry, acquireLease, close, log = () => {}, timeoutMs = 900000, workerFile, runWorker }) {
  if (!input || !output || input.startsWith('--') || output.startsWith('--')) throw new Error('用法：node runner.mjs --verify-import draft.json --output verified.json');
  if (resolve(input) === resolve(output) || existsSync(output)) throw new Error('输出文件已存在或与输入相同；请指定新的文件名');
  const draft = JSON.parse(readFileSync(input, 'utf8').replace(/^\uFEFF/, ''));
  if (!Number.isInteger(draft.project_id) || draft.project_id < 1 || !Number.isInteger(draft.runner_device_id) || draft.runner_device_id < 1) throw new Error('草稿需要真实 project_id 和 runner_device_id');
  if (!draft.requirement?.trim() || !Array.isArray(draft.cases) || !draft.cases.length || draft.cases.length > 20) throw new Error('草稿需要 requirement 和 1–20 条用例');
  draft.cases = draft.cases.map(compileReplayDraft);
  for (const c of draft.cases) {
    if (!['gui', 'e2e'].includes(c.exec_kind || 'e2e')) throw new Error('同路径验证入口当前支持 GUI/E2E；API 用例继续使用 API 执行器');
    for (const field of ['title', 'steps', 'expected', 'environment', 'scope']) if (!c[field]?.trim()) throw new Error(`用例缺少 ${field}`);
    c.script = normalizeScript(c.script);
    if (!c.script.some(s => s.action.startsWith('assert_'))) throw new Error('需要真实业务断言');
    for (const key of ['report', 'verdict', 'finished_at', 'duration_ms', 'execution_evidence', 'selector_registry', 'resolution']) delete c[key];
  }
  const release = await acquireLease();
  if (!release) throw new Error('桌面正在被另一个 Runner 使用；等待它结束后再验证，不抢占当前测试');
  try {
    const registry = await fetchRegistry(draft.project_id, draft.sub_product || '');
    mkdirSync(dirname(resolve(output)), { recursive: true });
    const directory = mkdtempSync(resolve(output) + '.evidence-');
    const cases = [];
    for (let i = 0; i < draft.cases.length; i++) {
      const c = draft.cases[i];
      const payload = { ...c, project_id: draft.project_id, sub_product: draft.sub_product || '', selector_registry: registry, strict_replay: true };
      payload.execution_contract_sha256 = fingerprint(executionContract(payload));
      const attempts = [];
      for (let repeat = 1; repeat <= 2; repeat++) {
        const localEvidenceDir = join(directory, `case-${i + 1}-run-${repeat}`);
        mkdirSync(localEvidenceDir);
        const item = { run_id: `local-${i + 1}-${repeat}`, kind: c.exec_kind || 'e2e', payload: structuredClone(payload), localEvidenceDir };
        log(`验证 ${c.title} (${repeat}/2)：同队列复位、脚本、断言和截图流程`);
        const result = runWorker ? await runWorker({ file: workerFile, item, heartbeat: async () => ({ alive: true }), log, timeoutMs }) : await execute(item, { localEvidenceDir });
        writeFileSync(join(localEvidenceDir, 'result.json'), JSON.stringify(result, null, 2));
        attempts.push(result);
        if (result.verdict !== 'pass' || !result.execution_evidence || result.report?.length !== c.script.length
            || result.report.some((r, n) => !r.ok || r.action !== c.script[n].action)
            || result.execution_evidence.contract_sha256 !== payload.execution_contract_sha256
            || (repeat === 2 && result.execution_evidence.runtime_sha256 !== attempts[0].execution_evidence.runtime_sha256))
          throw new Error(`${c.title} 第 ${repeat} 次未通过完整验证：${result.reason || '执行版本或报告不一致'}。证据：${localEvidenceDir}`);
      }
      const last = attempts[1];
      cases.push({ ...c, exec_kind: c.exec_kind || 'e2e', selector_registry: registry, verdict: 'pass', report: last.report,
        executor: 'QA Lab Runner / StepExecutor', finished_at: new Date().toISOString(), duration_ms: last.duration_ms,
        execution_evidence: { ...last.execution_evidence, repeat_count: 2, repeat_report_sha256: attempts.map(a => a.execution_evidence.report_sha256) } });
    }
    const packet = { ...draft, external_id: `runner-${randomUUID()}`, cases };
    writeFileSync(output, JSON.stringify(packet, null, 2), { flag: 'wx' });
    log(`完整验证通过，回填包：${resolve(output)}；截图和追踪：${directory}。尚未上传平台。`);
    return packet;
  } finally { try { await close?.(); } finally { await release(); } }
}
