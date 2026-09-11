// 选择器候选「稳定/脆弱」口径（前端侧）。
// 脆弱 by = text/role：getByText 子串匹配、role 靠 name，均 copy 依赖，易失效且多命中会 strict 报错。
// 镜像后端 backend/app/services/selector_ranking.py（改一处必改另一处），
// 参照 tools/qalab-runner/gui-mcp/gui-core.mjs::genCandidates 分梯（text/role 为最低档）。
export const FRAGILE_BYS = new Set(['text', 'role'])

// 候选是否脆弱（by=text/role）。缺 by 按 css（稳定）处理。
export function isFragile(cand) {
  return FRAGILE_BYS.has(cand?.by || 'css')
}

// 候选优先级权重（越小越先试）：testid > xpath（class+文本等精确定位）> css/label/placeholder（含缺省 by）
// > text/role（脆弱降尾）。镜像后端 selector_ranking._BY_RANK。CSS 类名多命中时用 XPath 精确区分。
const BY_RANK = { testid: 0, xpath: 1, text: 3, role: 3 }
function rankOf(cand) {
  return BY_RANK[cand?.by || 'css'] ?? 2
}

// 按 by 优先级稳定排序（testid > xpath > css/label/placeholder > text/role），返回新数组。
export function orderCandidates(cands) {
  return [...(cands || [])].sort((a, b) => rankOf(a) - rankOf(b))
}
