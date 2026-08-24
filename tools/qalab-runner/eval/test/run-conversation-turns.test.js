// DesktopRunner.runConversationTurns「多轮同一对话」编排不变式自测(stub 浏览器原子操作,node 直跑)。
// 运行: node tools/qalab-runner/eval/test/run-conversation-turns.test.js
//
// 锁定本次修复的核心不变式:同一会话的多轮里,只有轮次0新建对话,后续轮【复用当前对话】(绝不再新建),
// 且按 0→1→2 顺序连发、逐轮回写;中间轮跳过开面板抓取、末轮抓全字段;首轮失败则后续轮跳过(不裸发)。
// 用 Object.create 绕过重构造器(不起真 DialogRunner/TaskWatcher/浏览器),stub 原子操作记录调用序列。

const assert = require('assert');
const DesktopRunner = require('../src/desktop-runner');

function makeRunner(calls, overrides = {}) {
  const r = Object.create(DesktopRunner.prototype);
  r.logger = null;
  r.execution = {};
  r._log = () => {};
  r._warn = () => {};
  r._focus = async () => { calls.push('focus'); };
  r._openCleanConversation = async () => { calls.push('openClean'); return true; };
  r._sendOne = async (tc) => { calls.push('send:' + tc.turnIndex); };
  r._extractCurrent = async (tc, opts) => { calls.push(`extract:${tc.turnIndex}:skip=${!!(opts || {}).skipPanels}`); return { answer: 'a' + tc.turnIndex }; };
  r._buildResult = (tc, out, meta) => ({
    caseId: tc.caseId, run_id: tc.run_id, turnIndex: tc.turnIndex,
    success: !meta.errorMsg, answer: (out && out.answer) || '', durationMs: 0, completeReason: meta.completeReason,
  });
  r.dr = { waitForResponseComplete: async () => { calls.push('wait'); return { completed: true, reason: 'footer' }; } };
  return Object.assign(r, overrides);
}

const T = (run_id, turnIndex) => ({ caseId: `RUN-${run_id}`, run_id, turnIndex, question: `q${turnIndex}` });

async function test_followups_reuse_same_conversation() {
  const calls = [];
  const r = makeRunner(calls);
  const reported = [];
  await r.runConversationTurns([T(1, 0), T(2, 1), T(3, 2)], async (res, tc) => reported.push(tc.run_id));
  // 【核心】openClean 只出现一次(仅轮次0新建对话);后续轮不新建
  const openN = calls.filter(c => c === 'openClean').length;
  assert.strictEqual(openN, 1, `openClean 应恰好 1 次(仅轮次0),得 ${openN}(calls=${calls.join(',')})`);
  // send 三次,顺序 0→1→2,且首个 openClean 之后不再出现 openClean
  assert.deepStrictEqual(calls.filter(c => c.startsWith('send:')), ['send:0', 'send:1', 'send:2'], '应按 0→1→2 顺序各发一次');
  assert.strictEqual(calls.indexOf('openClean'), calls.lastIndexOf('openClean'), 'send:1/send:2 之间不应再 openClean(不新建对话)');
  // 逐轮回写
  assert.deepStrictEqual(reported, [1, 2, 3], '三轮都应逐轮回写');
  // 中间轮跳过开面板,末轮抓全字段
  assert.ok(calls.includes('extract:0:skip=true') && calls.includes('extract:1:skip=true'), '中间轮应跳过开面板抓取');
  assert.ok(calls.includes('extract:2:skip=false'), '末轮应抓全字段');
}

async function test_turns_sorted_by_turn_index() {
  const calls = [];
  const r = makeRunner(calls);
  // 故意乱序传入(2,0,1)→ 应按 turnIndex 升序连发
  await r.runConversationTurns([T(30, 2), T(10, 0), T(20, 1)], async () => {});
  assert.deepStrictEqual(calls.filter(c => c.startsWith('send:')), ['send:0', 'send:1', 'send:2'], '乱序输入应先按 turnIndex 排序再连发');
}

async function test_single_turn_opens_one_and_extracts_full() {
  const calls = [];
  const r = makeRunner(calls);
  await r.runConversationTurns([T(9, 0)], async () => {});
  assert.strictEqual(calls.filter(c => c === 'openClean').length, 1, '单轮应开一个对话');
  assert.ok(calls.includes('extract:0:skip=false'), '单轮=末轮,应抓全字段');
}

async function test_turn0_failure_skips_followups() {
  const calls = [];
  const r = makeRunner(calls, { _openCleanConversation: async () => { calls.push('openClean'); throw new Error('boom'); } });
  const reported = [];
  await r.runConversationTurns([T(1, 0), T(2, 1)], async (res, tc) => reported.push({ id: tc.run_id, ok: res.success }));
  assert.strictEqual(calls.filter(c => c.startsWith('send')).length, 0, '首轮失败不应发送任何 query(对话未建立)');
  assert.deepStrictEqual(reported.map(x => x.id), [1, 2], '首轮失败时两轮都应回写(失败态)');
  assert.ok(reported.every(x => !x.ok), '首轮失败时两轮都应为失败');
}

async function test_middle_turn_failure_continues() {
  const calls = [];
  let n = 0;
  const r = makeRunner(calls, { _sendOne: async (tc) => { calls.push('send:' + tc.turnIndex); if (++n === 2) throw new Error('mid boom'); } });
  const reported = [];
  await r.runConversationTurns([T(1, 0), T(2, 1), T(3, 2)], async (res, tc) => reported.push({ id: tc.run_id, ok: res.success }));
  // 中间轮(turn1)失败不中止:turn2 仍尝试发送;三轮都回写
  assert.deepStrictEqual(reported.map(x => x.id), [1, 2, 3], '中间轮失败不应中止后续轮');
  assert.strictEqual(reported[0].ok, true, 'turn0 成功');
  assert.strictEqual(reported[1].ok, false, 'turn1 失败');
  assert.strictEqual(reported[2].ok, true, 'turn2 仍执行并成功');
}

async function main() {
  await test_followups_reuse_same_conversation();
  await test_turns_sorted_by_turn_index();
  await test_single_turn_opens_one_and_extracts_full();
  await test_turn0_failure_skips_followups();
  await test_middle_turn_failure_continues();
  console.log('OK run-conversation-turns');
}

main().catch(e => { console.error(e); process.exit(1); });
