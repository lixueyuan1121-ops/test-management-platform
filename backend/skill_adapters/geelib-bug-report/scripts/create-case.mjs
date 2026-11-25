#!/usr/bin/env node
// create-case.mjs — 用极库云正确签名口径建/改缺陷 case,并把纯文本描述自动转成 HTML 分段。
// 为什么自带:geelib-project-skill 的 matter:create 签名有 bug(嵌套 data 报 4001);
// 且描述是富文本,纯文本换行会被 HTML 折叠成一句,必须用 <p> 分段。本脚本两点都已处理。
//
// 用法:
//   node create-case.mjs --title "标题" --content-file <纯文本文件> \
//        --data '[{"cf_name":"执行人","cf_value":"x@360.cn"},{"cf_name":"优先级","cf_value":"mid"},{"cf_name":"状态","cf_value":"新建"}]'
//   # 编辑已有 case 的描述: 加 --id <matterId>(只传 --content* 即可只改描述)
//   # 直接给文本: --content "多行\n文本"(shell 里建议用 --content-file 避免转义)
//   # 已经是 HTML: --content-html "<p>...</p>"(不再自动转换)
//   # 只看不发: --dry-run
//
// 固定默认: --sub-id 7087  --type-id 缺陷 (可用同名参数覆盖)。
// 凭据: 读 ~/.geelib.json 的 userMail/userToken(与其它 geelib skill 一致)。

import crypto from 'node:crypto';
import os from 'node:os';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';

// ---------- 参数解析 ----------
function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (!k.startsWith('--')) continue;
    const key = k.slice(2);
    if (key === 'dry-run') { a.dryRun = true; continue; }
    a[key] = argv[++i];
  }
  return a;
}
// QALab 用 --platform-sso 从标准输入读取一次性个人授权，仍由本 skill 组装并提交缺陷。
const platformMode = process.argv[2] === '--platform-sso';
const platform = platformMode ? JSON.parse(fs.readFileSync(0, 'utf8')) : null;
const args = platformMode ? platform.args : parseArgs(process.argv.slice(2));
if (platformMode && (!platform.appToken || !Number.isSafeInteger(args['sub-id']) || args['sub-id'] <= 0 || !args.title))
  throw new Error('缺少个人授权、项目或缺陷标题');
const cfg = platformMode ? { apiUrl: platform.apiUrl } : JSON.parse(fs.readFileSync(os.homedir() + '/.geelib.json', 'utf8'));
const USER_MAIL = cfg.userMail, TOKEN = cfg.userToken;
const BASE = (cfg.apiUrl || 'https://geelib.qihoo.net').replace(/\/+$/, '');

// ---------- 正确签名(与极库云 Go 版一致):展开嵌套 → 全局按 key 排序 → Go 式编码 → 双次 md5 ----------
function goEnc(s) {
  return encodeURIComponent(String(s))
    .replace(/%20/g, '+')                                             // 空格→+
    .replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase()); // 转义 ()!*' ; 保留 ~
}
function flatten(obj, parentKey, out) {
  if (obj === null || obj === undefined) return;
  if (Array.isArray(obj)) { obj.forEach((v, i) => flatten(v, `${parentKey}[${i}]`, out)); return; }
  if (typeof obj === 'object') { for (const k of Object.keys(obj)) flatten(obj[k], parentKey ? `${parentKey}[${k}]` : k, out); return; }
  out.push([parentKey, String(obj)]);
}
function sign(params) {
  const flat = []; flatten(params, '', flat);
  flat.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));      // 全局排序(关键:非只排顶层)
  const canonical = flat.map(([k, v]) => `${goEnc(k)}=${goEnc(v)}`).join('&');
  const h1 = crypto.createHash('md5').update(canonical).digest('hex');
  return { sign: crypto.createHash('md5').update(h1 + TOKEN).digest('hex'), canonical };
}

// ---------- 纯文本 → 富文本 HTML 分段(关键:否则描述挤成一句) ----------
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function textToHtml(text) {
  return String(text).replace(/\r\n/g, '\n').split('\n')
    .map((line) => (line.trim() === '' ? '<p><br></p>' : `<p>${esc(line)}</p>`))
    .join('');
}

// ---------- 组装 content ----------
let content;
if (args['content-html'] != null) content = args['content-html'];           // 已是 HTML,原样
else if (args['content-file'] != null) content = textToHtml(fs.readFileSync(args['content-file'], 'utf8'));
else if (args['content'] != null) content = textToHtml(args['content']);

// ---------- 组装 params ----------
const params = {
  sub_id: Number(args['sub-id'] ?? 7087),
  type_id: args['type-id'] ?? '缺陷',
  t: Math.floor(Date.now() / 1000),
  user_mail: USER_MAIL,
};
if (args['id'] != null) params.id = Number(args['id']);      // 有 id = 编辑
if (args['title'] != null) params.title = args['title'];
if (content != null) params.content = content;
if (args['data'] != null) {
  try { params.data = JSON.parse(args['data']); }
  catch { console.error('--data 必须是合法 JSON'); process.exit(1); }
}

let canonical;
if (platformMode) {
  delete params.t;
  delete params.user_mail;
  params.mkd_content = args.content;
} else {
  const signed = sign(params);
  params.sign = signed.sign;
  canonical = signed.canonical;
}

if (args.dryRun) {
  const preview = { ...params }; delete preview.sign;
  console.error('[dry-run] 不发送。params(去 sign):');
  console.error(JSON.stringify(preview, null, 2));
  console.error('[dry-run] canonical 前 400 字:');
  console.error(canonical.slice(0, 400));
  console.log(JSON.stringify({ dryRun: true, endpoint: `${BASE}/openapi/Matter/add`, hasContent: content != null }));
  process.exit(0);
}

// ---------- 发送(建/改都走 Matter/add,带 id 即编辑) ----------
// The deployment host runs Node 16 without global fetch. Never follow a redirect with a personal token.
async function submit() {
  const url = new URL(`${BASE}/openapi/Matter/add`);
  const transport = { 'http:': http, 'https:': https }[url.protocol];
  if (!transport) throw new Error('unsupported protocol');
  const payload = JSON.stringify(params);
  return new Promise((resolve, reject) => {
    const req = transport.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload),
        ...(platformMode ? { 'X-Agent-Auth': `Bearer ${platform.appToken}` } : { 'From-App': USER_MAIL }) },
    }, res => {
      let text = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { text += chunk; });
      res.on('error', reject);
      res.on('end', () => {
        let body;
        try { body = JSON.parse(text); } catch { body = {}; }
        resolve({ http: res.statusCode, errno: body?.errno, errmsg: body?.errmsg, data: body?.data });
      });
    });
    const timer = setTimeout(() => req.destroy(new Error('receipt timeout')), 30000);
    req.on('close', () => clearTimeout(timer));
    req.on('error', reject);
    req.end(payload);
  });
}

try {
  const result = await submit();
  console.log(JSON.stringify(result));
  process.exitCode = result.errno === 2000 ? 0 : 1;
} catch (error) {
  // Only these failures prove that no connection reached the tracker.
  const notSent = ['ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN', 'ERR_INVALID_URL'].includes(error.code);
  console.log(JSON.stringify({ delivery: notSent ? 'not_sent' : 'unknown', code: error.code || 'NO_RECEIPT' }));
  process.exitCode = 1;
}
