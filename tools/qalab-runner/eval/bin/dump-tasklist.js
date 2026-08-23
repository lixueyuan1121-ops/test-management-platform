#!/usr/bin/env node
// 诊断脚本：连桌面客户端(CDP)，进对话 iframe，dump 左侧任务列表条目结构，
// 用于核对 platform.taskListRunningSelector（「执行中」转圈标志）是否命中真实 class。
// 前提：客户端已带 --remote-debugging-port=9222 跑着且已登录到主对话界面。
//
// 用法：
//   node bin/dump-tasklist.js            # 只 dump 当前列表条目（已完成态）
//   node bin/dump-tasklist.js --running  # 发一条 query，趁「执行中」dump 最新条目，再等完成 dump 同条目，两态对比
//   node bin/dump-tasklist.js 9223 --running   # 指定端口

const { chromium } = require('playwright');

const args = process.argv.slice(2);
const wantRunning = args.includes('--running');
const PORT = parseInt(args.find(a => /^\d+$/.test(a)) || '9222', 10);
const ITEM_SEL = '.aside-panel-task-list__item';
const INPUT_SEL = '.chat-compose-rich__content';
const SEND_SEL = 'button.send-btn';
const NEW_TASK_SEL = '.aside-panel-chat-button, .aside-panel__chat-button';
const STOP_SEL = 'button.send-btn:not(.send-btn--noop):not([disabled])';
const FOOTER_SEL = '.chat-token-cost__text';
// 配置里现有的「执行中」猜测选择器
const RUNNING_SEL = '[class*="loading"], [class*="running"], [class*="spin"], svg.animate-spin';
const PROBE_Q = '用python写一个完整的贪吃蛇游戏代码，带开始界面、分数显示、游戏结束界面';

function classNames(el) { return (el && el.className && el.className.toString) ? el.className.toString().trim() : ''; }

(async () => {
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
  const ctx = browser.contexts()[0];
  const page = ctx.pages().find(p => /work\.n\.cn/.test(p.url()));
  if (!page) {
    console.error('未找到 work.n.cn 主窗口。已连接页面：', ctx.pages().map(p => p.url()));
    process.exit(1);
  }
  console.log('主窗口:', page.url());
  const fl = page.frameLocator('iframe[src*=".work.n.cn"]').first();
  try { await fl.locator(INPUT_SEL).first().waitFor({ state: 'visible', timeout: 10000 }); }
  catch { console.error('对话 iframe 未就绪（可能未登录或不在主对话界面）'); process.exit(1); }
  await inspectAndCloseModal(page, fl, 'iframe就绪');

  if (!wantRunning) {
    await dumpList(fl, '当前列表（已完成态）');
    await browser.close();
    return;
  }

  // —— --running：发一条 query，趁执行中/完成后各 dump 一次最新条目 ——
  console.log('\n--- 发送 probe query 趁执行中 dump ---');
  console.log('query:', PROBE_Q);
  // 新建一个干净任务
  await fl.locator(NEW_TASK_SEL).first().click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await inspectAndCloseModal(page, fl, '新建任务后');
  const input = fl.locator(INPUT_SEL).first();
  await input.click();
  await page.keyboard.type(PROBE_Q, { delay: 10 });
  await page.waitForTimeout(300);
  await fl.locator(SEND_SEL).first().click();
  // 可能耗算力豆/继续确认框，统统接受
  await dismissDialogs(page);

  // 等生成开始：停止按钮可见
  let started = false;
  for (let i = 0; i < 60; i++) {
    if (await fl.locator(STOP_SEL).first().isVisible().catch(() => false)) { started = true; break; }
    await page.waitForTimeout(1000);
  }
  console.log(started ? '✓ 已进入生成中' : '✗ 生成未开始（发送可能失败/弹了确认框）');
  if (!started) { await dumpList(fl, '未开始态'); await browser.close(); return; }

  // 趁执行中 dump 最新条目（index 0）
  await dumpItem(fl, 0, '执行中态');

  // 等完成：footer 出现 + 停止按钮消失
  console.log('\n--- 等生成完成 ---');
  for (let i = 0; i < 300; i++) {
    const foot = await fl.locator(FOOTER_SEL).first().count();
    const stop = await fl.locator(STOP_SEL).first().isVisible().catch(() => false);
    if (foot > 0 && !stop) { console.log(`✓ 完成（约 ${i}s）`); break; }
    await page.waitForTimeout(1000);
  }
  await dumpItem(fl, 0, '完成态');

  await browser.close();
})().catch(e => { console.error('出错:', e.message); process.exit(1); });

async function dismissDialogs(page) {
  // 平台原生确认弹窗
  page.on('dialog', async dg => { try { await dg.accept(); } catch { try { await dg.dismiss(); } catch {} } });
  // 自定义确认框（按文案点肯定按钮）
  const texts = ['确认', '确定', '继续', '允许', '同意', '好的', '是', '开始', '执行'];
  for (let i = 0; i < 3; i++) {
    for (const t of texts) {
      const btn = page.frameLocator('iframe[src*=".work.n.cn"]').first().locator(`button:has-text("${t}")`).first();
      if (await btn.isVisible().catch(() => false)) { await btn.click().catch(() => {}); await page.waitForTimeout(500); }
    }
    await page.waitForTimeout(400);
  }
}

// 检测并关闭 config-modal 类引导/配置弹窗（Web Component，dismissDialogs 不覆盖）。
// 用 config-modal[open] 的 count 判定（open 属性一旦设置即命中，不受动画/可见性时序影响）。
// 存在时先 dump 其结构与按钮文案（便于看清是啥弹窗），再依次试 Esc → 常见关闭按钮文案。
async function inspectAndCloseModal(page, fl, tag) {
  const exists = async () => (await fl.locator('config-modal[open]').count().catch(() => 0)) > 0;
  let found = false;
  for (let i = 0; i < 6; i++) { if (await exists()) { found = true; break; } await page.waitForTimeout(400); }
  if (!found) return false;
  const modal = fl.locator('config-modal[open]').first();
  console.log(`\n[${tag}] 检测到 config-modal 弹窗，dump 结构:`);
  const btns = await modal.evaluate(el => {
    const bs = el.querySelectorAll('button, [role="button"], .btn, [class*="close"], [class*="cancel"]');
    return [...bs].map(b => ({ tag: b.tagName.toLowerCase(), text: (b.innerText || '').trim().slice(0, 24), cls: (b.className || '').toString().trim() }));
  }).catch(() => []);
  console.log('  按钮:', btns.length ? JSON.stringify(btns) : '无');
  const tree = await modal.evaluate(el => {
    const out = []; const walk = (n, d) => { if (!n || d > 5 || n.nodeType !== 1) return; out.push('  '.repeat(d) + `<${n.tagName.toLowerCase()} class="${(n.className || '').toString().trim()}">`); for (const c of n.children) walk(c, d + 1); };
    walk(el, 0); return out.join('\n');
  }).catch(() => '');
  console.log('  结构:'); console.log(tree);
  // 优先点关闭按钮 class（config-modal__close 这种无文本 × 按钮，文案匹配不到）
  for (const sel of ['.config-modal__close', '[class*="close-btn"]', '[class*="close"]']) {
    const btn = modal.locator(sel).first();
    if (await btn.count().catch(() => 0) > 0) { await btn.click().catch(() => {}); await page.waitForTimeout(700); if (!(await exists())) { console.log(`  ✓ 点 ${sel} 关闭`); return true; } }
  }
  // 试 Esc
  await page.keyboard.press('Escape').catch(() => {});
  await page.waitForTimeout(700);
  if (!(await exists())) { console.log('  ✓ Esc 关闭'); return true; }
  for (const t of ['关闭', '取消', '知道了', '跳过', '稍后', '不用了', '确定', '确认', '开始', '继续', '同意', '下一步', '完成']) {
    const btn = modal.locator(`button:has-text("${t}")`).first();
    if (await btn.count().catch(() => 0) > 0) { await btn.click().catch(() => {}); await page.waitForTimeout(700); if (!(await exists())) { console.log(`  ✓ 点「${t}」关闭`); return true; } }
  }
  console.log('  ✗ 未能关闭（后续点击可能仍被拦截）');
  return true;
}

async function dumpList(fl, tag) {
  const items = fl.locator(ITEM_SEL);
  const total = await items.count();
  console.log(`\n=== ${tag}：列表条目数 ${total} ===`);
  console.log(`RUNNING_SEL = ${RUNNING_SEL}`);
  for (let i = 0; i < Math.min(total, 12); i++) {
    const it = items.nth(i);
    const cls = (await it.getAttribute('class') || '').trim();
    const title = (await it.locator('.aside-panel-task-list__title-text').first().innerText().catch(() => '')).trim();
    const runN = await it.locator(RUNNING_SEL).count().catch(() => 0);
    console.log(`[${i}] "${title.slice(0, 30)}" class="${cls}" RUNNING_SEL命中=${runN}`);
  }
}

async function dumpItem(fl, i, tag) {
  const it = fl.locator(ITEM_SEL).nth(i);
  const cls = (await it.getAttribute('class') || '').trim();
  const title = (await it.locator('.aside-panel-task-list__title-text').first().innerText().catch(() => '')).trim();
  console.log(`\n=== ${tag}：条目[${i}] "${title.slice(0, 30)}" class="${cls}" ===`);
  const runN = await it.locator(RUNNING_SEL).count().catch(() => 0);
  console.log(`RUNNING_SEL(${RUNNING_SEL}) 命中=${runN}`);
  // dump 完整子树 class（深度6），看执行中态有没有转圈/loading 元素
  const tree = await it.evaluate(el => {
    const out = [];
    const walk = (n, d) => {
      if (!n || d > 6 || n.nodeType !== 1) return;
      const cls = (n.className || '').toString().trim();
      const attrs = [...n.attributes || []].map(a => `${a.name}="${a.value}"`).join(' ');
      out.push('  '.repeat(d) + `<${n.tagName.toLowerCase()} ${attrs}>`);
      for (const c of n.children) walk(c, d + 1);
    };
    walk(el, 0);
    return out.join('\n');
  }).catch(() => '(取子树失败)');
  console.log('子树:'); console.log(tree);
}
