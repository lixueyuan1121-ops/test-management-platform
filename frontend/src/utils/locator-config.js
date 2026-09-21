import { normalizeCandidate, orderCandidates, candidateIdentity } from './selector-ranking.js'

export function configuredCandidates(config, fallbacks = []) {
  const candidate = normalizeCandidate({ by: config.by, value: String(config.value || '').trim(), primary: true,
    ...(config.mode === 'combined' ? { has_text: String(config.text || '').trim() } : {}),
    ...(config.nth != null ? { nth: config.nth } : {}) })
  if (!candidate) return []
  const extras = config.keepFallback ? orderCandidates(fallbacks.map(c => ({ ...c, primary: false }))) : []
  return [candidate, ...extras.filter(c => candidateIdentity(c) !== candidateIdentity(candidate))].slice(0, 6)
}
