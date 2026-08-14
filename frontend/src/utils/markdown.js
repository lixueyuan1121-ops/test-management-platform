/**
 * Markdown 渲染（安全）。
 *
 * 用于把需求正文等 Markdown 文本渲染为 HTML 展示。内容可能来自抓取的网页 /
 * 飞书文档等不可信来源，故：markdown-it 关闭原始 HTML 解析（html:false），
 * 渲染结果再经 DOMPurify 净化一层，双保险防 XSS。调用方拿到的是可安全
 * v-html 的字符串。
 */
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false,      // 不解析源码里的原始 HTML（降低 XSS 面，Markdown 语法仍完整）
  linkify: true,    // 裸链接自动转为 <a>
  breaks: true,     // 单换行渲染为 <br>，更贴近编辑器里的直观换行
})

// 渲染出的链接统一新窗口打开 + 加 rel，避免站内跳转与 opener 泄露。
const _defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const t = tokens[idx]
  t.attrSet('target', '_blank')
  t.attrSet('rel', 'noopener noreferrer')
  return _defaultLinkOpen(tokens, idx, options, env, self)
}

/**
 * 把 Markdown 文本渲染为净化后的安全 HTML 字符串。
 * @param {string} text Markdown 源码
 * @returns {string} 可用于 v-html 的 HTML；text 为空时返回空串
 */
export function renderMarkdown(text) {
  if (!text) return ''
  const raw = md.render(String(text))
  // target/rel 是我们自己加的，允许保留；其余按 DOMPurify 默认白名单净化。
  return DOMPurify.sanitize(raw, { ADD_ATTR: ['target', 'rel'] })
}
