import { candidateIdentity, isActiveCandidate } from './selector-ranking.js'

// 延续注册表原有稳定候选匹配，同时按本次探测的 frame 别名和唯一性隔离。
const reusable = c => isActiveCandidate(c) && (c.by === 'testid' || (c.by === 'role' && c.name && c.exact)
  || (c.by === 'css' && (/^#/.test(c.value) || /^\[data-test/.test(c.value))))
const frameOf = (frame, aliases) => {
  const value = aliases[frame || 'auto'] || frame || 'auto'
  return value === 'content' ? 'vm' : value
}
export function elementStatus(element, rows = [], elements = [], aliases = {}) {
  const frame = frameOf(element._frameMatch || element._frame, aliases)
  const identity = (candidate, scope) => `${scope}|${candidateIdentity(candidate)}`
  const owners = new Map(), occurrences = new Map()
  for (const [i, el] of elements.entries()) {
    for (const c of (el.candidates || []).filter(reusable)) {
      const id = identity(c, frameOf(el._frameMatch || el._frame, aliases))
      if (!occurrences.has(id)) occurrences.set(id, new Set())
      occurrences.get(id).add(el.element_ref || el._uid || i)
    }
  }
  for (const row of rows) for (const c of (row.candidates || []).filter(reusable)) {
    const id = identity(c, frameOf(row.frame, aliases))
    if ((occurrences.get(id)?.size || 0) > 1) continue
    if (!owners.has(id)) owners.set(id, row.key)
    else if (owners.get(id) !== row.key) owners.set(id, null)
  }
  const hits = new Set((element.candidates || []).filter(reusable).map(c => owners.get(identity(c, frame))).filter(Boolean))
  if (hits.size !== 1) return { type: 'new' }
  const key = [...hits][0]
  const exists = element.best && reusable(element.best) && owners.get(identity(element.best, frame)) === key
  return { type: exists ? 'exists' : 'update', key }
}

export function suggestKey(element, page = '', reserved = new Set()) {
  const candidate = (element.candidates || []).find(c => c.by === 'testid') || element.best
  const seed = [page, candidate?.value || element.accessibleName || element.text || element.tag || 'element'].join('_')
  let hash = 2166136261
  for (const char of seed) hash = Math.imul(hash ^ char.codePointAt(0), 16777619)
  const stem = seed.replace(/[^a-zA-Z0-9_]+/g, '_').replace(/^_+|_+$/g, '') || `element_${(hash >>> 0).toString(36)}`
  const base = (/^[a-zA-Z]/.test(stem) ? stem : `element_${stem}`).slice(0, 56)
  let key = base, i = 2
  while (reserved.has(key)) key = `${base}_${i++}`
  reserved.add(key)
  return key
}
