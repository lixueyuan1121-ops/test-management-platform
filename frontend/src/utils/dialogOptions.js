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

// 三项拼成下发 body 的 dialog_options;全空返回 null(后端按"未指定"处理,客户端保持页面默认)
export function buildDialogOptions({ chatMode, model, thinkingDepth }) {
  const out = {}
  if (chatMode) out.chatMode = chatMode
  if ((model || '').trim()) out.model = model.trim()
  if (thinkingDepth) out.thinkingDepth = thinkingDepth
  return Object.keys(out).length ? out : null
}

// 紧凑展示:「计划模式 · GLM-5.2 · 深思:高」;空/未指定返回 ''(由调用方显示"默认")
export function fmtDialogOptions(opts) {
  if (!opts || typeof opts !== 'object') return ''
  const parts = []
  if (opts.chatMode) parts.push(opts.chatMode)
  if (opts.model) parts.push(opts.model)
  if (opts.thinkingDepth) parts.push(`深思:${opts.thinkingDepth}`)
  return parts.join(' · ')
}
