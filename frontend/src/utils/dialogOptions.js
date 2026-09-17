// 对话测评「对话选项」共享常量与工具:下发时统一指定被测客户端(纳米 Work)发送前的三个开关,
// EvalLibrary(题库下发)与 EvalTasks(任务执行/列表展示)共用,避免两处文案漂移。
// ⚠️ value 必须与客户端页面下拉选项的真实文案一致——执行器按文本匹配点选
// (见 tools/qalab-runner/eval config.platform 选择器段与 dialog-runner._applyDialogOptions)。

// 对话模式:值=客户端页面下拉选项的真实文案(2026-08 现网确认),执行器按文本一字不差匹配点选
export const CHAT_MODES = [
  { value: '边想边做', label: '边想边做' },
  { value: '先规划，再执行', label: '先规划，再执行' },
  { value: '盯住目标做到底', label: '盯住目标做到底' },
]

// 思考深度:值即页面选项文案
export const THINKING_DEPTHS = ['低', '中', '标准', '高', '超高']

// 模型名手动输入的占位提示(下拉项随客户端版本变化,故不做成枚举)
export const MODEL_PLACEHOLDER = '模型名(留空=默认),如 GLM-5.2 / 豆包（seed-2.1）'

export const MAX_DIALOG_COMBINATIONS = 300
export const MAX_MATRIX_RUNS = 10000

// 只拆模型输入；对话模式「先规划，再执行」中的逗号属于选项本身。
export function parseModelNames(text) {
  const seen = new Set()
  return String(text || '').split(/[\n\r,，;；]+/).map(v => v.trim()).filter(value => {
    const key = value.toLowerCase()
    if (!value || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function dialogCombinationCount(matrix) {
  return ['chatMode', 'model', 'thinkingDepth'].reduce((count, key) => count * Math.max(1, matrix?.[key]?.length || 0), 1)
}

// 三项拼成下发 body 的 dialog_options;全空返回 null(后端按"未指定"处理,客户端保持页面默认)
export function buildDialogOptions({ chatMode, model, thinkingDepth }) {
  const out = {}
  if (chatMode) out.chatMode = chatMode
  if ((model || '').trim()) out.model = model.trim()
  if (thinkingDepth) out.thinkingDepth = thinkingDepth
  return Object.keys(out).length ? out : null
}

// 紧凑展示:「计划模式 · GLM-5.2 · 深思:高」;空/未指定返回 ''(由调用方显示"默认")
// A/B 对比执行(带 compareB 键)显示「A组 vs B组」,任一组全默认显示"默认"
export function fmtDialogOptions(opts) {
  if (!opts || typeof opts !== 'object') return ''
  const one = (o) => {
    const parts = []
    if (o?.chatMode) parts.push(o.chatMode)
    if (o?.model) parts.push(o.model)
    if (o?.thinkingDepth) parts.push(`深思:${o.thinkingDepth}`)
    return parts.join(' · ')
  }
  if ('compareB' in opts) return `${one(opts) || '默认'} vs ${one(opts.compareB) || '默认'}`
  if (opts.matrix) {
    const m = opts.matrix
    const parts = [m.chatMode?.join(' / '), m.model?.join(' / '), m.thinkingDepth?.length && `深思:${m.thinkingDepth.join(' / ')}`].filter(Boolean)
    return `纳米Work ${dialogCombinationCount(m)} 种组合：${parts.join(' · ') || '沿用配置'}${opts.model ? `；WorkBuddy：${opts.model}` : ''}`
  }
  return one(opts)
}
