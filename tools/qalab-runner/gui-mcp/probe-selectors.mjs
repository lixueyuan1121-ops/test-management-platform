// 选择器巡检 —— selectors.json 健康检查(只读)。复用 gui-core 定位引擎连纳米 Work(9222)。
//
// 用法(前提:纳米 Work 带 --remote-debugging-port=9222 且已登录):
//   node probe-selectors.mjs                 巡检所有 navMode=auto 的页
//   node probe-selectors.mjs --page experts  只巡某页(manual 页需你先手动切过去)
//   node probe-selectors.mjs --keys a,b,c    只探指定 key(在当前页)
//   node probe-selectors.mjs dump [--page X] 在(切到)某页 dump 全部可交互控件+候选,供指认失效 key 的新候选
//
// 只读:绝不改 selectors.json。发现失效/冗余后,由你拍板要改什么,再单独写回(备份)。
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createGuiCore } from './gui-core.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const SELECTORS = join(HERE, 'selectors.json');
const MANIFEST = join(HERE, 'probe-manifest.json');

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t === '--page') a.page = argv[++i];
    else if (t === '--keys') a.keys = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
    else a._.push(t);
  }
  return a;
}

const args = parseArgs(process.argv.slice(2));
const cmd = args._[0] === 'dump' ? 'dump' : 'scan';
const manifest = JSON.parse(readFileSync(MANIFEST, 'utf-8'));
const registry = JSON.parse(readFileSync(SELECTORS, 'utf-8')).registry;
const allKeys = new Set(Object.keys(registry));

const core = createGuiCore({ cdpUrl: 'http://127.0.0.1:9222', selectorsPath: SELECTORS, timeout: 2000 });

// 连接(顺带确认 9222 + 登录态)
let conn;
try { conn = await core.connect(); }
catch (e) { console.error('✗ 连不上纳米 Work(9222):', e.message, '\n  请确认纳米 Work 带 --remote-debugging-port=9222 启动、且已登录。'); process.exit(1); }
console.log('连接:', conn.url, '| in_iframe=' + conn.in_iframe, '\n');

async function runNav(steps) {
  for (const s of steps) {
    await core.click(s.key ? { key: s.key } : { selector: s.selector });
    await new Promise((r) => setTimeout(r, 2500));
  }
}

if (cmd === 'dump') {
  const page = manifest.pages.find((p) => p.page === args.page);
  if (page && page.nav.length) { try { await runNav(page.nav); } catch (e) { console.log('(导航失败,就地 dump)', e.message); } }
  const res = await core.probe({ limit: 60 });
  for (const g of res.groups) {
    console.log(`=== [${g.frame}] ${g.url?.slice(0, 60)} — ${g.total ?? (g.elements || []).length} 个控件 ===`);
    for (const el of g.elements || []) {
      console.log(`  <${el.tag}${el.type ? ' type=' + el.type : ''}> "${el.text}"  best=${el.best?.by}:${el.best?.value}`);
    }
  }
  await core.close?.();
  process.exit(0);
}

// ---- scan ----
// --keys:只在当前页探指定 key(不导航)
if (args.keys) {
  console.log('指定 key 探测(当前页):');
  for (const k of args.keys) {
    if (!allKeys.has(k)) { console.log(`  ${k}: ✗ selectors.json 无此 key`); continue; }
    const r = await core.assertVisible({ key: k });
    console.log(`  ${k}: ${r.pass ? '✓ 命中' : '⚠ 未命中'}`);
  }
  await core.close?.();
  process.exit(0);
}

const pages = args.page ? manifest.pages.filter((p) => p.page === args.page) : manifest.pages.filter((p) => p.navMode === 'auto');

// full scan(非 --page)前尽力回主界面,消除"上次停在 creator 等深层页"的干扰;--page 时尊重你手动切的页
if (!args.page) {
  try { await core.click({ key: 'creatorBack' }); await new Promise((r) => setTimeout(r, 1200)); } catch { /* 不在 creator 页 */ }
  try { await core.click({ key: 'navTasks' }); await new Promise((r) => setTimeout(r, 2000)); }
  catch { console.log('(提示:未能自动回主界面,shell/chat 结果可能受当前页影响;可手动切到首页再跑)\n'); }
}

const covered = new Set();
// 覆盖集先纳入 manifest 全部页(含 manual 页)+ manual 桶,避免 navMode=manual 的页/特殊态 key 被误报"未归类"
for (const p of manifest.pages) { p.staticKeys.forEach((k) => covered.add(k)); p.triggeredKeys.forEach((k) => covered.add(k)); }
for (const arr of Object.values(manifest.manual)) if (Array.isArray(arr)) arr.forEach((k) => covered.add(k));
let totalStatic = 0, totalHit = 0;
const failedAll = [];

for (const page of pages) {
  // manual 页仅在 --page 指定时探(假定你已手动切过去);auto 页执行导航
  if (page.navMode === 'auto' && page.nav.length) {
    try { await runNav(page.nav); }
    catch (e) { console.log(`【${page.page}】${page.title}\n  ✗ 无法进入该页(导航失败: ${e.message}),跳过\n`); continue; }
  }
// manual 页 nav 为空、又不在 --page 指定时,跳过(避免在错误页面误报失效)
  if (page.navMode === 'manual' && !args.page) continue;
  const hit = [], failed = [];
  for (const k of page.staticKeys) {
    const r = await core.assertVisible({ key: k });
    (r.pass ? hit : failed).push(k);
  }
  totalStatic += page.staticKeys.length; totalHit += hit.length;
  failed.forEach((k) => failedAll.push(`${page.page}/${k}`));
  console.log(`【${page.page}】${page.title}`);
  console.log(`  静态 ${page.staticKeys.length}  命中 ${hit.length}  ⚠失效 ${failed.length}${failed.length ? ': ' + failed.join(', ') : ''}`);
  console.log(`  (跳过 ${page.triggeredKeys.length} 个待触发项)\n`);
}

// manual 桶的 key 也算已覆盖(已知特殊态)
for (const arr of Object.values(manifest.manual)) if (Array.isArray(arr)) arr.forEach((k) => covered.add(k));

// 汇总
console.log('══════════ 汇总 ══════════');
console.log(`静态 key 命中率:${totalHit}/${totalStatic}`);
console.log(`⚠ 失效(该有却没有,需修/删):${failedAll.length}${failedAll.length ? '\n  ' + failedAll.join('\n  ') : ''}`);

// 冗余检测:selectors.json 里有、但 manifest 完全没覆盖(既不在任何页也不在 manual)→ 可疑冗余
const orphan = [...allKeys].filter((k) => !covered.has(k));
console.log(`\n🔵 未归类 key(不在 manifest 任何桶,可能是冗余或漏配 manifest):${orphan.length}${orphan.length ? '\n  ' + orphan.join(', ') : ''}`);
console.log('\n提示:失效项→ dump 该页找新候选;冗余项→ 你确认后我删。改 selectors.json 由你拍板,我改前列 diff。');

await core.close?.();
process.exit(0);
