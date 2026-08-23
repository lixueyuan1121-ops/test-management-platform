const fetch = require('node-fetch');

class FeishuSheetReader {
  constructor(config) {
    this.url = config.url || '';
    this.appId = config.appId || process.env.FEISHU_APP_ID || '';
    this.appSecret = config.appSecret || process.env.FEISHU_APP_SECRET || '';
    this.startRow = config.startRow || 2;
    this.endRow = config.endRow || 200;
    this.queryCol = config.queryCol || 'A';
    this.attachmentCol = config.attachmentCol || 'B';
    // 会话ID列（多轮对话）：同一会话ID的多行 = 同一对话按行序连发的多轮。
    // 留空(未配置该列 或 单元格为空) = 该行自成一个单轮会话（退化为旧的「一行一对话」）。
    this.conversationCol = config.conversationCol || '';
    this.writeColumns = config.writeColumns || {
      conversationShareLink: 'C',
      artifactShareLink: 'D',
      duration: 'E',
      beanCost: 'F',
      answer: 'H'
    };
    this.token = null;
    this.spreadsheetToken = null;
    this.sheetId = null;
    this._isWiki = false;
    this._parseUrl();
  }

  // ---- 列字母 <-> 序号 互转（支持多字母，替代脆弱的 charCode 加减）----
  _colToNum(col) {
    let n = 0;
    for (const ch of String(col).toUpperCase()) {
      const c = ch.charCodeAt(0);
      if (c < 65 || c > 90) continue;
      n = n * 26 + (c - 64);
    }
    return n || 1;
  }

  _numToCol(n) {
    let s = '';
    while (n > 0) {
      const r = (n - 1) % 26;
      s = String.fromCharCode(65 + r) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  // 飞书单元格值可能是字符串、数字，或富文本段落数组；统一转成纯文本
  _cellToText(cell) {
    if (cell == null) return '';
    if (typeof cell === 'string') return cell;
    if (typeof cell === 'number' || typeof cell === 'boolean') return String(cell);
    if (Array.isArray(cell)) return cell.map(seg => this._segText(seg)).join('');
    if (typeof cell === 'object') return this._segText(cell);
    return String(cell);
  }

  _segText(seg) {
    if (seg == null) return '';
    if (typeof seg === 'string') return seg;
    return seg.text || seg.link || '';
  }

  // 从飞书链接解析 spreadsheetToken 和 sheetId
  _parseUrl() {
    if (!this.url) return;
    const sheetParam = this.url.match(/[?&]sheet=([A-Za-z0-9]+)/);
    const sheetId = sheetParam ? sheetParam[1] : '0';

    // 直链：https://xxx.feishu.cn/sheets/<spreadsheetToken>?sheet=<sheetId>
    const sheetsMatch = this.url.match(/\/sheets\/([A-Za-z0-9]+)/);
    if (sheetsMatch) {
      this.spreadsheetToken = sheetsMatch[1];
      this.sheetId = sheetId;
      this._isWiki = false;
      return;
    }

    // wiki 链接：https://xxx.feishu.cn/wiki/<wikiToken>?sheet=<sheetId>
    // spreadsheetToken 需要通过 wiki get_node 接口解析
    const wikiMatch = this.url.match(/\/wiki\/([A-Za-z0-9]+)/);
    if (wikiMatch) {
      this.wikiToken = wikiMatch[1];
      this.sheetId = sheetId;
      this._isWiki = true;
    }
  }

  async getAccessToken() {
    if (!this.appId || !this.appSecret) {
      throw new Error(
        '缺少飞书应用凭证：请在环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 中提供，' +
        '或直接写入配置。需在 https://open.feishu.cn 创建自建应用，开通 sheets 读写权限并授权该表格。'
      );
    }
    const res = await fetch('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_id: this.appId, app_secret: this.appSecret })
    });
    const data = await res.json();
    if (!data.tenant_access_token) {
      throw new Error(`飞书认证失败: ${JSON.stringify(data)}`);
    }
    this.token = data.tenant_access_token;
    // wiki 链接：解析真实的 spreadsheetToken
    if (this._isWiki && !this.spreadsheetToken) {
      await this._resolveWikiSpreadsheetToken();
    }
    return this.token;
  }

  // wiki 链接 → 通过 get_node 拿到电子表格的真实 token
  async _resolveWikiSpreadsheetToken() {
    const url = `https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token=${this.wikiToken}`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${this.token}` } });
    const data = await res.json();
    const node = data.data && data.data.node;
    if (!node || !node.obj_token) {
      throw new Error(`解析 wiki 节点失败: ${JSON.stringify(data)}`);
    }
    this.spreadsheetToken = node.obj_token;
  }

  async _ensureReady() {
    if (!this.token) await this.getAccessToken();
    if (!this.spreadsheetToken) {
      throw new Error('无法从链接解析 spreadsheetToken，请检查 feishu.url 是否为有效的飞书表格链接');
    }
  }

  async readRange(range) {
    await this._ensureReady();
    const url = `https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/${this.spreadsheetToken}/values/${range}`;
    const res = await fetch(url, { headers: { Authorization: `Bearer ${this.token}` } });
    const data = await res.json();
    if (data.code && data.code !== 0) {
      throw new Error(`读取表格失败: ${JSON.stringify(data)}`);
    }
    if (!data.data || !data.data.valueRange) {
      throw new Error(`读取表格失败: ${JSON.stringify(data)}`);
    }
    return data.data.valueRange.values || [];
  }

  async getTestCases() {
    const qNum = this._colToNum(this.queryCol);
    const aNum = this._colToNum(this.attachmentCol);
    const cNum = this.conversationCol ? this._colToNum(this.conversationCol) : null; // 会话ID列（可选）
    // 读取窗口需覆盖 query / 附件 /（若配置）会话ID三列，取最小～最大列一次性读回
    const cols = [qNum, aNum].concat(cNum ? [cNum] : []);
    const lo = Math.min(...cols);
    const hi = Math.max(...cols);
    const range = `${this.sheetId}!${this._numToCol(lo)}${this.startRow}:${this._numToCol(hi)}${this.endRow}`;
    const rows = await this.readRange(range);

    const qi = qNum - lo; // query 在读取窗口内的列索引
    const ai = aNum - lo; // 附件在读取窗口内的列索引
    const ci = cNum != null ? cNum - lo : -1; // 会话ID在窗口内的列索引（-1=未配置会话列）

    const cases = rows.map((row, idx) => {
      const query = this._cellToText(row[qi]).trim();
      const attachments = this._parseAttachments(row[ai]);
      const convId = ci >= 0 ? this._cellToText(row[ci]).trim() : '';
      const realRow = idx + this.startRow;

      return {
        caseId: `CASE-${realRow}`,
        row: realRow, // 真实行号，回填时据此定位，避免并发完成顺序造成错位
        question: query,
        attachments,
        // 会话分组键：填了会话ID用其值；留空则该行自成一组（用行号保证唯一），即退化为单轮独立对话
        conversationId: convId || `__row_${realRow}`,
        hasConversationId: !!convId, // 是否显式指定了会话ID（诊断/日志用）
        turnIndex: 0, // 同会话内第几轮（0 起），下面按行序补齐
        account: 'default'
      };
    }).filter(c => c.question);

    // 按会话分组补齐 turnIndex（同会话内按出现行序 0,1,2...）。注意：过滤空 query 后再排序，
    // 避免中间空行把轮次算错。
    const turnCounter = new Map();
    for (const c of cases) {
      const t = turnCounter.get(c.conversationId) || 0;
      c.turnIndex = t;
      turnCounter.set(c.conversationId, t + 1);
    }
    return cases;
  }

  // 兼容电子表格单元格里附件的多种形态：图片段(fileToken)、超链接段(link)、纯 URL 文本
  _parseAttachments(cell) {
    if (cell == null) return [];
    const atts = [];
    const pushUrl = (u, name) => {
      if (!u) return;
      atts.push({ url: u, name: name || u.split('?')[0].split('/').pop() });
    };
    const pushToken = (t, name) => {
      if (!t) return;
      atts.push({ file_token: t, name: name || t });
    };

    const handle = (seg) => {
      if (seg == null) return;
      if (typeof seg === 'string') {
        const s = seg.trim();
        if (/^https?:\/\//.test(s)) pushUrl(s);
        return;
      }
      if (typeof seg !== 'object') return;
      const token = seg.fileToken || seg.file_token; // 飞书图片段用 fileToken
      if (token) { pushToken(token, seg.text || seg.name); return; }
      if (seg.link) { pushUrl(seg.link, seg.text); return; }
      if (seg.url) { pushUrl(seg.url, seg.name || seg.text); return; }
      if (typeof seg.text === 'string' && /^https?:\/\//.test(seg.text.trim())) pushUrl(seg.text.trim());
    };

    if (Array.isArray(cell)) cell.forEach(handle);
    else handle(cell);
    return atts;
  }

  async downloadAttachment(fileToken, savePath) {
    await this._ensureReady();
    const fs = require('fs');
    const pathMod = require('path');
    const url = `https://open.feishu.cn/open-apis/drive/v1/medias/${fileToken}/download`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    if (!res.ok) throw new Error(`下载附件失败: HTTP ${res.status}`);
    const buffer = await res.buffer();
    fs.mkdirSync(pathMod.dirname(savePath), { recursive: true });
    fs.writeFileSync(savePath, buffer);
    return savePath;
  }

  async downloadUrl(fileUrl, savePath) {
    const fs = require('fs');
    const pathMod = require('path');
    const res = await fetch(fileUrl);
    if (!res.ok) throw new Error(`下载 URL 失败: HTTP ${res.status}`);
    const buffer = await res.buffer();
    fs.mkdirSync(pathMod.dirname(savePath), { recursive: true });
    fs.writeFileSync(savePath, buffer);
    return savePath;
  }

  // 把配置的列按“连续区间”分组（如 C/D/E/F 一组、H 单独一组），
  // 写入时逐组各写各的区间，绝不碰中间未用的列（如 G）。
  _columnGroups() {
    if (!this._colGroupsCache) {
      const entries = Object.entries(this.writeColumns)
        .map(([field, col]) => ({ field, num: this._colToNum(col) }))
        .sort((a, b) => a.num - b.num);
      const groups = [];
      for (const e of entries) {
        const last = groups[groups.length - 1];
        if (last && e.num === last.endNum + 1) { last.endNum = e.num; last.fields.push(e.field); }
        else groups.push({ startNum: e.num, endNum: e.num, fields: [e.field] });
      }
      this._colGroupsCache = groups;
    }
    return this._colGroupsCache;
  }

  _valueOf(r, field) {
    const MAX_CELL = 45000; // 飞书单元格约 5 万字符上限
    switch (field) {
      case 'conversationShareLink': return r.shareLink || '';
      case 'artifactShareLink': return r.artifactShareLink || '';
      case 'duration': return r.reportedDuration || '';
      case 'beanCost': return r.beanCost || '';
      case 'cost': return r.cost || r.costRaw || '';
      case 'answer': return (r.answer || '').slice(0, MAX_CELL);
      case 'account': return r.account || '';
      default: return '';
    }
  }

  _rowOf(r) {
    if (r.row) return r.row;
    const m = /(\d+)\s*$/.exec(r.caseId || '');
    return m ? parseInt(m[1], 10) : null;
  }

  // 一条结果 -> 若干 valueRange（每个连续列组一个区间，中间空列不写）
  _valueRangesForRow(r, row) {
    return this._columnGroups().map(g => ({
      range: `${this.sheetId}!${this._numToCol(g.startNum)}${row}:${this._numToCol(g.endNum)}${row}`,
      values: [g.fields.map(f => this._valueOf(r, f))]
    }));
  }

  // 逐个区间写入（沿用已验证可用的单区间 PUT /values 接口；区间外单元格不受影响）。
  // 长时间运行后 tenant_access_token 可能失效（99991663/61/68），命中则刷新 token 重试一次。
  async _writeRanges(valueRanges) {
    const url = `https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/${this.spreadsheetToken}/values`;
    const TOKEN_ERR = new Set([99991663, 99991661, 99991668]);
    for (const vr of valueRanges) {
      const doPut = async () => {
        const res = await fetch(url, {
          method: 'PUT',
          headers: { 'Authorization': `Bearer ${this.token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ valueRange: vr })
        });
        return res.json();
      };
      let data = await doPut();
      if (TOKEN_ERR.has(data.code)) {
        this.token = null;
        await this.getAccessToken();
        data = await doPut();
      }
      if (data.code !== 0) throw new Error(`写回失败(${vr.range}): ${JSON.stringify(data)}`);
    }
  }

  // 回填单条结果（每完成一条立即调用）。按真实行号定位；非连续列(如 C:F 与 H)分区间写，不动中间列(G)。
  async writeResult(result) {
    await this._ensureReady();
    const row = this._rowOf(result);
    if (!row) return { written: 0 };
    await this._writeRanges(this._valueRangesForRow(result, row));
    return { written: 1 };
  }

  // 批量回填（本地汇总备用）。逐条按真实行号写入，非连续列分区间，避免覆盖中间列。
  async writeResults(results) {
    await this._ensureReady();
    const valueRanges = [];
    for (const r of results) {
      const row = this._rowOf(r);
      if (!row) continue;
      valueRanges.push(...this._valueRangesForRow(r, row));
    }
    await this._writeRanges(valueRanges);
    return { written: results.length };
  }

  // 按行号写指定单元格（如 {C:'3644472828', G:'是'}）。非相邻列自动分区间写，
  // 中间未指定的列（如 C 与 G 之间的 D/E/F）不会被覆盖。
  async writeCells(row, colValues) {
    await this._ensureReady();
    if (!row) throw new Error('writeCells 需要有效行号');
    const entries = Object.entries(colValues)
      .map(([col, val]) => ({ col: String(col).toUpperCase(), num: this._colToNum(col), val: val == null ? '' : String(val) }))
      .sort((a, b) => a.num - b.num);
    if (entries.length === 0) return { written: 0 };
    const groups = [];
    for (const e of entries) {
      const last = groups[groups.length - 1];
      if (last && e.num === last.endNum + 1) { last.endNum = e.num; last.vals.push(e.val); last.cols.push(e.col); }
      else groups.push({ startNum: e.num, endNum: e.num, vals: [e.val], cols: [e.col] });
    }
    const valueRanges = groups.map(g => ({
      range: `${this.sheetId}!${this._numToCol(g.startNum)}${row}:${this._numToCol(g.endNum)}${row}`,
      values: [g.vals]
    }));
    await this._writeRanges(valueRanges);
    return { written: entries.length, row, cols: entries.map(e => e.col) };
  }
}

module.exports = FeishuSheetReader;
