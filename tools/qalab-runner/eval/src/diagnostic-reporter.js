const fs = require('fs');
const path = require('path');

// 诊断报告收集器：统一记录「并发观察诊断」（串台/agent/结构）与「执行侧诊断」（完成无正文/耗时需刷新）。
// 每条诊断：① 控制台打印（logger.warn）② 若传入 page 则截当前可视区域一张 PNG 存档 ③ 收尾生成 HTML
// 报告（有图有描述），图片以相对路径引用，报告与截图目录放在一起，整体拷走即可离线查看。
class DiagnosticReporter {
  constructor(outputDir, logger) {
    this.logger = logger || null;
    this.records = [];
    this.outputDir = outputDir || './output';
    this.shotSeq = 0;
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    this.stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    this.startedAt = d;
    // 截图子目录（与 HTML 报告同级；HTML 用相对路径引用其中的 PNG）
    this.shotDirName = `diagnostics_${this.stamp}_shots`;
    this.shotDir = path.join(this.outputDir, this.shotDirName);
    this.htmlPath = path.join(this.outputDir, `diagnostics_${this.stamp}.html`);
    try {
      fs.mkdirSync(this.shotDir, { recursive: true });
      this._ready = true;
    } catch (e) {
      if (this.logger) this.logger.warn(`诊断截图目录创建失败（诊断将无截图）: ${e.message}`);
      this._ready = false;
    }
  }

  // 记录一条诊断（异步：需要时截图）。
  //  label：关联标识（用例号/会话标题）；code：代号（A~E）；detail：具体描述；
  //  page：可选，Playwright Page；传入则截「当前可视区域」一张 PNG 存档，报告里内嵌该图。
  async record(label, code, detail, page) {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    let shot = null;
    if (page && this._ready) {
      try {
        const file = `${String(++this.shotSeq).padStart(3, '0')}_${this._safe(code)}_${this._safe(label)}.png`;
        await page.screenshot({ path: path.join(this.shotDir, file) }); // 默认仅可视区域（非 fullPage）
        shot = `${this.shotDirName}/${file}`; // 相对 HTML 的路径
      } catch (e) {
        if (this.logger) this.logger.warn(`   🩺 诊断截图失败(${code} ${label}): ${e.message}`);
      }
    }
    this.records.push({ time, label, code, detail, shot });
    if (this.logger) this.logger.warn(`   🩺 诊断 [${code}] ${label}: ${detail}${shot ? '（已截图）' : ''}`);
    // 增量落盘：每记录一条就即时更新 HTML 报告，这样即便任务中途 Ctrl-C / 异常 / 被强杀，
    // 磁盘上的报告也已是最新的——不依赖退出钩子（Windows 下信号处理不一定可靠）。
    this._writeHtml();
  }

  // 文件名安全化：去掉路径非法字符，限长，避免超长标题/特殊符号导致写文件失败。
  _safe(s) {
    return String(s || '').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40) || 'x';
  }

  get count() { return this.records.length; }

  // 生成/更新 HTML 报告文件（有图有描述 + 按代号统计）。返回 HTML 路径（写失败/无内容则 null）。
  // 每条 record 后增量调用，故中途退出也有最新报告；重复覆盖写同样内容无害，不做幂等。
  _writeHtml() {
    if (!this._ready && this.records.length === 0) return null;
    const byCode = {};
    for (const r of this.records) byCode[r.code] = (byCode[r.code] || 0) + 1;
    try {
      fs.writeFileSync(this.htmlPath, this._buildHtml(byCode));
      return this.htmlPath;
    } catch (e) {
      if (this.logger) this.logger.warn(`诊断 HTML 报告生成失败: ${e.message}`);
      return null;
    }
  }

  // 收尾：确保 HTML 最新并返回路径（正常结尾 / 异常 catch / Ctrl-C 钩子都可调，安全重复）。
  finalize() {
    return this._writeHtml();
  }

  _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  _buildHtml(byCode) {
    const summaryRows = Object.entries(byCode)
      .map(([c, n]) => `<li><span class="code">${this._esc(c)}</span> × ${n}</li>`).join('') || '<li>（无）</li>';

    const cards = this.records.map((r, i) => {
      const img = r.shot
        ? `<a href="${this._esc(r.shot)}" target="_blank"><img src="${this._esc(r.shot)}" loading="lazy" alt="截图"></a>`
        : `<div class="noshot">（本条无截图）</div>`;
      return `<div class="card">
      <div class="meta">
        <span class="idx">#${i + 1}</span>
        <span class="code">${this._esc(r.code)}</span>
        <span class="label">${this._esc(r.label)}</span>
        <span class="time">${this._esc(r.time)}</span>
      </div>
      <div class="detail">${this._esc(r.detail)}</div>
      <div class="shot">${img}</div>
    </div>`;
    }).join('\n');

    const empty = this.records.length === 0
      ? '<p class="empty">🎉 本次运行未发现诊断异常。</p>' : '';

    return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 测评诊断报告 ${this._esc(this.stamp)}</title>
<style>
  * { box-sizing: border-box; }
  body { font: 14px/1.6 -apple-system, "Microsoft YaHei", sans-serif; margin: 0; background: #f5f6f8; color: #222; }
  header { background: #1f2937; color: #fff; padding: 20px 28px; }
  header h1 { margin: 0 0 6px; font-size: 20px; }
  header .sub { color: #9ca3af; font-size: 13px; }
  .summary { background: #fff; margin: 16px 28px; padding: 16px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .summary h2 { margin: 0 0 10px; font-size: 15px; }
  .summary ul { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 10px; }
  .summary li { background: #eef2ff; padding: 4px 12px; border-radius: 999px; font-size: 13px; }
  .cards { padding: 0 28px 40px; display: flex; flex-direction: column; gap: 14px; }
  .card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; border-left: 4px solid #f59e0b; }
  .meta { display: flex; align-items: center; gap: 10px; padding: 12px 16px 6px; flex-wrap: wrap; }
  .idx { color: #9ca3af; font-weight: 700; }
  .code { background: #fef3c7; color: #92400e; padding: 2px 10px; border-radius: 6px; font-weight: 700; font-size: 13px; }
  .label { font-weight: 600; }
  .time { color: #9ca3af; font-size: 12px; margin-left: auto; }
  .detail { padding: 0 16px 12px; color: #374151; }
  .shot { padding: 0 16px 16px; }
  .shot img { max-width: 100%; border: 1px solid #e5e7eb; border-radius: 6px; display: block; }
  .noshot { color: #9ca3af; font-size: 13px; }
  .empty { text-align: center; color: #10b981; font-size: 16px; padding: 40px; }
</style>
</head>
<body>
<header>
  <h1>🩺 AI 测评诊断报告</h1>
  <div class="sub">生成于 ${this._esc(this.startedAt.toLocaleString('zh-CN'))} · 共 ${this.records.length} 条诊断</div>
</header>
<div class="summary">
  <h2>按类型统计</h2>
  <ul>${summaryRows}</ul>
  <p style="margin:10px 0 0;color:#6b7280;font-size:12px;">代号说明：A 疑似串台 / B agent 异常 / C 连续提问无正文 / D 完成无正文 / E 字段需刷新</p>
</div>
${empty}
<div class="cards">
${cards}
</div>
</body>
</html>`;
  }
}

module.exports = DiagnosticReporter;
