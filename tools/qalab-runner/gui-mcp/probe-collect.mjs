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

/** 元素矩形 el 有多大比例落在框 box 内 = 交集面积 / el 面积（0~1）。任一为空/el 无面积 → 0。
 *  框选用它排除"仅小部分与框相交的页面级大容器"（.shell 等，占比极小），只留大部分在框内的目标控件。 */
export function rectInsideRatio(el, box) {
  if (!el || !box) return 0;
  const ix = Math.max(0, Math.min(el.x + el.w, box.x + box.w) - Math.max(el.x, box.x));
  const iy = Math.max(0, Math.min(el.y + el.h, box.y + box.h) - Math.max(el.y, box.y));
  const inter = ix * iy;
  const area = el.w * el.h;
  return area > 0 ? inter / area : 0;
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
