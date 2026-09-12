// 镜像后端 selector_ranking.py；现场唯一性由探测/runtime 验证。
export const FRAGILE_BYS = new Set(['text', 'role'])
const VALID_BYS = new Set(['testid', 'xpath', 'role', 'label', 'text', 'placeholder', 'css'])
export function normalizeCandidate(c) {
  if (!c || !VALID_BYS.has(c.by) || typeof c.value !== 'string' || !c.value.trim()
      || ('name' in c && typeof c.name !== 'string') || ('exact' in c && typeof c.exact !== 'boolean')) return null
  return Object.fromEntries(['by', 'value', 'name', 'exact', 'src', 'status', 'disabled']
    .filter(k => k in c).map(k => [k, c[k]]))
}
export const candidateIdentity = c => {
  const match = c.by === 'css' && c.value?.match(/^\[data-testid=("(?:[^"\\]|\\.)*"|[\w-]+)\]$/)
  if (match) { try { c = { ...c, by: 'testid', value: match[1].startsWith('"') ? JSON.parse(match[1]) : match[1] } } catch {} }
  return JSON.stringify([c.by, c.value, c.name || null, c.exact ?? false])
}
export const isActiveCandidate = c => !!normalizeCandidate(c) && c.src !== 'learned'
  && !['pending', 'rejected', 'retired'].includes(c.status) && c.disabled !== true
export function isFragile(c) {
  return (c?.by === 'role' && !(c.name && c.exact)) || (c?.by === 'text' && !c.exact)
}
export function candidateRank(c) {
  if (c.by === 'testid') return 0
  if (c.by === 'role' && c.name && c.exact) return 1
  if (c.by === 'label') return 2
  if (c.by === 'placeholder') return 3
  if (c.by === 'css' && (c.value.startsWith('#') || c.value.startsWith('[data-test'))) return 4
  if (c.by === 'text' && c.exact) return 5
  if (c.by === 'role' && c.name) return 6
  if (c.by === 'xpath') return 7
  if (c.by === 'css') return 8
  return 9
}
export const orderCandidates = cands => [...(cands || [])].sort((a, b) => candidateRank(a) - candidateRank(b))
export function mergeCandidates(...groups) {
  const unique = new Map()
  for (const c of groups.flat().map(normalizeCandidate).filter(Boolean)) {
    const id = candidateIdentity(c)
    if (!unique.has(id)) unique.set(id, c)
  }
  return orderCandidates([...unique.values()]).slice(0, 6)
}
