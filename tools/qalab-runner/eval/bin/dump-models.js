#!/usr/bin/env node
// 诊断脚本：dump 出「模型 / 对话模式 / 深度思考」三个下拉里每个选项的「真实文本」，
// 用于排查 --model / --chat-mode / --thinking-depth 填的名字为何匹配不上（多为全半角括号/空格差异）。
// 复用项目现有的 ContextPool + DialogRunner（等 iframe、开新对话），无头即可跑。
// 用法：node bin/dump-models.js [账号名]   （账号名可选，默认用 accounts/ 下第一个）

const path = require('path');
const fs = require('fs');
const ContextPool = require('../src/context-pool');
const DialogRunner = require('../src/dialog-runner');

// 打印字符串的「逐字符编码」，暴露全角空格 / 中文括号 / 不可见字符等对不上的元凶
function reveal(s) {
  return [...(s || '')].map(ch => {
    const code = ch.codePointAt(0);
    if (ch === ' ') return '·(U+0020半角空格)';
    if (code === 0x3000) return '·(U+3000全角空格)';
    if (code === 0x00A0) return '·(U+00A0不断行空格)';
    if (code === 0xFF08) return '（(U+FF08全角左括号)';
    if (code === 0xFF09) return '）(U+FF09全角右括号)';
    if (code < 0x20 || code === 0x200B) return `[U+${code.toString(16).toUpperCase()}]`;
    return ch;
  }).join('');
}

(async () => {
  const accountsDir = path.resolve('./accounts');
  const files = fs.existsSync(accountsDir)
    ? fs.readdirSync(accountsDir).filter(f => f.toLowerCase().endsWith('.json')).sort()
    : [];
  if (files.length === 0) {
    console.error('accounts/ 下没有账号 json，请先录制账号');
    process.exit(1);
  }
  const wantName = process.argv[2];
  const file = wantName ? files.find(f => path.basename(f, '.json') === wantName) : files[0];
  if (!file) {
    console.error(`账号「${wantName}」不存在。可用：${files.map(f => path.basename(f, '.json')).join(', ')}`);
    process.exit(1);
  }
  const accountName = path.basename(file, '.json');
  const config = require('../config/default.config.js');
  const P = config.platform;

  // 三个下拉：名称 / 触发选择器 / 选项选择器。选项选择器为空（深度思考）时用可见文本兜底 dump。
  const DROPDOWNS = [
    { name: '模型',     trigger: P.modelDropdownSelector,      option: P.modelOptionSelector,      flag: '--model' },
    { name: '对话模式', trigger: P.chatModeTriggerSelector,    option: P.chatModeOptionSelector,   flag: '--chat-mode' },
    { name: '深度思考', trigger: P.thinkingDepthTriggerSelector, option: P.thinkingDepthOptionSelector, flag: '--thinking-depth' }
  ];

  console.log(`用账号「${accountName}」无头打开页面，dump 对话选项三个下拉...\n`);

  const pool = new ContextPool(
    [{ name: accountName, storageState: path.join(accountsDir, file) }],
    { ...config.browser, headless: true }
  );
  await pool.init();
  const context = await pool.getContext(accountName);

  const runner = new DialogRunner(context, P, config.execution, {
    info: () => {}, warn: () => {}, error: (m) => console.error(m)
  });

  // 展开一个下拉并 dump 其选项真实文本。option 为空时用「除脚本注入外的可见短文本」难以稳定枚举，
  // 故深度思考这类无选项 class 的，提示改用有头 F12；能枚举的（模型/对话模式）逐条打印。
  async function dumpOne(ctx, d) {
    console.log(`==== ${d.name}（${d.flag}）====`);
    if (!d.trigger) { console.log('  （未配置触发选择器，跳过）\n'); return; }
    const trigger = ctx.locator(d.trigger).first();
    if (await trigger.count().catch(() => 0) === 0) {
      console.log(`  找不到触发控件（${d.trigger}）\n`); return;
    }
    await trigger.click({ timeout: 4000 }).catch(() => {});
    await runner.page.waitForTimeout(400);
    if (!d.option) {
      console.log('  该下拉未配置选项选择器（选项 class 未知），无法稳定枚举；请用有头 F12 核对，' +
        '或直接按提示文案填（低/中/标准/高/超高 之类）。\n');
      await runner.page.keyboard.press('Escape').catch(() => {});
      return;
    }
    let optCount = await ctx.locator(d.option).count().catch(() => 0);
    if (optCount === 0) { // 坐标点击没展开，原生 click 兜底
      await trigger.evaluate(el => el.click()).catch(() => {});
      await runner.page.waitForTimeout(700);
      optCount = await ctx.locator(d.option).count().catch(() => 0);
    }
    if (optCount === 0) {
      console.log(`  展开后未找到任何「${d.option}」选项（下拉可能没真正展开，或选择器不对，需 F12 核对）\n`);
      await runner.page.keyboard.press('Escape').catch(() => {});
      return;
    }
    const opts = ctx.locator(d.option);
    const n = await opts.count();
    console.log(`  共 ${n} 个选项，真实文本如下（务必逐字复制到 ${d.flag}）：`);
    for (let i = 0; i < n; i++) {
      const t = ((await opts.nth(i).innerText().catch(() => '')) || '').trim();
      console.log(`    [${i + 1}] 原文: ${JSON.stringify(t)}`);
      console.log(`        编码: ${reveal(t)}`);
    }
    console.log('');
    await runner.page.keyboard.press('Escape').catch(() => {});
    await runner.page.waitForTimeout(200);
  }

  try {
    await runner.init(); // 打开页面、等对话 iframe、开新对话
    const ctx = runner._ctx();
    for (const d of DROPDOWNS) {
      try { await dumpOne(ctx, d); }
      catch (e) { console.log(`  dump「${d.name}」出错：${(e.message || e)}\n`); }
    }
    console.log('说明：--model / --chat-mode / --thinking-depth 传入的值，匹配时已自动容忍');
    console.log('全半角括号、空格、大小写差异（见 dialog-runner._normOptionText），但主体字得对上。');
  } catch (e) {
    console.error('执行失败：', (e.message || e));
  } finally {
    await runner.close().catch(() => {});
    await pool.close().catch(() => {});
  }
})();
