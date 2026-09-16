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

// 在浏览器中执行；Playwright evaluateAll 会穿透开放的 Shadow DOM。
// 「全部」的横线是部分选中，不能按 SVG 存在或背景为黑色推断全选。
function shareSelectionState(boxes) {
  const checked = boxes.map(box => {
    const native = box.matches('input[type="checkbox"]') ? box : box.querySelector('input[type="checkbox"]');
    if (native) return native.checked && !native.indeterminate;
    const aria = box.getAttribute('aria-checked');
    if (aria !== null) return aria === 'true';
    if (box.querySelector('line')) return false;
    return [...box.querySelectorAll('path')].some(path =>
      /^M4\.5[ ,]+8\.5\s*L6\.5[ ,]+10\.5\s*L11\.5[ ,]+5\.5$/i.test((path.getAttribute('d') || '').trim()));
  });
  const toolbar = boxes.findIndex(box => box.closest('.chat-share-panel__toolbar'));
  const items = boxes.filter(box => box.closest('.chat-share-panel__item'));
  return { checked, allChecked: toolbar >= 0 && items.length > 0 && checked.every(Boolean) };
}

module.exports = { ensureAllSelected, shareSelectionState };
