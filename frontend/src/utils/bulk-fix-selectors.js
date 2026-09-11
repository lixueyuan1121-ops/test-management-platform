// 批量补选择器纯逻辑(无 Vue/DOM 依赖,便于单测)。
// 服务于用例库「批量补选择器」:汇总选中待补用例缺的 key、去重剔重复,
// 以及探测后把探测元素按语义批量配对到各待补 key。候选来自真实探测,不臆造 CSS。
import { scoreElement, tokenize } from "./selector-match.js";

// 汇总选中用例里「选择器待补」的缺失 key。
// cases:用例数组(每条含 selector_fix / selector_fix_keys / title / steps)。
// registered:已注册 key 集合(Set 或数组)——命中即剔除(遵循已有覆盖,不重复建)。
// 返回 { keys:去重剔已注册后按首现保序的缺 key, skipped:因已注册而跳过的 key,
//        caseCount:参与的待补用例数, ctx:待补用例 title+steps 拼接的语义匹配上下文 }。
export function collectMissingKeys(cases, registered) {
  const reg = registered instanceof Set ? registered : new Set(registered || []);
  const keys = [];
  const seen = new Set();
  const skipped = [];
  const ctxParts = [];
  let caseCount = 0;
  for (const c of cases || []) {
    if (!c || !c.selector_fix) continue; // 只处理「选择器待补」用例
    caseCount += 1;
    ctxParts.push(`${c.title || ""} ${c.steps || ""}`.trim());
    for (const k of c.selector_fix_keys || []) {
      if (!k || seen.has(k)) continue;
      seen.add(k);
      if (reg.has(k)) { skipped.push(k); continue; } // 已注册 → 已有覆盖,跳过
      keys.push(k);
    }
  }
  return { keys, skipped, caseCount, ctx: ctxParts.join(" ").trim().slice(0, 400) };
}

// 把探测元素按语义批量配对到待补 key(贪心:分数从高到低,一元素配一 key、一 key 配一元素)。
// keys:待补 key 名数组;ctx:用例上下文(中文文案助配);elements:探测结果元素数组。
// 返回 [{ key, el:配上的元素或null, score }],每个 key 一条(未配上 el=null,留待换状态再探)。
export function matchElementsToKeys(keys, ctx, elements) {
  const ctxTokens = tokenize(ctx);
  // 所有 (key,元素) 组合打分,收集正分候选
  const scored = [];
  for (const key of keys || []) {
    for (const el of elements || []) {
      const s = scoreElement(key, ctxTokens, el);
      if (s > 0) scored.push({ key, el, score: s });
    }
  }
  scored.sort((a, b) => b.score - a.score);
  const usedKeys = new Set();
  const usedEls = new Set();
  const pairByKey = new Map();
  for (const { key, el, score } of scored) {
    if (usedKeys.has(key) || usedEls.has(el)) continue; // 贪心去冲突:key/元素各只用一次
    usedKeys.add(key);
    usedEls.add(el);
    pairByKey.set(key, { key, el, score });
  }
  // 每个 key 都出一条(未配上的 el=null),保持与 keys 对应,便于 UI 展示"哪些还缺"
  return (keys || []).map((key) => pairByKey.get(key) || { key, el: null, score: 0 });
}
