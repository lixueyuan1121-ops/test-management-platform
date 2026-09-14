// 缺失 key ↔ 探测元素 语义匹配（纯函数，供 P1「定位缺失 key」桥接排序推荐）。
// 信号：key 名(驼峰/下划线拆词) + 用例上下文(title/steps) 与 元素 text / 候选 value·name / testid 的词重叠。
// key 名多为英文(submitOrderBtn)→ 命中元素的英文属性(testid/css)；用例上下文多为中文 → 命中元素可见文案。两路互补。
const CJK = '一-鿿'

// 拆词：驼峰插空格 → 按非字母数字/CJK 切分 → 小写 → 去掉长度 <2 的英文碎片(CJK 单字保留)。
export function tokenize(s) {
  if (!s) return []
  const spaced = String(s).replace(/([a-z0-9])([A-Z])/g, '$1 $2')
  const raw = spaced.toLowerCase().split(new RegExp(`[^a-z0-9${CJK}]+`))
  return raw.filter((t) => t && (t.length >= 2 || new RegExp(`[${CJK}]`).test(t)))
}

// 元素可匹配文本池：可见 text + 各候选的 value/name。
function elementTokens(el) {
  const parts = [el.text || '']
  const cands = (el.candidates && el.candidates.length) ? el.candidates : (el.best ? [el.best] : [])
  for (const c of cands) {
    if (c && c.value) parts.push(String(c.value))
    if (c && c.name) parts.push(String(c.name))
  }
  return new Set(tokenize(parts.join(' ')))
}

// 对一个元素给缺失 key 打分：key 词命中强、上下文词命中弱、有稳定锚点轻微加权。
export function scoreElement(key, ctxTokens, el) {
  const hay = elementTokens(el)
  let score = 0
  for (const t of tokenize(key)) if (hay.has(t)) score += 3
  for (const t of (ctxTokens || [])) if (hay.has(t)) score += 1
  const cands = (el.candidates && el.candidates.length) ? el.candidates : (el.best ? [el.best] : [])
  if (score > 0 && cands.some((c) => c && (c.by === 'testid' || (c.by === 'css' && /^#/.test(c.value || ''))))) score += 0.5
  return score
}

// 按匹配度对探测元素排序（降序），返回 [{ el, score }, ...]。
export function rankElements(key, ctx, elements) {
  const ctxTokens = tokenize(ctx)
  return (elements || [])
    .map((el) => ({ el, score: scoreElement(key, ctxTokens, el) }))
    .sort((a, b) => b.score - a.score)
}

// 自动配对必须有目标本身的依据；页面词或整段祖先 CSS 只能用于排序，不能代表控件。
export function hasSpecificMatch(key, el, hint = {}) {
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase()
  const cands = el.candidates?.length ? el.candidates : (el.best ? [el.best] : [])
  const labels = [el.text, ...cands.flatMap(c => c.by === 'text' ? [c.value] : c.by === 'role' ? [c.name] : [])]
    .map(normalize).filter(Boolean)
  const expected = (hint?.expectedTexts || []).map(normalize).filter(Boolean)
  // 严格使用控件文案，不能用含该词的整页文本来代表子控件。
  if (expected.length) return expected.every(text => labels.includes(text))
  const ownValues = cands.flatMap(c => {
    if (c.by === 'css') {
      // 复合路径里的 home 等祖先信息不是当前元素的身份。
      return /[\s>+~]/.test(c.value || '') ? [] : [c.value]
    }
    if (c.by === 'xpath') return []
    return [c.value, c.name]
  })
  const hay = new Set(tokenize([el.text, ...ownValues].filter(Boolean).join(' ')))
  const words = [...new Set(tokenize(key))]
  const hit = words.filter(word => hay.has(word))
  return words.length > 0 && hit.length >= Math.min(2, words.length) && hit.length / words.length >= 0.6
}
