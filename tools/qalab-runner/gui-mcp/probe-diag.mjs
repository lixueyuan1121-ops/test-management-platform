// 探测诊断 —— 定位"某元素探不到"卡在哪一层(只读，不改任何东西)。
//
// 前提：被测客户端/Chrome 已带 --remote-debugging-port=9222 启动、停在目标页面、已登录。
// 用法：
//   node probe-diag.mjs --grep 导出            按可见文本/aria/placeholder 子串找目标，逐 frame 报告
//   node probe-diag.mjs --grep 导出 --html     额外打印命中元素的 outerHTML 头 200 字(看它是什么控件)
//   node probe-diag.mjs                         不给 grep：只打印每 frame 的 原始/白名单/放宽 三档计数
//
// 它对每个 frame 各跑一次自包含的诊断脚本，回答四个问题：
//   1) 原始 DOM(穿 open shadowRoot)里有没有这个元素   → 没有＝闭合 shadow / 不在此 frame / 别的页
//   2) 白名单模式(= 平台「探测」按钮)收不收             → 不收＝自定义控件，需框选
//   3) 放宽模式(= 平台「框选探测」)收不收               → 不收＝下面 why 给原因
//   4) 收不到时 why：invisible / no-candidates / not-in-whitelist
import { chromium } from 'playwright-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
function arg(name) { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : undefined; }
const GREP = arg('--grep') || '';
const SHOW_HTML = process.argv.includes('--html');

const DIAG = function ({ grep }) {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const isBEM = (cls) => /^[a-z][a-z0-9]*(?:[-_]{1,2}[a-z0-9]+)+$/i.test(cls);
  const isHash = (cls) => /[A-Za-z0-9]{6,}$/.test(cls) && !/[-_]/.test(cls.slice(-8));
  const genCandidates = (el) => {
    const cands = [];
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test');
    if (testid) cands.push({ by: 'testid', value: testid });
    if (el.id && !/^\d/.test(el.id) && el.id.length < 50) cands.push({ by: 'css', value: '#' + el.id });
    const aria = el.getAttribute('aria-label');
    if (aria && aria.length < 60) cands.push({ by: 'label', value: aria });
    const name = el.getAttribute('name');
    if (name) cands.push({ by: 'css', value: '[name="' + name + '"]' });
    const ph = el.getAttribute('placeholder');
    if (ph) cands.push({ by: 'placeholder', value: ph });
    const classes = Array.from(el.classList);
    const bem = classes.filter(isBEM);
    const stable = bem.length ? bem : classes.filter((c) => !isHash(c) && c.length > 3);
    if (stable.length) cands.push({ by: 'css', value: stable.map((c) => '.' + c).join('') });
    const txt = (el.innerText || el.textContent || '').trim().slice(0, 30);
    if (txt && txt.length >= 2 && txt.length <= 20) cands.push({ by: 'text', value: txt });
    const type = el.getAttribute('type');
    if (type) cands.push({ by: 'css', value: el.tagName.toLowerCase() + '[type="' + type + '"]' });
    return cands;
  };
  const collectDeep = (root) => {
    const acc = [];
    for (const el of root.querySelectorAll('*')) {
      acc.push(el);
      if (el.shadowRoot) for (const s of collectDeep(el.shadowRoot)) acc.push(s);
    }
    return acc;
  };
  const all = collectDeep(document);
  const WL = 'a, button, [role=button], [role=tab], [role=menuitem], input, textarea, select, [contenteditable=true], [onclick], [class*=btn], [class*=action], [class*=nav__item], [class*=menu-item]';
  const inWhitelist = (el) => { try { return el.matches(WL) || getComputedStyle(el).cursor === 'pointer'; } catch { return false; } };

  let whitelistCount = 0, relaxCount = 0;
  for (const el of all) {
    const vis = isVisible(el);
    const hasCand = genCandidates(el).length > 0;
    if (vis && hasCand) relaxCount++;
    if (vis && hasCand && inWhitelist(el)) whitelistCount++;
  }

  const hitText = (el) => {
    const parts = [el.innerText, el.value, el.getAttribute('aria-label'), el.getAttribute('placeholder'), el.getAttribute('title')];
    return parts.some((s) => s && String(s).includes(grep));
  };
  const matches = [];
  if (grep) {
    for (const el of all) {
      if (!hitText(el)) continue;
      const vis = isVisible(el);
      const cands = genCandidates(el);
      const wl = inWhitelist(el);
      let why = 'OK(应能被放宽采集)';
      if (!vis) why = 'invisible(尺寸0/hidden/display:none)';
      else if (!cands.length) why = 'no-candidates(无 testid/id/aria/name/稳定class/文本)';
      else if (!wl) why = 'not-in-whitelist(自定义控件→需框选)';
      matches.push({
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || '').trim().slice(0, 40),
        vis, wl, candN: cands.length,
        best: cands[0] ? (cands[0].by + '=' + cands[0].value) : '-',
        why,
        html: el.outerHTML.slice(0, 200).replace(/\s+/g, ' '),
      });
    }
  }
  return { rawCount: all.length, whitelistCount, relaxCount, matches };
};

const browser = await chromium.connectOverCDP(CDP_URL).catch((e) => {
  console.error('X 连不上 CDP ' + CDP_URL + '：' + e.message + '\n  请确认目标客户端带 --remote-debugging-port=9222 启动、且停在目标页面。');
  process.exit(1);
});
const ctx = browser.contexts()[0];
if (!ctx) { console.error('X CDP 无 context(页面未就绪)'); process.exit(1); }
const pages = ctx.pages().filter((p) => !p.isClosed());
if (!pages.length) { console.error('X 无打开的页面'); process.exit(1); }
const page = pages.find((p) => (p.url() || '').includes('work.n.cn')) || pages[0];
console.log('连接 ' + CDP_URL + ' OK  页面：' + page.url());
console.log('grep = ' + (GREP ? ('「' + GREP + '」') : '(无，仅计数)') + '\n');

let anyMatch = false;
for (const f of page.frames()) {
  let r;
  try { r = await f.evaluate(DIAG, { grep: GREP }); }
  catch (e) { console.log('[frame ' + f.url().slice(0, 60) + '] evaluate 失败(跨域/已卸载)：' + e.message); continue; }
  const label = f === page.mainFrame() ? 'shell(主文档)' : ('iframe ' + f.url().slice(0, 55));
  console.log('-- ' + label);
  console.log('   原始元素 ' + r.rawCount + '  |  白名单可采 ' + r.whitelistCount + '  |  放宽可采 ' + r.relaxCount);
  if (GREP) {
    if (!r.matches.length) console.log('   grep「' + GREP + '」：本 frame 原始 DOM 无命中');
    for (const m of r.matches) {
      anyMatch = true;
      console.log('   * <' + m.tag + '> "' + m.text + '"  可见=' + m.vis + ' 白名单=' + m.wl + ' 候选=' + m.candN + ' best=' + m.best);
      console.log('     判定：' + m.why);
      if (SHOW_HTML) console.log('     html：' + m.html);
    }
  }
  console.log('');
}

if (GREP && !anyMatch) {
  console.log('! 所有 frame 原始 DOM 都没找到含该文本的元素。可能：');
  console.log('   - 元素在闭合 shadow root(closed shadowRoot，JS 无法穿透)');
  console.log('   - 元素文本不含该子串(换个关键词，或用它的 aria-label/placeholder)');
  console.log('   - 当前连的不是目标页(上方"页面"URL 对不对？换页/换 tab 后重跑)');
}
await browser.close();
process.exit(0);
