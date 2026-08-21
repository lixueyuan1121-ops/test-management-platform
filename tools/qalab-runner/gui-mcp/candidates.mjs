// 「有效候选」口径(mjs 侧,纯函数、无 playwright 依赖,便于单测)。
// 镜像 backend/app/services/selector_ranking.py::is_valid_candidate /
// frontend/src/utils/selector-ranking.js —— 三处口径契约,改一处必改另两处。
export const VALID_BYS = new Set(["testid", "role", "label", "text", "placeholder", "css"]);

// 有效候选:含合法 by + 非空 value。坏例 {}、{by:"css"}(缺 value)、{value:"x"}(缺 by)、非法 by 均剔除。
export function validCands(cands) {
  return (Array.isArray(cands) ? cands : []).filter((c) => c && VALID_BYS.has(c.by) && c.value);
}

// 逐 key 回落:DB 候选过滤后有效则用之;全坏/空且内置有效 → 用内置同名 key 候选。
export function pickCandidates(dbCands, builtinCands) {
  const db = validCands(dbCands);
  return db.length ? db : validCands(builtinCands);
}
