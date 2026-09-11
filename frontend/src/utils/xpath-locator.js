// 从探测元素自动生成 XPath 定位（当 CSS 类名多命中、需按文本精确区分时用）。
// 例:两个 class 相同、只文本不同的按钮(「打开文件夹」「查看原文件」)——CSS 类会多命中,
// XPath 用 class + 精确文本区分:
//   //button[contains(@class,'task-files__preview-office-local-btn')][normalize-space(.)='打开文件夹']
// 纯函数,便于单测;镜像 runner byToLocator 的 xpath 分支(scope.locator("xpath="+value))。

// 从元素候选里取第一条 CSS「类选择器」(.a 或 .a.b;排除 #id / [attr]) → 类名数组。
export function cssClassesFromEl(el) {
  for (const c of (el?.candidates || [])) {
    if (c && c.by === 'css' && typeof c.value === 'string'
        && c.value.startsWith('.') && !/[#[]/.test(c.value)) {
      return c.value.split('.').map((s) => s.trim()).filter(Boolean)
    }
  }
  return []
}

// XPath 1.0 无字符串转义:优先单引号包裹;文本含单引号则用双引号;两者都含(极罕见)→ 返回空(跳过文本条件)。
export function xpathTextEq(text) {
  const t = (text || '').trim()
  if (!t) return ''
  if (!t.includes("'")) return `normalize-space(.)='${t}'`
  if (!t.includes('"')) return `normalize-space(.)="${t}"`
  return ''
}

// 生成 XPath:标签 + 各类名 contains + 精确文本。无任何条件(既无类又无文本)→ 返回空。
export function autoXPath(el) {
  const tag = (el?.tag || '*').toLowerCase() || '*'
  const conds = cssClassesFromEl(el).map((cls) => `contains(@class,'${cls}')`)
  const textEq = xpathTextEq(el?.text)
  if (textEq) conds.push(textEq)
  if (!conds.length) return ''
  return `//${tag}[${conds.join('][')}]`
}

// 该元素的 CSS 类选择器值(用于统计多命中);无则空串。
export function cssSelectorValue(el) {
  const c = (el?.candidates || []).find(
    (x) => x && x.by === 'css' && typeof x.value === 'string' && x.value.startsWith('.') && !/[#[]/.test(x.value),
  )
  return c ? c.value : ''
}

// 统计探测结果里有多少个元素共享同一 CSS 类选择器值(≥2 即「该 CSS 多命中」,建议改 XPath)。
// elements: 扁平的探测元素数组(各含 candidates)。cssVal 空 → 0。
export function countCssMatches(elements, cssVal) {
  if (!cssVal) return 0
  let n = 0
  for (const e of (elements || [])) {
    if ((e?.candidates || []).some((x) => x && x.by === 'css' && x.value === cssVal)) n += 1
  }
  return n
}
