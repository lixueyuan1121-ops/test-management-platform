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
    const g = p.conversation_group ? JSON.stringify([
      it.project_id || null, it.batch_id || null, it.target_engine || null,
      it.target_device || null, p.compare_group || null, p.conversation_group,
    ]) : null;
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
  // 兼容旧队列：同名且全部 turn_index=0 不能证明是多轮，各自新建对话。
  return conversations.flatMap(conv => conv.every(it => Number((it.payload || {}).turn_index || 0) === 0)
    ? conv.map(it => [it]) : [conv]);
}

// 判定一个会话(conv=该会话各轮 item 数组)是否含带附件轮次。
// 任一轮 payload.attachments 是非空数组即 true。用于止血:真支持做好前,带附件会话不裸跑(fail-closed)。
// 全程容错(null/缺字段/非数组均判 false 不抛错),因输入来自平台下发数据。
function convHasAttachments(conv) {
  return (conv || []).some((it) => {
    const a = (it && it.payload || {}).attachments;
    return Array.isArray(a) && a.length > 0;
  });
}

module.exports = { groupIntoConversations, convHasAttachments };
