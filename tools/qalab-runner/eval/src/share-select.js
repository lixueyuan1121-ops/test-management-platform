// 反复点分享面板「全选」直到全部内容勾选(isAllChecked 为真)或达上限,返回实际点击次数。
//
// 为什么循环:多轮对话一次「全选」常只到 indeterminate/部分选中,需再点一次才全勾(用户明确流程)。
// 循环点到全勾,自适应单轮(点 1 次)/多轮(点 2 次);达上限仍未全勾则返回,交调用方逐个补勾兜底。
// 纯控制流,不依赖 Playwright(实际点击/检测由调用方注入),便于单测。
async function ensureAllSelected({ isAllChecked, clickSelectAll, sleep, maxClicks = 3 }) {
  let clicks = 0;
  for (let k = 0; k < maxClicks; k++) {
    if (await isAllChecked()) return clicks;   // 已全勾,停(单轮点 1 次后即在此退出)
    await clickSelectAll();
    clicks++;
    if (sleep) await sleep(300);
  }
  return clicks;
}

module.exports = { ensureAllSelected };
