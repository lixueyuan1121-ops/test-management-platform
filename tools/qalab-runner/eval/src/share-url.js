// 从分享面板 DOM 候选里挑出分享 URL(优先级:input value > a[href] > 可见文本)。
//
// 会话分享链接原只走系统剪贴板(失焦/权限/并发下概率读不到)。彻底解:点生成后优先直读面板 DOM 的
// 链接——面板扫描出各 input value / a[href] / 文本交本函数按优先级挑首个 http URL,不碰剪贴板。
// 纯逻辑,便于单测;实际 DOM 收集由调用方(evaluate)提供。
const _URL_RE = /https?:\/\/[^\s"'<>]+/;

function _first(str) {
  const m = String(str == null ? '' : str).match(_URL_RE);
  return m ? m[0] : '';
}

function pickShareUrl({ inputs = [], hrefs = [], text = '' } = {}) {
  for (const v of inputs) { const u = _first(v); if (u) return u; }   // 生成的链接多在只读输入框,最可靠
  for (const h of hrefs) { const u = _first(h); if (u) return u; }
  return _first(text);                                                 // 兜底:面板可见文本
}

module.exports = { pickShareUrl };
