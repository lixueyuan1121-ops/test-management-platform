// 平台模式「把 pending 列表按会话分组」纯函数自测(无测试框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/conversation-group.test.js
//
// 背景:平台模式拉到的 pending 各 run 里,同一 conversation_group 的多条是「同一多轮会话的各轮」,
// 必须归到同一个「会话」里、按 turn_index 升序,交给执行器在同一对话内顺序连发(轮次0新建对话、
// 后续轮复用),而非各自新建对话独立跑。单轮(无 conversation_group)各自成一个只含一条的会话。

const assert = require('assert');
const { groupIntoConversations } = require('../src/conversation-group');

function item(run_id, group, turn, device) {
  return { run_id, target_device: device || null,
           payload: { conversation_group: group, turn_index: turn, prompt: `q${run_id}` } };
}

function test_multiturn_grouped_and_sorted() {
  // 同组三轮,故意乱序传入(turn 2,0,1)→ 应归一组、按 turn_index 升序
  const pending = [item(12, 'A', 2), item(10, 'A', 0), item(11, 'A', 1)];
  const convs = groupIntoConversations(pending);
  assert.strictEqual(convs.length, 1, '同 group 三轮应合成 1 个会话');
  assert.deepStrictEqual(convs[0].map(x => x.run_id), [10, 11, 12], '组内应按 turn_index 升序');
}

function test_singletons_each_own_conversation() {
  const pending = [item(1, null, 0), item(2, null, 0)];
  const convs = groupIntoConversations(pending);
  assert.strictEqual(convs.length, 2, '两条单轮应是两个独立会话');
  assert.deepStrictEqual(convs.map(c => c.length), [1, 1]);
}

function test_mixed_preserves_first_seen_order() {
  // 顺序:A#0, 单4, A#1, B#0, 单7 → 会话序应为 [A(首见位), 单4, B, 单7],A 收拢到首见位置
  const pending = [item(1, 'A', 0), item(4, null, 0), item(2, 'A', 1), item(6, 'B', 0), item(7, null, 0)];
  const convs = groupIntoConversations(pending);
  assert.strictEqual(convs.length, 4, `应有 4 个会话(A、单4、B、单7),得 ${convs.length}`);
  assert.deepStrictEqual(convs[0].map(x => x.run_id), [1, 2], '会话A 应含其两轮且排最前(首见位)');
  assert.deepStrictEqual(convs[1].map(x => x.run_id), [4], '单4 保持其位置');
  assert.deepStrictEqual(convs[2].map(x => x.run_id), [6], '会话B 一轮');
  assert.deepStrictEqual(convs[3].map(x => x.run_id), [7], '单7 保持其位置');
}

function test_empty_group_treated_as_singleton() {
  // conversation_group 为空串应视为单轮(不与其他空串串成一组)
  const pending = [item(1, '', 0), item(2, '', 0)];
  const convs = groupIntoConversations(pending);
  assert.strictEqual(convs.length, 2, '空串 group 应各自单轮,不串成一组');
}

function main() {
  test_multiturn_grouped_and_sorted();
  test_singletons_each_own_conversation();
  test_mixed_preserves_first_seen_order();
  test_empty_group_treated_as_singleton();
  console.log('OK conversation-group');
}

main();
