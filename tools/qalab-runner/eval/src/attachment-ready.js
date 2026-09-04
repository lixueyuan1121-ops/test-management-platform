// 从「各候选选择器 → 计数」判定附件是否已挂:按优先级顺序,任一候选计数 ≥ expectN 即 ready,
// 返回命中的选择器。纯逻辑(不依赖 Playwright),便于单测;真实计数由调用方用 ctx.locator 提供。
//
// 为什么多候选:桌面客户端真实的草稿附件卡片 DOM 未知,单一自定义选择器易失配、误杀带附件用例。
function pickReadyAttachment(results, expectN) {
  for (const r of (results || [])) {
    if (r && r.n >= expectN) return { ready: true, via: r.sel, count: r.n };
  }
  return { ready: false, via: null, count: 0 };
}

module.exports = { pickReadyAttachment };
