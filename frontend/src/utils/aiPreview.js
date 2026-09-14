// Presentation only: incomplete output never enters the editable/confirmed draft.
const labels = {
  text: '识别内容 / 验收条件', uncertainties: '待核对内容', summary: '需求概述', scope: '本期范围',
  out_of_scope: '本期不做', flow: '关键流程', title: '标题', module: '模块', platform: '平台',
  condition: '前提条件', action: '操作', expected: '预期结果', forbidden: '禁止行为', boundaries: '边界与例外',
  evidence: '验证方式', question: '澄清问题', options: '可选结论', source_section: '来源', source_quote: '原文依据',
  actor: '角色', given: '场景前提', when: '场景操作', then: '场景结果', counterexample: '反例',
  precondition: '前置条件', steps: '测试步骤', kind_reason: '执行方式说明', category: '分类',
}

export function readableAiPreview(raw) {
  const parts = []
  let key = ''
  // Scan JSON strings, including an unfinished value at the end of the stream.
  // Decode escapes only when complete; a later snapshot supplies the rest.
  for (let i = 0; i < raw.length; i++) {
    if (raw[i] !== '"') continue
    let value = '', closed = false
    for (i++; i < raw.length; i++) {
      const c = raw[i]
      if (c === '"') { closed = true; break }
      if (c !== '\\') { value += c; continue }
      const next = raw[++i]
      if (next === 'u') {
        const hex = raw.slice(i + 1, i + 5)
        if (!/^[a-f\d]{4}$/i.test(hex)) break
        value += String.fromCharCode(parseInt(hex, 16)); i += 4
      } else if (next) {
        value += ({ n: '\n', r: '\r', t: '\t', b: '', f: '', '"': '"', '\\': '\\', '/': '/' })[next] ?? next
      }
    }
    if (closed && /^\s*:/.test(raw.slice(i + 1))) key = value
    else if (labels[key] && value.trim()) parts.push(`${labels[key]}\n${value}`)
  }
  return parts.join('\n\n')
}
