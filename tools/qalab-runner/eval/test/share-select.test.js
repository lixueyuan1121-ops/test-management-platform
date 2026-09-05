// 分享面板「点全选直到全部勾选」控制流自测(无框架,node 直跑,失败退非0)。
// 运行: node tools/qalab-runner/eval/test/share-select.test.js
//
// 背景:抓会话分享链接前须勾选全部内容再「生成链接」。多轮对话一次「全选」常只到 indeterminate/
// 部分选中,需再点一次才全勾(用户明确流程)。故循环点全选直到 isAllChecked() 为真或达上限,
// 自适应单轮(点1次)/多轮(点2次)。纯控制流,不依赖 Playwright。

const assert = require('assert');
const { ensureAllSelected } = require('../src/share-select');

async function run(checkSeq, maxClicks = 3) {
  let i = 0, clicks = 0;
  const returned = await ensureAllSelected({
    isAllChecked: async () => checkSeq[Math.min(i++, checkSeq.length - 1)],
    clickSelectAll: async () => { clicks++; },
    sleep: async () => {},
    maxClicks,
  });
  return { returned, clicks };
}

async function test_already_all_checked_no_click() {
  const { clicks } = await run([true]);
  assert.strictEqual(clicks, 0, '已全勾不应点全选');
  console.log('✓ 已全勾:不点');
}

async function test_single_turn_one_click() {
  const { clicks } = await run([false, true]);
  assert.strictEqual(clicks, 1, '单轮点 1 次全选即全勾');
  console.log('✓ 单轮:点 1 次全选');
}

async function test_multi_turn_two_clicks() {
  const { clicks } = await run([false, false, true]);
  assert.strictEqual(clicks, 2, '多轮一次全选到部分,需再点一次(共 2 次)');
  console.log('✓ 多轮:点 2 次全选');
}

async function test_never_checked_stops_at_max() {
  const { clicks } = await run([false, false, false, false, false], 3);
  assert.strictEqual(clicks, 3, '始终未全勾:点到上限即停,交调用方逐个补勾兜底');
  console.log('✓ 始终未全勾:点到上限停');
}

async function main() {
  await test_already_all_checked_no_click();
  await test_single_turn_one_click();
  await test_multi_turn_two_clicks();
  await test_never_checked_stops_at_max();
  console.log('\n✅ 分享全选控制流 全部通过');
}
main().catch((e) => { console.error('✗ 测试失败:', e.message); process.exit(1); });
