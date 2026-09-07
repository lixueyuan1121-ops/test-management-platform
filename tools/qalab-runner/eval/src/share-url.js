// 从分享面板 DOM 候选里挑出分享 URL(优先级:input value > a[href] > 可见文本)。
//
// 会话分享链接原只走系统剪贴板(失焦/权限/并发下概率读不到)。彻底解:点生成后优先直读面板 DOM 的
// 链接——面板扫描出各 input value / a[href] / 文本交本函数按优先级挑首个【对话分享】URL,不碰剪贴板。
// 纯逻辑,便于单测;实际 DOM 收集由调用方(evaluate)提供。
//
// ⚠️ 只认对话分享链接(含 /share/,如 work.n.cn/share/xxx):真机坐实分享面板的 a[href] 全是产物文件 URL
// (ns.chat.360.cn/zhaomi-so/client-up/...,不含 /share/),对话分享链接只在剪贴板。若不筛选,直读会抓回
// 产物链接。要产物链接走独立的 extractArtifactShareLink,不复用本函数。
const _URL_RE = /https?:\/\/[^\s"'<>]+/g;

// 从字符串里挑第一个【对话分享】URL(含 /share/ 路径);无则空。
function _firstShare(str) {
  const s = String(str == null ? '' : str);
  const all = s.match(_URL_RE) || [];
  for (const u of all) { if (u.includes('/share/')) return u; }
  return '';
}

function pickShareUrl({ inputs = [], hrefs = [], text = '' } = {}) {
  for (const v of inputs) { const u = _firstShare(v); if (u) return u; }   // 生成的链接多在只读输入框,最可靠
  for (const h of hrefs) { const u = _firstShare(h); if (u) return u; }
  return _firstShare(text);                                                // 兜底:面板可见文本
}

module.exports = { pickShareUrl };
