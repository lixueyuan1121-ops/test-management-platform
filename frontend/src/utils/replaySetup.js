// The server validates the merged DSL. Keep the original requirement readable,
// while clearing the AI navigation field only after explicit author input.
export function replaySetupUpdate(row, script, setup) {
  if (!script.some(s => String(s.action || '').startsWith('assert_'))) throw new Error('主测试脚本需要业务断言，前置到位断言不能代替功能验证')
  if (!Array.isArray(setup) || !setup.length || !setup.some(s => String(s.action || '').startsWith('assert_')))
    throw new Error('前置步骤必须是非空数组，并包含确认起始页面就绪的断言')
  if ([...setup, ...script].some(s => s.action === 'judge')) throw new Error('纯脚本回归请使用明确的业务断言，移除 AI judge')
  if (setup.length + script.length > 200) throw new Error('合并后的脚本不能超过 200 步')
  return { script: [...setup, ...script], precondition: '', steps: `前置要求（已并入脚本）：${row.precondition}\n\n${row.steps || ''}` }
}
