'use strict';
const fetch = require('node-fetch');
const dns = require('node:dns').promises;
const net = require('node:net');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const MAX_BYTES = 20 * 1024 * 1024;

function publicAddress(address) {
  if (net.isIPv4(address)) {
    const [a, b] = address.split('.').map(Number);
    return !(a === 0 || a === 10 || a === 127 || a >= 224 || a === 169 && b === 254 || a === 172 && b >= 16 && b <= 31 || a === 192 && b === 168 || a === 100 && b >= 64 && b <= 127);
  }
  return net.isIPv6(address) && /^(2|3)/i.test(address) && !address.includes('.');
}

async function safeUrl(raw) {
  const url = new URL(raw);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('不支持的产物地址');
  const host = url.hostname.replace(/^\[|\]$/g, '');
  const addresses = net.isIP(host) ? [{ address: host }] : await dns.lookup(host, { all: true });
  if (!addresses.length || addresses.some(a => !publicAddress(a.address))) throw new Error('产物地址不是公开文件服务');
  return url;
}

async function download(page, raw, timeout = 15000) {
  let url = await safeUrl(raw);
  const deadline = Date.now() + timeout;
  for (let redirect = 0; redirect < 4; redirect++) {
    const cookies = await page.context().cookies(url.href);
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('文件下载超时');
    const response = await fetch(url.href, { redirect: 'manual', size: MAX_BYTES, timeout: remaining,
      headers: cookies.length ? { Cookie: cookies.map(c => `${c.name}=${c.value}`).join('; ') } : {} });
    if (response.status >= 300 && response.status < 400) {
      response.body?.destroy();
      url = await safeUrl(new URL(response.headers.get('location'), url).href);
      continue;
    }
    if (!response.ok) { response.body?.destroy(); throw new Error(`文件服务返回 HTTP ${response.status}`); }
    if (Number(response.headers.get('content-length')) > MAX_BYTES) { response.body?.destroy(); throw new Error('产物超过20MB上限'); }
    const data = await response.buffer();
    return { data, contentType: response.headers.get('content-type') || '' };
  }
  throw new Error('文件重定向次数过多');
}

function filename(candidate) {
  const name = String(candidate.name || '').trim().split(/[\\/]/).pop();
  if (name && /\.(xlsx|docx|pptx|pdf|txt|md|csv|tsv|json|html)$/i.test(name)) return name;
  try { return decodeURIComponent(new URL(candidate.url).pathname.split('/').pop()); } catch { return name || 'artifact'; }
}

async function candidatesFromPage(page, selectors = {}) {
  const found = [];
  for (const frame of page.frames()) {
    const group = frame.locator(selectors.answerGroupSelector || selectors.answerSelector || '.chat-group.assistant').last();
    if (!await group.count()) continue;
    const links = await group.locator('a[href]').evaluateAll(els => els.map(el => ({
      url: el.href, name: el.getAttribute('download') || el.textContent.trim(), download: el.hasAttribute('download')
    })));
    found.push(...links.filter(a => a.download || /\.(xlsx|docx|pptx|pdf|txt|md|csv|tsv|json|html)(?:[?#]|$)/i.test(a.url) || /\.(xlsx|docx|pptx|pdf|txt|md|csv|tsv|json|html)$/i.test(a.name)));
  }
  return found;
}

async function collectArtifacts({ page, trace, rules, client, runId, selectors, downloadFile = download }) {
  if (!Array.isArray(rules) || !rules.length) return;
  const diagnostics = [], captured = [];
  const candidates = [...(trace.artifacts || []).map(a => ({ name: a.name, url: a.download_url || a.file_url || a.url }))];
  try { candidates.push(...await candidatesFromPage(page, selectors)); }
  catch { diagnostics.push({ status: 'unknown', reason: '无法读取当前回答的文件链接' }); }
  const seen = new Set();
  const started = Date.now();
  for (const candidate of candidates) {
    if (!candidate.url || seen.has(candidate.url)) continue;
    if (seen.size >= 10 || Date.now() - started > 60000) { diagnostics.push({ status: 'unknown', reason: '已到本轮产物采集数量或时间上限' }); break; }
    seen.add(candidate.url);
    const name = filename(candidate);
    try {
      const { data, contentType } = await downloadFile(page, candidate.url, Math.min(15000, 60000 - (Date.now() - started)));
      if (/text\/html/i.test(contentType) && !/\.html$/i.test(name)) throw new Error('文件链接返回网页，未取得实际产物');
      const result = await client.uploadArtifact(runId, name, data);
      captured.push(result);
    } catch (error) {
      diagnostics.push({ name, status: 'unknown', reason: error.type === 'max-size' ? '产物超过20MB上限' : String(error.message).replace(/https?:\/\/\S+/g, '[文件地址]').slice(0, 300) });
    }
  }
  trace.captured_artifacts = captured;
  trace.artifact_capture = { status: captured.length ? 'captured' : 'unknown', diagnostics };
}

function runnerMetadata() {
  const hash = crypto.createHash('sha256');
  for (const name of ['dialog-runner.js', 'desktop-runner.js', 'workbuddy-runner.js', 'dialog-config.js', 'artifact-collector.js']) hash.update(fs.readFileSync(path.join(__dirname, name)));
  return { runner_hash: hash.digest('hex'), node_version: process.version, platform: process.platform };
}

module.exports = { collectArtifacts, candidatesFromPage, download, safeUrl, publicAddress, filename, runnerMetadata };
