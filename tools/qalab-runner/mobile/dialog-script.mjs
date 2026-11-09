export const dialogScenarios = ['send', 'followup', 'stop', 'history'];

export const descriptions = {
  new_chat:'打开新任务输入页', compose:'输入测试文本', assert_draft:'逐字核对草稿',
  clear_draft:'清空本次草稿', assert_send_unavailable:'核对发送不可用',
  send:'提交当前草稿', assert_user_message:'核对指定轮次的用户消息', wait_reply:'核对本轮完整回复',
  assert_reply_contains:'核对回复内容', stop_generation:'停止本轮生成并核对输入区恢复',
  open_history:'从历史重新打开本次会话', assert_history_restored:'核对恢复后的消息与回复',
};

export function templateSteps(scenario) {
  if (!dialogScenarios.includes(scenario)) throw new Error('未知对话场景');
  const long = scenario === 'stop';
  const steps = [{action:'new_chat'},
    {action:'compose', text:long ? '{{marker}} 请逐条列出一百种水果的名称和英文拼写。' : '{{marker}} 请只回复“测试完成”。'},
    {action:'send'}];
  if (long) steps.push({action:'stop_generation'});
  steps.push({action:'assert_user_message',turn:1});
  if (!long) steps.push({action:'wait_reply'});
  if (scenario === 'followup' || long) {
    steps.push({action:'compose',text:'{{marker}}-2 请只回复“第二轮完成”。'},
      {action:'send'},{action:'assert_user_message',turn:2},{action:'wait_reply'});
    if (!long) steps.push({action:'assert_user_message',turn:1});
  }
  if (scenario === 'history') steps.push({action:'open_history'},{action:'assert_history_restored'});
  return steps;
}

// Also validate local direct executions, before locating or operating any control.
export function validateSteps(steps) {
  if (!Array.isArray(steps) || steps.length < 2 || steps.length > 40 || steps[0]?.action !== 'new_chat')
    throw new Error('真机脚本必须以 new_chat 开始，长度为 2–40 步');
  let draft = '', turn = 0, generating = false, reply = false, history = false, first = '';
  for (const [index, step] of steps.entries()) {
    const action = step?.action;
    if (typeof action !== 'string' || !Object.hasOwn(descriptions, action)) throw new Error('不支持的对话动作');
    const fields = ['action'];
    if (['compose','assert_draft','assert_reply_contains'].includes(action)) {
      fields.push('text');
      if (typeof step.text !== 'string' || [...step.text].length > 1000 ||
          /[\u0000-\u0008\u000b\u000c\u000e-\u001f\ud800-\udfff]/u.test(step.text) ||
          (action === 'compose' && step.text.length === 0) || (action === 'assert_reply_contains' && !step.text.trim())) {
        throw new Error('对话步骤文本无效：' + action);
      }
    }
    if (action === 'assert_user_message') fields.push('turn');
    if (Object.keys(step).some(k => !fields.includes(k))) throw new Error('对话步骤包含未知字段');
    if (history && action !== 'assert_history_restored') throw new Error('重开历史后需要核对历史');
    const invalid = () => {throw new Error('对话步骤顺序无效：' + action);};
    if (generating && !['assert_user_message', 'wait_reply', 'stop_generation'].includes(action)) invalid();
    switch (action) {
      case 'new_chat': if (index !== 0) invalid(); break;
      case 'compose': if (draft) invalid(); draft=step.text; break;
      case 'assert_draft': if (step.text !== draft) invalid(); break;
      case 'clear_draft': if (!draft) invalid(); draft=''; break;
      case 'assert_send_unavailable': if (draft.trim()) invalid(); break;
      case 'send':
        if (!draft?.trim() || generating || turn >= 5) invalid();
        if (!turn) first=draft;
        turn++; draft=''; generating=true; reply=false; break;
      case 'wait_reply': case 'stop_generation':
        if (!generating) invalid();
        generating=false; reply=action==='wait_reply'; break;
      case 'assert_user_message':
        if (!Number.isInteger(step.turn) || step.turn < 1 || step.turn > turn) invalid();
        break;
      case 'assert_reply_contains': if (!reply) invalid(); break;
      case 'open_history': if (!reply || draft || !first.includes('{{marker}}')) invalid(); history=true; break;
      case 'assert_history_restored': if (!history) invalid(); history=false; break;
    }
  }
  const last = steps.at(-1).action;
  if (!last.startsWith('assert_') && !['wait_reply','stop_generation'].includes(last)) throw new Error('脚本缺少最终断言');
  if (generating) throw new Error('脚本必须等待或停止最后一轮回复');
}
