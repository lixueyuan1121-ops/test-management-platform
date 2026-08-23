// 批量录制账号登录态（CDP 连独立 Chrome）。配置见 config/default.config.js 的 accountRecording 节。
//
// 做什么：从「账号台账表」读未录制（statusCol ≠ 是）的账号 → 逐个用账号密码登录 work.n.cn
//   （风控偶发弹图形验证码时用离线 ddddocr 自动识别，无需人工）→ 成功后存 accounts/<qid>.json、
//   台账回填 recordedQidCol=实际 qid、statusCol=是。幂等：已录（台账=是 或 本地已有文件）自动跳过。
//
// 用法：
//   node bin/record-accounts.js                 # 全量（从飞书台账自取清单，跳过已录）
//   node bin/record-accounts.js --limit 3       # 只录前 3 个未录的（试跑）
//   node bin/record-accounts.js --passes 3      # 整批重跑 3 轮（捡回验证码卡壳的）
//   node bin/record-accounts.js --from 100      # 只处理台账行号 ≥100 的
//   node bin/record-accounts.js --delay 30000   # 账号间隔 30s（慢速，尽量绕开风控验证码）
//   node bin/record-accounts.js --file x.json   # 用本地 JSON 清单代替飞书（[{row,qid,phone,pwd}...]）
//   node bin/record-accounts.js --config <path> # 换配置文件
'use strict';
// 轻量加载 .env（项目无 dotenv）——飞书凭证 FEISHU_APP_ID / FEISHU_APP_SECRET
try { for (const line of require('fs').readFileSync('.env', 'utf8').split(/\r?\n/)) { const m = /^\s*([\w.]+)\s*=\s*(.*)\s*$/.exec(line); if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, ''); } } catch {}
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const FeishuSheetReader = require('../src/feishu-sheet');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const argVal = (name, def) => { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : def; };

// ---- 载入配置（accountRecording 节）+ 命令行覆盖 ----
const CONFIG = require(path.resolve(argVal('--config', './config/default.config.js')));
const R = Object.assign({
  startRow: 2, endRow: 400, qidCol: 'D', phoneCol: 'E', passwordCol: 'F', statusCol: 'G', recordedQidCol: 'C', doneValue: '是',
  cdpUrl: 'http://127.0.0.1:9333', chromePath: '', chromeUserDataDir: '', pythonPath: '',
  maxCaptcha: 14, cooldownEvery: 6, cooldownMs: 25000, baseGapMs: 4000, passes: 1, passCooldownSec: 60
}, CONFIG.accountRecording || {});
if (!R.url) { console.error('❌ 未配置 accountRecording.url（账号台账表链接）'); process.exit(1); }

const ACCOUNTS_DIR = 'accounts';
const LAUNCHER_URL = (CONFIG.platform && CONFIG.platform.chatUrl) || 'https://work.n.cn/launcher';
const MAX_CAPTCHA = R.maxCaptcha;
const COOLDOWN_EVERY = R.cooldownEvery;
const COOLDOWN_MS = R.cooldownMs;
const LIMIT = parseInt(argVal('--limit', '0'), 10) || 0;
const FROM = parseInt(argVal('--from', '0'), 10) || 0;
const PASSES = parseInt(argVal('--passes', String(R.passes)), 10) || 1;
const PASS_COOLDOWN_MS = parseInt(argVal('--pass-cooldown', String(R.passCooldownSec)), 10) * 1000;
const BASE_GAP_MS = parseInt(argVal('--delay', String(R.baseGapMs)), 10);
const PENDING_FILE = argVal('--file', '');

// ---- Python 解释器：配置 > 环境 > PATH 的 python > 本机 openclaw 内置 ----
function resolvePython() {
  const cands = [R.pythonPath, process.env.PYTHON, 'python', 'C:/Users/Administrator/.openclaw/skills/Python313/Python313/python.exe', 'python3'].filter(Boolean);
  for (const c of cands) {
    try { if (/[\\/]/.test(c) && !fs.existsSync(c)) continue; execFileSync(c, ['--version'], { stdio: 'ignore' }); return c; } catch {}
  }
  return null;
}

// ---- 独立 Chrome：端口就绪直接用；否则用 chromePath/常见路径带调试端口拉起 ----
function findChrome() {
  const cands = [R.chromePath,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    process.env.LOCALAPPDATA ? process.env.LOCALAPPDATA.replace(/\\/g, '/') + '/Google/Chrome/Application/chrome.exe' : ''
  ].filter(Boolean);
  for (const c of cands) if (fs.existsSync(c)) return c;
  return '';
}
function portProbe(host, port) {
  return new Promise(res => { const req = http.get(`http://${host}:${port}/json/version`, { timeout: 2500 }, r => { r.on('data', () => {}); r.on('end', () => res(true)); }); req.on('error', () => res(false)); req.on('timeout', () => { req.destroy(); res(false); }); });
}
async function ensureChrome() {
  const m = String(R.cdpUrl).match(/^https?:\/\/([^:/]+):(\d+)/);
  const host = m ? m[1] : '127.0.0.1', port = m ? parseInt(m[2], 10) : 9333;
  if (await portProbe(host, port)) return R.cdpUrl;
  const chrome = findChrome();
  if (!chrome) throw new Error(`Chrome 调试端口 ${R.cdpUrl} 未就绪，且未找到 chrome.exe。请在 config.accountRecording.chromePath 指定，或先手动启动：\n  chrome.exe --remote-debugging-port=${port} --user-data-dir="%TEMP%\\chrome-eval-record"`);
  const udd = R.chromeUserDataDir || path.join(os.tmpdir(), 'chrome-eval-record');
  console.log(`调试端口未就绪，自动拉起独立 Chrome：${chrome}  (userDataDir=${udd})`);
  const child = spawn(chrome, [`--remote-debugging-port=${port}`, `--user-data-dir=${udd}`], { detached: true, stdio: 'ignore' });
  child.unref();
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) { if (await portProbe(host, port)) return R.cdpUrl; await sleep(700); }
  throw new Error(`已尝试拉起 Chrome，但端口 ${R.cdpUrl} 仍未就绪`);
}

// ---- 从飞书台账读「未录制」清单：[{row,qid,phone,pwd}] ----
async function buildPendingFromFeishu(reader) {
  await reader._ensureReady();
  const nums = [R.recordedQidCol, R.qidCol, R.phoneCol, R.passwordCol, R.statusCol].map(c => reader._colToNum(c));
  const lo = Math.min(...nums), hi = Math.max(...nums);
  const rows = await reader.readRange(`${reader.sheetId}!${reader._numToCol(lo)}${R.startRow}:${reader._numToCol(hi)}${R.endRow}`);
  const t = c => reader._cellToText(c).trim();
  const at = (row, col) => t(row[reader._colToNum(col) - lo]);
  const out = [];
  rows.forEach((row, i) => {
    const phone = at(row, R.phoneCol), pwd = at(row, R.passwordCol);
    if (!phone || !pwd) return;                          // 无账号密码的行跳过
    if (at(row, R.statusCol) === R.doneValue) return;    // 台账已标记「是」跳过
    out.push({ row: i + R.startRow, qid: at(row, R.qidCol), phone, pwd });
  });
  return out;
}

// ---- ddddocr 常驻解码器（行协议）----
function startSolver(pyPath) {
  const p = spawn(pyPath, [path.join(__dirname, '..', 'tools', 'solve_captcha.py')], { stdio: ['pipe', 'pipe', 'pipe'] });
  const queue = []; let buf = '';
  p.stdout.on('data', d => { buf += d.toString(); let i; while ((i = buf.indexOf('\n')) >= 0) { const l = buf.slice(0, i); buf = buf.slice(i + 1); const cb = queue.shift(); if (cb) cb(l); } });
  let rr, rj; const ready = new Promise((r, j) => { rr = r; rj = j; });
  p.stderr.on('data', d => { const s = d.toString(); if (/READY/.test(s)) rr(); if (/IMPORT_FAIL/.test(s)) rj(new Error('ddddocr 加载失败（需 pip install ddddocr onnxruntime==1.20.0）: ' + s)); });
  p.on('exit', c => { if (c) rj(new Error('solver 退出码 ' + c)); });
  return { ready, solve: b64 => new Promise(res => { queue.push(res); p.stdin.write(String(b64).replace(/\r?\n/g, '') + '\n'); }), quit: () => { try { p.stdin.write('__QUIT__\n'); } catch {} } };
}

// ---- 页面工具 ----
async function frameHaving(page, sel) { for (const f of page.frames()) { const ok = await f.evaluate(s => !!document.querySelector(s), sel).catch(() => false); if (ok) return f; } return null; }
async function clickText(page, txt) { for (const f of page.frames()) { const ok = await f.evaluate(t => { const e = [...document.querySelectorAll('*')].find(x => x.childElementCount === 0 && x.innerText && x.innerText.trim() === t); if (e) { e.click(); return true; } return false; }, txt).catch(() => false); if (ok) return true; } return false; }
async function anyFrameHasText(page, txt) { for (const f of page.frames()) { const o = await f.evaluate(t => [...document.querySelectorAll('*')].some(e => e.childElementCount === 0 && e.innerText && e.innerText.trim() === t), txt).catch(() => false); if (o) return true; } return false; }
async function qidFromCookies(ctx) {
  const cookies = await ctx.cookies();
  for (const c of cookies) { if (c.name === 'qid' && c.value) return c.value; }
  for (const c of cookies) { const v = decodeURIComponent(c.value || ''); const m = v.match(/qid=([^&;]+)/); if (m) return m[1]; }
  return null;
}
// 成功 = qid cookie 出现 且 登录弹窗（账号登录 tab 文本）已消失
async function checkSuccess(ctx, page) {
  const qid = await qidFromCookies(ctx);
  if (!qid) return null;
  if (await anyFrameHasText(page, '账号登录')) return null;
  return qid;
}

// 说明：填 React 受控 input 用「原生 value setter + 派发 input/change 事件」，各 evaluate 内联，不用 eval。

// 清空登录态，回到干净登录页
async function resetToLogin(ctx, page) {
  await ctx.clearCookies().catch(() => {});
  try { await page.evaluate(() => { try { localStorage.clear(); sessionStorage.clear(); } catch {} }); } catch {}
  await page.goto(LAUNCHER_URL, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await sleep(5000);
}

// 录制单个账号，返回 {ok, qid, reason}
async function recordOne(ctx, page, solver, acc, log) {
  await resetToLogin(ctx, page);
  if (await qidFromCookies(ctx)) { // 清态后仍有 qid：异常，再清一次
    await resetToLogin(ctx, page);
  }
  await clickText(page, '个人版'); await sleep(400);
  const tab = await clickText(page, '账号登录'); await sleep(1200);
  if (!tab) log('  ⚠️ 未点到「账号登录」tab（可能已在该 tab）');

  const ff = await frameHaving(page, 'input[name="userName"]') || await frameHaving(page, 'input[name="password"]');
  if (!ff) return { ok: false, reason: 'no_form' };
  await ff.evaluate(({ phone, pwd }) => {
    const set = (el, v) => { if (!el) return; const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; s.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); };
    set(document.querySelector('input[name="userName"]'), phone);
    set(document.querySelector('input[name="password"]'), pwd);
    const ag = document.querySelector('input[name="is_agree"]') || document.querySelector('input[type="checkbox"]'); if (ag && !ag.checked) ag.click();
  }, { phone: acc.phone, pwd: acc.pwd });
  await sleep(400);

  const clickLogin = () => ff.evaluate(() => { const b = document.querySelector('button[type="submit"],input[type="submit"]') || [...document.querySelectorAll('button')].find(x => /登\s*录/.test(x.innerText || '')); if (b) { b.click(); return true; } return false; });
  const capState = () => ff.evaluate(() => { const img = document.querySelector('img.quc-captcha-img'); if (!img) return { exists: false }; const r = img.getBoundingClientRect(); return { exists: true, vis: r.width > 8 && r.height > 8 }; }).catch(() => ({ exists: false }));
  const clickRefresh = () => ff.evaluate(() => { const el = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 && /换一张|看不清|刷新/.test(e.innerText || '')); if (el) { el.click(); return true; } const img = document.querySelector('img.quc-captcha-img'); if (img) { img.click(); return true; } return false; });
  const errText = () => ff.evaluate(() => { const t = document.body.innerText || ''; if (/密码错误|账号或密码|用户名或密码|账号不存在/.test(t)) return 'password'; if (/验证码错误|验证码有误|图形验证码错误/.test(t)) return 'captcha'; return ''; }).catch(() => '');

  await clickLogin();
  log('  已提交账号密码，等待结果...');

  // 主循环：等成功 / 验证码 / 密码错误
  for (let attempt = 0; attempt <= MAX_CAPTCHA; attempt++) {
    // 等 ~8s：成功？验证码可见？
    let st = { exists: false };
    for (let t = 0; t < 16; t++) {
      const qid = await checkSuccess(ctx, page);
      if (qid) return { ok: true, qid };
      st = await capState();
      if (st.exists && st.vis) break;
      const e = await errText();
      if (e === 'password') return { ok: false, reason: 'bad_password' };
      await sleep(500);
    }
    if (!(st.exists && st.vis)) {
      const qid = await checkSuccess(ctx, page);
      if (qid) return { ok: true, qid };
      const e = await errText();
      if (e === 'password') return { ok: false, reason: 'bad_password' };
      return { ok: false, reason: 'no_captcha_no_success' };
    }
    if (attempt === MAX_CAPTCHA) break;
    // 识别验证码（图+识别结果存 output/_caplog 便于诊断）
    const h = await ff.$('img.quc-captcha-img');
    if (!h) return { ok: false, reason: 'captcha_gone' };
    const shot = await h.screenshot({ type: 'png' });
    const guess = (await solver.solve(shot.toString('base64'))).replace(/[^0-9a-zA-Z]/g, '');
    log(`  验证码#${attempt + 1} 识别="${guess}"`);
    try { fs.mkdirSync('output/_caplog', { recursive: true }); fs.writeFileSync(`output/_caplog/${acc.qid || 'na'}_${attempt + 1}_${guess || 'NA'}.png`, shot); } catch {}
    // 验证码长度不固定（实测有 4 位也有 5 位）。只丢弃明显数错的（<4 或 >6），4~6 位都提交——
    // 之前硬性「只提交4位」会把正确的 5 位码当数错丢弃，导致 5 位码账号永远登不上。
    if (guess.length < 4 || guess.length > 6) { await clickRefresh(); await sleep(1200); continue; }
    await ff.evaluate((g) => { const el = document.querySelector('input[name="captcha"]'); if (!el) return; const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; s.call(el, g); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }, guess);
    await sleep(300);
    await clickLogin();
    // 提交后轮询最多 ~8s 等「成功/密码错/验证码错」，不再只等 2.5s 就盲目刷新
    // （修复：登录未返回时误判失败并「换一张」，会把正确验证码也刷掉）
    for (let t = 0; t < 16; t++) {
      await sleep(500);
      const qid = await checkSuccess(ctx, page);
      if (qid) return { ok: true, qid };
      const e = await errText();
      if (e === 'password') return { ok: false, reason: 'bad_password' };
      if (e === 'captcha') break; // 明确验证码错，跳出去换一张
    }
    await clickRefresh(); await sleep(1200);
  }
  return { ok: false, reason: 'captcha_exhausted' };
}

(async () => {
  fs.mkdirSync(ACCOUNTS_DIR, { recursive: true });
  const isDone = a => !!a.qid && fs.existsSync(path.join(ACCOUNTS_DIR, a.qid + '.json'));

  // 飞书 reader（回填 + 自取清单都用它）
  const reader = new FeishuSheetReader({ url: R.url, startRow: R.startRow, endRow: R.endRow });

  // 待录清单：默认从飞书台账自取；--file 指定则用本地 JSON
  let allPending;
  if (PENDING_FILE) {
    allPending = JSON.parse(fs.readFileSync(PENDING_FILE, 'utf8'));
    console.log(`清单来源：本地文件 ${PENDING_FILE}（${allPending.length} 条）`);
  } else {
    console.log('从飞书台账读取待录清单...');
    allPending = await buildPendingFromFeishu(reader);
    console.log(`清单来源：飞书台账 ${R.url}（未录 ${allPending.length} 条）`);
  }
  if (FROM) allPending = allPending.filter(a => a.row >= FROM);
  if (!allPending.length) { console.log('没有需要录制的账号（台账里 ' + R.statusCol + ' 列都已=' + R.doneValue + '，或无有效账号密码）。'); return; }

  // Python + ddddocr
  const py = resolvePython();
  if (!py) { console.error('❌ 未找到可用的 Python。请在 config.accountRecording.pythonPath 指定，或把 python 加入 PATH。'); process.exit(1); }
  const solver = startSolver(py);
  console.log(`加载 ddddocr 验证码模型（python=${py}）...`);
  await solver.ready;
  console.log('ddddocr 就绪');

  // Chrome（连已开的调试端口，或自动拉起）
  const cdpUrl = await ensureChrome();
  const browser = await chromium.connectOverCDP(cdpUrl);
  const ctx = browser.contexts()[0];
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  const page = ctx.pages()[0] || await ctx.newPage();

  const allResults = [];
  for (let pass = 1; pass <= PASSES; pass++) {
    const todo = allPending.filter(a => !isDone(a));
    const batch = LIMIT ? todo.slice(0, LIMIT) : todo;
    console.log(`\n===== 第 ${pass}/${PASSES} 轮：清单 ${allPending.length}，已录 ${allPending.length - todo.length}，本轮处理 ${batch.length} =====`);
    if (!batch.length) { console.log('全部已录，提前结束。'); break; }

    for (let i = 0; i < batch.length; i++) {
      const acc = batch[i];
      console.log(`[${pass}轮 ${i + 1}/${batch.length}] 行${acc.row} qid=${acc.qid || '(空)'} phone=${acc.phone}`);
      const log = m => console.log(m);
      let res;
      try { res = await recordOne(ctx, page, solver, acc, log); }
      catch (e) { res = { ok: false, reason: 'exception:' + e.message.split('\n')[0] }; }

      if (res.ok) {
        const qid = res.qid;
        if (acc.qid && qid !== acc.qid) console.log(`  ⚠️ 登录 qid=${qid} 与台账 qid=${acc.qid} 不一致（按实际 qid 记录）`);
        await ctx.storageState({ path: path.join(ACCOUNTS_DIR, qid + '.json') });
        try {
          await reader.writeCells(acc.row, { [R.recordedQidCol]: qid, [R.statusCol]: R.doneValue });
          console.log(`  ✅ 存 accounts/${qid}.json，回填行${acc.row} ${R.recordedQidCol}=${qid},${R.statusCol}=${R.doneValue}`);
          allResults.push({ ...acc, pass, ok: true, qid });
        } catch (e) {
          console.log(`  ⚠️ 登录态已存但飞书回填失败：${e.message.split('\n')[0]}`);
          allResults.push({ ...acc, pass, ok: true, qid, feishu: 'fail' });
        }
      } else {
        console.log(`  ❌ 失败：${res.reason}`);
        allResults.push({ ...acc, pass, ok: false, reason: res.reason });
      }
      // 账号间隔：基础(可配 --delay) + 抖动 0~3s；每 COOLDOWN_EVERY 个额外冷却，降低风控触发验证码
      if (i < batch.length - 1) {
        let gap = BASE_GAP_MS + Math.floor(Math.random() * 3000);
        if ((i + 1) % COOLDOWN_EVERY === 0) { gap += COOLDOWN_MS; console.log(`  ...冷却 ${Math.round(gap / 1000)}s（每 ${COOLDOWN_EVERY} 个）`); }
        await sleep(gap);
      }
    }
    const stillLeft = allPending.filter(a => !isDone(a)).length;
    if (pass < PASSES && stillLeft) { console.log(`\n本轮后仍有 ${stillLeft} 个未录，轮间冷却 ${PASS_COOLDOWN_MS / 1000}s...`); await sleep(PASS_COOLDOWN_MS); }
    if (!stillLeft) break;
  }

  // 总汇总（以 accounts/<qid>.json 是否存在为准）
  const doneCount = allPending.filter(a => isDone(a)).length;
  const failed = allPending.filter(a => !isDone(a));
  console.log(`\n====== 总汇总：已录 ${doneCount}/${allPending.length}，仍缺 ${failed.length} ======`);
  for (const a of failed) console.log(`  仍未录 行${a.row} qid=${a.qid || '(空)'} phone=${a.phone}`);
  fs.writeFileSync('output/_record_result.json', JSON.stringify({ done: doneCount, total: allPending.length, failedRows: failed.map(a => a.row), results: allResults }, null, 2));
  console.log('明细: output/_record_result.json');

  await browser.close();
  solver.quit();
})().catch(e => { console.error('FATAL:', e.message.split('\n')[0]); process.exit(1); });
