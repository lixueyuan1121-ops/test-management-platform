import { executionContract, fingerprint } from './execution-evidence.mjs';

// Local verification and queue runs enter the same preparation/dispatch path.
// Strict replay never substitutes an agent run for an unsupported script.
export async function executeGui({ payload, reset, navigate, scriptRun, agentRun, log = () => {} }) {
  const execution = { mode: 'not_started', strict_replay: payload.strict_replay === true, reset: false, precondition: false, fallback_reason: null };
  const finish = result => ({ ...result, execution });
  const blocked = reason => finish({ verdict: 'fail', fail_kind: 'selector', reason, report: [] });
  const hasScript = Array.isArray(payload.script) && payload.script.length > 0;
  if (payload.strict_replay && !hasScript) return blocked('严格回归需要完整结构化脚本；未调用 Claude 兜底');
  if (hasScript && payload.execution_contract_sha256 && fingerprint(executionContract(payload)) !== payload.execution_contract_sha256)
    return blocked('下发脚本或选择器快照与执行指纹不一致，未执行操作');
  const gate = await reset();
  execution.reset = gate.reset === true;
  if (!gate.ok) return finish(gate.result);
  if (payload.strict_replay && !execution.reset) return blocked('严格回归必须启用每条用例执行前复位');
  if (hasScript && payload.precondition?.trim()) {
    execution.precondition = true;
    execution.mode = 'claude_precondition';
    const nav = await navigate(payload);
    execution.precondition_ok = nav.ok === true;
    log(`前置导航${nav.ok ? '到位' : '未确认到位'}:${nav.reason || ''}`);
    if (payload.strict_replay && !nav.ok) return blocked(`前置导航未确认到位：${nav.reason || ''}`);
  }
  if (hasScript) {
    execution.mode = execution.precondition ? 'claude_precondition' : (payload.script.some(s => s.action === 'judge') ? 'claude_judge' : 'script');
    const result = await scriptRun(payload.script);
    if (!result.needClaude) return finish(result);
    execution.fallback_reason = result.reason;
    if (payload.strict_replay) return blocked(`严格回归停止：${result.reason}；请更新 Runner 或修正脚本后重新验证`);
  }
  execution.mode = 'claude_fallback';
  return finish(await agentRun(payload));
}
