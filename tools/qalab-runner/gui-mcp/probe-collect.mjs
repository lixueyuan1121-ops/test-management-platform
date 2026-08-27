// 探测采集纯逻辑（可脱机 node:test 测）。
//
// DISCOVER_SCRIPT 在浏览器 frame.evaluate() 内执行，不能 import 本模块（evaluate 需自包含），
// 故 gui-core.mjs 的 DISCOVER_SCRIPT 内联同款 collectDeep 实现；本模块是可测副本 +
// 供 probe 主体（Node 侧）复用的 rectIntersect。两边逻辑一致由测试保证。

/** 两个 {x,y,w,h} 矩形是否相交（含边界相接）。任一为空 → false。 */
export function rectIntersect(a, b) {
  if (!a || !b) return false;
  return a.x <= b.x + b.w && a.x + a.w >= b.x && a.y <= b.y + b.h && a.y + a.h >= b.y;
}

/** 从 root（document 或 shadowRoot）递归收集所有元素，穿透 open shadowRoot。
 *  纯 DOM 遍历（只用 querySelectorAll + el.shadowRoot），便于 mock 脱机测。 */
export function collectDeep(root, { includeShadow = true } = {}) {
  const out = [];
  for (const el of root.querySelectorAll("*")) {
    out.push(el);
    if (includeShadow && el.shadowRoot) {
      out.push(...collectDeep(el.shadowRoot, { includeShadow }));
    }
  }
  return out;
}
