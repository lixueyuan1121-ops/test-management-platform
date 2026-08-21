// 选择器候选「稳定/脆弱」口径（前端侧）。
// 脆弱 by = text/role：getByText 子串匹配、role 靠 name，均 copy 依赖，易失效且多命中会 strict 报错。
// 镜像后端 backend/app/services/selector_ranking.py（改一处必改另一处），
// 参照 tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates 分梯（text/role 为最低档）。
export const FRAGILE_BYS = new Set(['text', 'role'])

// 候选是否脆弱（by=text/role）。缺 by 按 css（稳定）处理。
export function isFragile(cand) {
  return FRAGILE_BYS.has(cand?.by || 'css')
}

// 稳定候选在前、脆弱候选在后，各自保持相对顺序（返回新数组）。
export function orderCandidates(cands) {
  const list = cands || []
  const stable = list.filter((c) => !isFragile(c))
  const fragile = list.filter((c) => isFragile(c))
  return [...stable, ...fragile]
}
