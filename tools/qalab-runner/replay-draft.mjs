import { normalizeScript } from './execution-evidence.mjs';

// setup_script is an authoring field only. Persist one ordinary DSL script so
// old runners execute exactly the same preparation + assertions, without AI.
export function compileReplayDraft(draft) {
  const c = { ...draft, script: normalizeScript(draft.script) };
  if (!c.script.some(s => s.action.startsWith('assert_'))) throw new Error('主测试脚本需要业务断言，前置到位断言不能代替功能验证');
  if (draft.setup_script !== undefined) {
    const setup = normalizeScript(draft.setup_script);
    if (!setup.some(s => s.action.startsWith('assert_'))) throw new Error('前置脚本需要至少一个到位断言');
    c.script = [...setup, ...c.script];
    if (c.precondition?.trim()) c.steps = `前置要求（已并入脚本）：${c.precondition.trim()}\n\n${c.steps}`;
    c.precondition = '';
    delete c.setup_script;
  }
  if (c.precondition?.trim()) throw new Error('纯脚本回归不能依赖文字前置导航；请提供 setup_script（含到位断言），或将前置操作写入完整 script 后清空 precondition');
  if (c.script.some(s => s.action === 'judge')) throw new Error('纯脚本回归不能包含 AI judge，请改为可验证的业务断言');
  if (c.script.length > 200) throw new Error('合并后的脚本不能超过 200 步');
  return c;
}
