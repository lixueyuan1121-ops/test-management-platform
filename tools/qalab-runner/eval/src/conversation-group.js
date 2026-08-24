// 平台模式:把 fetchPending 拉到的 pending 列表按「会话」分组。
//
// 同一 conversation_group 的多条 run 是「同一多轮会话的各轮」,必须归到一个会话里、按 turn_index 升序,
// 交给执行器在同一对话内顺序连发(轮次0新建对话、后续轮复用同一对话形成上下文)。单轮(无
// conversation_group 或空串)各自成一个只含一条的会话。返回「会话数组」,每个会话是「该会话各轮 item 的数组」。
// 会话在结果中的先后 = 其首个成员在 pending 里的首见顺序(组收拢到首见位置),便于按下发顺序执行。
function groupIntoConversations(pending) {
  const conversations = [];      // 结果:会话数组(每个会话=item 数组)
  const byGroup = new Map();     // conversation_group -> 该会话的 item 数组(用于往同组追加)
  for (const it of (pending || [])) {
    const p = it.payload || {};
    const g = p.conversation_group || null; // 空串/缺省 → null(单轮)
    if (!g) {
      conversations.push([it]);            // 单轮:自成一个会话,保持其位置
    } else if (byGroup.has(g)) {
      byGroup.get(g).push(it);             // 已见过该组:追加到同一会话
    } else {
      const conv = [it];
      byGroup.set(g, conv);
      conversations.push(conv);            // 组首见:在首见位置占一个会话槽
    }
  }
  // 组内按 turn_index 升序(下发/生成可能乱序;执行必须按轮次顺序)
  for (const conv of conversations) {
    conv.sort((a, b) => ((a.payload || {}).turn_index || 0) - ((b.payload || {}).turn_index || 0));
  }
  return conversations;
}

module.exports = { groupIntoConversations };
