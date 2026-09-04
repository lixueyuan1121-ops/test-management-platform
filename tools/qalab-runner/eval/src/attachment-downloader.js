// 平台模式附件下载器:把 payload.attachments([{name,url}])下到本地目录,返回本地路径数组。
//
// 平台生成侧把附件存为 {name,url}(公开 CDN 链接,见 claude_runner 生成规范);执行前须下到本地,
// 以本地路径喂给桌面执行器 setInputFiles 上传。任一附件下载失败即抛错——上层据此 fail-closed 整组,
// 绝不「缺附件裸跑」污染判定。fetchImpl 供测试注入(默认 node-fetch,与 feishu-sheet 同款)。
const fs = require('fs');
const path = require('path');

// 文件名安全化:附件 name 来自平台数据,可能含 ../ 或非法字符 → 只取 basename 并替换非法字符,
// 保证落点始终在 destDir 内(防路径遍历/覆盖任意文件)。空名回落 file<ext>。
function safeFileName(name, fallbackExt) {
  const base = path.basename(String(name || '')).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim();
  return base || `file${fallbackExt || ''}`;
}

function extFromUrl(url) {
  try { return path.extname(new URL(url).pathname) || ''; }
  catch { return path.extname(String(url).split('?')[0]) || ''; }
}

async function downloadAttachments(attachments, destDir, opts = {}) {
  const doFetch = opts.fetchImpl || require('node-fetch');
  const logger = opts.logger;
  const out = [];
  const list = attachments || [];
  for (let i = 0; i < list.length; i++) {
    const att = list[i] || {};
    if (!att.url) throw new Error(`附件缺少可下载 url(name=${att.name || '?'})`);
    const fileName = safeFileName(att.name || `file_${i}`, extFromUrl(att.url));
    const savePath = path.join(destDir, fileName);
    const res = await doFetch(att.url);
    if (!res || !res.ok) throw new Error(`下载附件失败 HTTP ${res && res.status}: ${att.url}`);
    const buf = await res.buffer();
    fs.mkdirSync(path.dirname(savePath), { recursive: true });
    fs.writeFileSync(savePath, buf);
    out.push(savePath);
    if (logger && logger.info) logger.info(`   ✅ 附件下载: ${fileName}`);
  }
  return out;
}

module.exports = { downloadAttachments, safeFileName };
