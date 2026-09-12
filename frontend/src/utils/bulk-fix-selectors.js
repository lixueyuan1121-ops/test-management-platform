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
  const contexts = {};
  let caseCount = 0;
  for (const c of cases || []) {
    if (!c || !c.selector_fix) continue; // 只处理「选择器待补」用例
    caseCount += 1;
    ctxParts.push(`${c.title || ""} ${c.steps || ""}`.trim());
    for (const k of c.selector_fix_keys || []) {
      if (!k) continue;
      let script = c.script;
      try { if (typeof script === 'string') script = JSON.parse(script); } catch { script = []; }
      const matchingSteps = (Array.isArray(script) ? script : []).filter(s => JSON.stringify(s.target || {}).includes(JSON.stringify(k)));
      const localContext = matchingSteps.length ? matchingSteps.map(s => `${s.desc || ''} ${s.args?.expected || ''}`).join(' ') : `${c.title || ''} ${c.steps || ''}`;
      contexts[k] = `${contexts[k] || ''} ${localContext}`.trim().slice(0, 1200);
      if (seen.has(k)) continue;
      seen.add(k);
      if (reg.has(k)) { skipped.push(k); continue; } // 已注册 → 已有覆盖,跳过
      keys.push(k);
    }
  }
  return { keys, skipped, caseCount, contexts, ctx: ctxParts.join(" ").trim().slice(0, 400) };
}

// 把探测元素按语义批量配对到待补 key(贪心:分数从高到低,一元素配一 key、一 key 配一元素)。
// keys:待补 key 名数组;ctx:用例上下文(中文文案助配);elements:探测结果元素数组。
// 返回 [{ key, el:配上的元素或null, score }],每个 key 一条(未配上 el=null,留待换状态再探)。
export function matchElementsToKeys(keys, ctx, elements) {
  const ranked = new Map();
  for (const key of keys || []) {
    const context = typeof ctx === 'object' ? (ctx?.[key] || '') : ctx;
    ranked.set(key, (elements || []).map(el => ({ key, el, score: scoreElement(key, tokenize(context), el) }))
      .filter(p => p.score > 0).sort((a, b) => b.score - a.score));
  }
  // 仅采用明确领先的首选；冲突时不拿第二名凑数，保留人工选择入口。
  const proposed = [...ranked.values()].map(list => {
    const best = list[0];
    if (!best || best.score < 3 || (list[1] && best.score - list[1].score < 2)) return null;
    return best;
  }).filter(Boolean);
  const accepted = new Map();
  for (const p of proposed) {
    const competing = proposed.filter(other => other.el === p.el && other.key !== p.key);
    if (competing.some(other => p.score - other.score < 2)) continue;
    accepted.set(p.key, { ...p, confidence: 'high', reason: '语义匹配明确且与其他候选有足够差距' });
  }
  return (keys || []).map(key => accepted.get(key) || { key, el: null, score: 0, confidence: 'uncertain',
    reason: ranked.get(key)?.length ? '候选相近或存在冲突，请手动选择' : '缺少语义匹配依据' });
}
