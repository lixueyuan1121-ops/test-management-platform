#!/usr/bin/env node

const path = require('path');
const fs = require('fs');
const { Command } = require('commander');
const FeishuSheetReader = require('../src/feishu-sheet');
const ContextPool = require('../src/context-pool');
const DialogRunner = require('../src/dialog-runner');
const TaskWatcher = require('../src/task-watcher');
const DesktopPool = require('../src/desktop-pool');
const DesktopRunner = require('../src/desktop-runner');
const { groupIntoConversations } = require('../src/conversation-group');
const ResultReporter = require('../src/reporter');
const DiagnosticReporter = require('../src/diagnostic-reporter');
const Logger = require('../src/logger');

// 轻量加载 .env（KEY=VALUE）到环境变量，无需额外依赖。
// 优先读取「当前运行目录」的 .env，其次「项目根目录」；已存在的同名环境变量不覆盖。
function loadDotEnv() {
  const candidates = [
    path.resolve(process.cwd(), '.env'),
    path.resolve(__dirname, '..', '..', '.env'),  // 上级 tools/qalab-runner/.env(与功能测试点 runner 共享一份配置)
    path.resolve(__dirname, '..', '.env')          // 兜底:eval/.env(过渡/独立运行)
  ];
  for (const envPath of candidates) {
    if (!fs.existsSync(envPath)) continue;
    for (const line of fs.readFileSync(envPath, 'utf-8').split(/\r?\n/)) {
      const s = line.trim();
      if (!s || s.startsWith('#')) continue;
      const eq = s.indexOf('=');
      if (eq === -1) continue;
      const key = s.slice(0, eq).trim();
      let val = s.slice(eq + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (key && process.env[key] === undefined) process.env[key] = val;
    }
    break; // 命中第一个存在的 .env 即停止
  }
}
loadDotEnv();

const program = new Command();
const logger = new Logger();

program
  .name('ai-eval')
  .description('AI 对话测评自动化 CLI 工具')
  .version('1.0.0');

// ========== run 命令：执行测评 ==========
program
  .command('run')
  .description('执行批量测评')
  .option('-c, --config <path>', '配置文件路径', './config/default.config.js')
  .option('-n, --concurrency <num>', '账号并发数覆盖（同时并行的账号数）')
  .option('-p, --per-account <num>', '单账号内任务并发数覆盖（每账号同时并行的任务数）')
  .option('--headless <bool>', '是否无头模式（默认读取配置文件）')
  .option('--headed', '有头模式：单账号执行（需配合 -a 指定账号），用于可视化调试/观察', false)
  .option('--watch-switch', '并发观察：开独立观察标签，定时轮流点击左侧任务列表切换查看各对话（仅有头有意义）', false)
  .option('--switch-interval <sec>', '并发观察的切换间隔（秒，0=关闭）；覆盖配置，现场调快慢用')
  .option('--reload-interval <sec>', '并发观察的兜底刷新间隔（秒，0=关闭）：列表未出齐时隔多久刷新拉取')
  .option('--model <name>', '对话模型（覆盖配置），如 GLM-5.2 / 豆包（seed-2.1） / DeepSeek-V4-Pro（全量跑 node bin/dump-models.js）')
  .option('--chat-mode <name>', '对话模式（覆盖配置）：智能模式 / 计划模式 / 目标模式')
  .option('--thinking-depth <name>', '深度思考（覆盖配置）：低 / 中 / 标准 / 高 / 超高')
  .option('-a, --account <name>', '指定单个账号（仅 --headed 有头模式需要）')
  .option('--dry-run', '仅读取用例不执行', false)
  .option('--skip-writeback', '不回填飞书表格（只跑对话，结果仍存本地 JSON）', false)
  .option('--answer-only', '只抓答案正文，跳过分享链接/耗时/算力豆等字段抓取（大幅提速，用于快速验证）', false)
  .action(async (options) => {
    const configPath = path.resolve(options.config);
    if (!fs.existsSync(configPath)) {
      logger.error(`配置文件不存在: ${configPath}`);
      process.exit(1);
    }

    const config = require(configPath);

    // --headed 有头模式与 --headless true 语义冲突（有头即 headless=false），提前拦截避免困惑
    if (options.headed && options.headless === 'true') {
      logger.error('参数冲突：--headed（有头）与 --headless true 不能同时使用');
      process.exit(1);
    }

    if (options.concurrency) config.browser.maxConcurrent = parseInt(options.concurrency);
    if (options.perAccount) config.browser.perAccountConcurrent = parseInt(options.perAccount);
    if (options.headless !== undefined) config.browser.headless = options.headless === 'true';

    // 有头模式：强制「单账号」（账号间不并行）+ 禁止有头崩溃时静默降级为无头。
    // 注意：不再强制账号内串行——账号内并发数(perAccountConcurrent)仍读配置/-p，
    // 以便在有头下用 --watch-switch 观察多任务并发时对话是否会串。
    // 放在 CLI 覆盖之后，确保 --headed 的账号约束优先于 -n。
    if (options.headed) {
      config.browser.headless = false;
      config.browser.maxConcurrent = 1;             // 单账号（账号间不并行）
      config.browser.allowHeadlessFallback = false; // 有头启动失败即报错，不静默变无头
      if (options.concurrency) {
        logger.warn('有头模式强制单账号（账号间不并行），已忽略 -n/--concurrency');
      }
    }

    // 并发观察切换间隔覆盖（--switch-interval，单位秒；配置里是 switchIntervalMs 毫秒）。现场调快慢用。
    if (options.switchInterval !== undefined) {
      const sec = parseFloat(options.switchInterval);
      if (!Number.isFinite(sec) || sec < 0) {
        logger.error('--switch-interval 需为 >=0 的秒数（0=关闭切换）');
        process.exit(1);
      }
      config.execution.switchIntervalMs = Math.round(sec * 1000);
    }

    // 并发观察兜底刷新间隔覆盖（--reload-interval，单位秒；配置里是 watchReloadMs 毫秒）。0=关闭兜底刷新。
    if (options.reloadInterval !== undefined) {
      const sec = parseFloat(options.reloadInterval);
      if (!Number.isFinite(sec) || sec < 0) {
        logger.error('--reload-interval 需为 >=0 的秒数（0=关闭兜底刷新）');
        process.exit(1);
      }
      config.execution.watchReloadMs = Math.round(sec * 1000);
    }

    // 对话选项覆盖（--model / --chat-mode / --thinking-depth）：覆盖 config.execution.dialogOptions。
    // 运行测评与并发观察都走同一 DialogRunner，故两条路径都会应用这套选项。
    config.execution.dialogOptions = Object.assign(
      { model: '', chatMode: '', thinkingDepth: '' },
      config.execution.dialogOptions || {}
    );
    if (options.model !== undefined) config.execution.dialogOptions.model = options.model;
    if (options.chatMode !== undefined) config.execution.dialogOptions.chatMode = options.chatMode;
    if (options.thinkingDepth !== undefined) config.execution.dialogOptions.thinkingDepth = options.thinkingDepth;

    // --skip-writeback：跳过飞书回填（只跑对话，结果仍存本地 JSON）。用户想快速验证流程时用。
    if (options.skipWriteback) {
      config.feishu.writeBack = false;
      logger.info('⏭️ 已跳过飞书回填（--skip-writeback，结果仍会存本地 output/ JSON）');
    }
    // --answer-only：只抓答案正文，跳过分享链接/耗时/算力豆等字段抓取（耗时大头），用于快速验证。
    // 注意：answer-only 下 D/E/F/C 列不会回填（只有 H 答案）；多轮的完成判定不受影响（仍靠 footer/气泡稳定）。
    if (options.answerOnly) {
      config.execution.answerOnly = true;
      logger.info('⚡ answer-only：只抓答案，跳过分享链接/耗时/算力豆等字段抓取');
    }

    // 并发观察（--watch-switch）：开独立观察标签、定时点击左侧任务列表切换查看，仅有头有意义（无头无界面）。
    // 无头下开启无效并提示；账号内并发=1 时只有一个标签页，切换无意义，也提示。
    config.execution.watchSwitch = !!options.watchSwitch;
    if (options.watchSwitch && config.browser.headless) {
      logger.warn('--watch-switch 仅在有头模式下有意义（无头无界面可观察），本次无头运行将忽略');
      config.execution.watchSwitch = false;
    }
    if (config.execution.watchSwitch && (config.browser.perAccountConcurrent || 1) <= 1) {
      logger.warn('--watch-switch 需账号内并发>1 才有切换意义（当前=1，只有一个标签页）；请配合 -p 2 及以上');
      config.execution.watchSwitch = false;
    }
    if (config.execution.watchSwitch &&
        (config.execution.switchIntervalMs != null ? config.execution.switchIntervalMs : 5000) <= 0) {
      logger.warn('--watch-switch 已开启但切换间隔为 0，标签页不会自动切换（可手动点标签，或把 --switch-interval 设为 >0）');
      config.execution.watchSwitch = false;
    }

    // 账号来源：始终以本地 accounts/ 目录实际存在的文件为准（自动扫描），
    // 而非 config 里写死的列表——避免换机器 / 重新录制后仍按旧账号执行。
    const accountsDir = path.resolve('./accounts');
    if (!fs.existsSync(accountsDir)) {
      logger.error(`账号目录不存在: ${accountsDir}\n   请先用「录制账号」录制至少一个账号（生成 accounts/<名字>.json）`);
      process.exit(1);
    }
    const scannedAccounts = fs.readdirSync(accountsDir)
      .filter(f => f.toLowerCase().endsWith('.json'))
      .sort()
      .map(f => ({
        name: path.basename(f, '.json'),
        storageState: path.join(accountsDir, f)
      }));
    if (scannedAccounts.length === 0) {
      logger.error(`账号目录 ${accountsDir} 下没有任何 .json 账号文件\n   请先用「录制账号」录制至少一个账号`);
      process.exit(1);
    }

    if (options.headed) {
      // 有头模式必须显式指定账号，且只跑这一个（单账号串行）
      const available = scannedAccounts.map(a => a.name).join(', ');
      if (!options.account) {
        logger.error(`有头模式（--headed）必须用 -a/--account 指定账号\n   可用账号: ${available}`);
        process.exit(1);
      }
      const picked = scannedAccounts.find(a => a.name === options.account);
      if (!picked) {
        logger.error(`账号「${options.account}」不存在（accounts/ 下无 ${options.account}.json）\n   可用账号: ${available}`);
        process.exit(1);
      }
      config.accounts = [picked];
      logger.info(`🖥️ 有头模式：单账号串行，账号 = ${picked.name}`);
    } else {
      config.accounts = scannedAccounts;
      logger.info(`👤 已从本地目录发现 ${scannedAccounts.length} 个账号: ${scannedAccounts.map(a => a.name).join(', ')}`);
      if (options.account) {
        logger.warn('非有头模式下 -a/--account 无效（仍会轮询全部账号）；如需单账号请加 --headed');
      }
    }

    logger.info('🚀 AI 测评任务启动');
    logger.info(`   运行模式: ${options.headed ? '🖥️ 有头 · 单账号' : '无头 · 并发'}`);
    logger.info(`   账号并发数: ${config.browser.maxConcurrent}`);
    logger.info(`   单账号内任务并发数: ${config.browser.perAccountConcurrent || 1}`);
    logger.info(`   无头模式: ${config.browser.headless}`);
    // 对话选项（仅打印已指定的项；留空的不显示，因为留空=不设置、用页面默认）
    {
      const d = config.execution.dialogOptions || {};
      const parts = [];
      if (d.model) parts.push(`模型=${d.model}`);
      if (d.chatMode) parts.push(`对话模式=${d.chatMode}`);
      if (d.thinkingDepth) parts.push(`深度思考=${d.thinkingDepth}`);
      if (parts.length) logger.info(`   🎛️ 对话选项: ${parts.join(' | ')}`);
    }
    if (config.execution.watchSwitch) {
      const switchMs = config.execution.switchIntervalMs != null ? config.execution.switchIntervalMs : 5000;
      const reloadMs = config.execution.watchReloadMs != null ? config.execution.watchReloadMs : 30000;
      const reloadTip = reloadMs > 0 ? `，列表未出齐则每 ${(reloadMs / 1000).toFixed(1)}s 刷新拉取` : '，兜底刷新已关闭';
      logger.info(`   👀 并发观察: 每 ${(switchMs / 1000).toFixed(1)}s 轮流点击左侧任务列表、切换查看各对话${reloadTip}`);
    }

    // 诊断报告器提到 try 外声明：中断(Ctrl-C)/异常退出时也能抢救生成 HTML 报告（否则已截的诊断图白截）。
    let diag = null;
    // Ctrl-C 中断时：先把已收集的诊断落成 HTML 报告再退出（观察场景常中途手动停）。
    process.on('SIGINT', () => {
      try {
        const p = diag && diag.finalize();
        if (p) logger.warn(`\n⛔ 已中断：诊断报告已保存(${diag.count} 条): ${p}`);
        else logger.warn('\n⛔ 已中断');
      } catch (_) {}
      process.exit(130);
    });

    try {
      // 1. 读取测试用例
      logger.info('📋 读取测试用例...');
      const sheetReader = new FeishuSheetReader(config.feishu);
      const testCases = await sheetReader.getTestCases();
      logger.info(`   共读取 ${testCases.length} 条用例`);

      if (testCases.length === 0) {
        logger.warn('没有读取到测试用例，任务结束');
        return;
      }

      // 轮询分配账号——按「会话」轮询而非按行：同一 conversationId 的所有轮必须落在同一账号，
      // 否则多轮会被拆到不同账号（各自独立登录态/对话），第 2 轮起接不上上下文，多轮就断了。
      const accountNames = config.accounts.map(a => a.name);
      const convAccount = new Map(); // conversationId -> 账号（同会话复用同一账号）
      let convSeq = 0;
      testCases.forEach((tc) => {
        if (!convAccount.has(tc.conversationId)) {
          convAccount.set(tc.conversationId, accountNames[convSeq % accountNames.length]);
          convSeq++;
        }
        tc.account = convAccount.get(tc.conversationId);
      });
      logger.info(`   账号分配: ${accountNames.join(', ')}（按会话轮询，同一会话的多轮同账号）`);

      if (options.dryRun) {
        logger.info('【dry-run 模式】仅展示用例，不执行');
        testCases.forEach((tc, i) => {
          const attach = tc.attachments.length > 0 ? ` [附件×${tc.attachments.length}]` : '';
          // 多轮：展示会话ID与轮次，便于核对分组解析是否正确
          const turn = tc.hasConversationId ? ` 🔁[${tc.conversationId}#${(tc.turnIndex || 0) + 1}]` : '';
          logger.info(`   ${i + 1}. [${tc.caseId}] ${tc.account}${turn} ${tc.question.slice(0, 30)}...${attach}`);
        });
        const convIds = new Set(testCases.filter(t => t.hasConversationId).map(t => t.conversationId));
        if (convIds.size > 0) {
          logger.info(`   🔁 含多轮会话 ${convIds.size} 个：${[...convIds].join(', ')}`);
        }
        return;
      }

      // 2. 下载附件
      const casesWithAttachments = testCases.filter(tc => tc.attachments.length > 0);
      if (casesWithAttachments.length > 0) {
        logger.info(`📎 下载附件（${casesWithAttachments.length} 条用例含附件）...`);
        const tmpDir = path.resolve('./output/_attachments');
        for (const tc of casesWithAttachments) {
          tc.attachmentPaths = [];
          for (const att of tc.attachments) {
            const fileName = att.name || att.file_token || `file_${Date.now()}`;
            const savePath = path.join(tmpDir, tc.caseId, fileName);
            try {
              if (att.file_token) {
                await sheetReader.downloadAttachment(att.file_token, savePath);
              } else if (att.url) {
                await sheetReader.downloadUrl(att.url, savePath);
              }
              tc.attachmentPaths.push(savePath);
              logger.info(`   ✅ ${tc.caseId}: ${fileName}`);
            } catch (e) {
              logger.warn(`   ⚠️ ${tc.caseId}: 下载失败 ${fileName} - ${e.message}`);
            }
          }
        }
      }

      // 3. 初始化浏览器
      logger.info('🌐 初始化浏览器...');
      const pool = new ContextPool(config.accounts, config.browser);
      await pool.init();

      // 4. 并发执行
      logger.info('⚡ 开始执行测评...');
      const startTime = Date.now();
      const results = [];
      let completed = 0;
      // 诊断报告：观察器（串台/agent/结构）与执行侧（完成无正文/耗时需刷新）的异常统一记录到这里。
      diag = new DiagnosticReporter(config.output && config.output.outputDir, logger);

      // 按账号分组：组间并行，组内也并行（发送严格有序）。
      const byAccount = new Map();
      for (const tc of testCases) {
        if (!byAccount.has(tc.account)) byAccount.set(tc.account, []);
        byAccount.get(tc.account).push(tc);
      }

      const perAccount = config.browser.perAccountConcurrent || 1;
      const watchInterval = config.execution.watchIntervalMs != null ? config.execution.watchIntervalMs : 15000;
      // 并发观察：定时轮流点击左侧任务列表切换查看的间隔（仅当 --watch-switch 开启时生效）
      const switchInterval = config.execution.switchIntervalMs != null ? config.execution.switchIntervalMs : 5000;
      const watchSwitch = !!config.execution.watchSwitch;
      // 账号内相邻两条对话「创建/发送」之间的最小间隔（毫秒）：上一条确认进入生成后，等这么久再发下一条，
      // 避免并发时对话创建挤在一起（平台创建跟不上/限流）。默认 3000ms，设 0 关闭。
      const sendIntervalMs = config.execution.sendIntervalMs != null ? config.execution.sendIntervalMs : 3000;
      const delay = (ms) => new Promise((r) => setTimeout(r, ms));

      // 发送门：把「输入→点发送→确认已开始生成」串行化，确保上一条顺利发出去执行后再发下一条；
      // 并在两条发送之间留出 gapMs 间隔（对话创建之间相隔一段时间）。
      const createSendGate = (gapMs) => {
        let chain = Promise.resolve();
        return (fn) => {
          const run = chain.then(fn, fn); // 前一条发送 settle（成功/失败）后才轮到本条发送
          // 本条 settle 后等 gapMs 再放行下一条；间隔只作用于「下一条何时开始」，不拖慢本条的返回。
          chain = gapMs > 0 ? run.then(() => delay(gapMs), () => delay(gapMs)) : run.catch(() => {});
          return run;                     // 调用方 await 自己的发送完成后，再去并行等待/抓取
        };
      };

      // 账号内并发限流：同时最多 limit 个任务在跑，其余排队；按数组顺序启动（发送顺序即用例顺序）。
      const runLimited = async (items, limit, worker) => {
        const executing = new Set();
        for (const item of items) {
          const p = Promise.resolve().then(() => worker(item)).finally(() => executing.delete(p));
          executing.add(p);
          if (executing.size >= limit) await Promise.race(executing);
        }
        await Promise.all(executing);
      };

      const runAccountQueue = async (account) => {
        const context = await pool.getContext(account);
        const cases = byAccount.get(account);
        // 账号内把用例按 conversationId 分组成「会话」：同会话多轮按 turnIndex 排序、在同一对话串行连发；
        // 不同会话之间并行（各占一标签页，限流 perAccount）。单轮行各自成一个只含一条的会话。
        const convMap = new Map();
        for (const tc of cases) {
          if (!convMap.has(tc.conversationId)) convMap.set(tc.conversationId, []);
          convMap.get(tc.conversationId).push(tc);
        }
        const conversations = [...convMap.values()]
          .map(turns => turns.slice().sort((a, b) => (a.turnIndex || 0) - (b.turnIndex || 0)));
        const multiTurnCount = conversations.filter(c => c.length > 1).length;
        if (multiTurnCount > 0) {
          logger.info(`   🔁 [${account}] ${conversations.length} 个会话（其中 ${multiTurnCount} 个为多轮），账号内并发上限 ${perAccount}`);
        }
        const sendGate = createSendGate(sendIntervalMs); // 每账号一个发送门（账号内发送串行 + 相邻间隔；账号间互不影响）
        const activeRunners = new Set();        // 巡检用：该账号当前活跃的任务

        // 巡检定时器：来回查看各任务标签，确认是否正在执行，并打印状态
        let watchTimer = null;
        if (watchInterval > 0) {
          const patrol = async () => {
            const runners = [...activeRunners];
            if (runners.length === 0) return;
            const marks = [];
            for (const r of runners) {
              // 主动实时探测一次“生成中”，兼顾巡检可见性（同时更新缓存标记）
              let alive = r.isGenerating();
              try { if (!alive) alive = await r._probeGenerating(); } catch {}
              marks.push(`${r.label}:${alive ? '执行中' : '…'}`);
            }
            logger.info(`   🔎 [${account}] 巡检 ${runners.length} 个任务 → ${marks.join('  ')}`);
          };
          watchTimer = setInterval(() => { patrol().catch(() => {}); }, watchInterval);
        }

        // 并发观察（--watch-switch）：开一个独立的观察标签页，定时轮流点击左侧任务列表的前 N 个条目，
        // 让内容区切换显示各任务对话，供肉眼确认多个并发任务的内容是否会串。观察页面只读浏览，不干扰执行。
        // 仅在 --watch-switch 开启且账号内并发>1 时才有意义（只有一个任务无需切换观察）。
        let watcher = null;
        if (watchSwitch && switchInterval > 0 && perAccount > 1) {
          watcher = new TaskWatcher(context, config.platform, config.execution, logger, account, diag);
          try {
            await watcher.init();
            // 传入活跃任务探针：观察器切换前若发现无进行中的任务（都执行完、进入收尾），自动停止切换。
            watcher.start(perAccount, switchInterval, () => activeRunners.size);
            logger.info(`   👀 [${account}] 观察标签页已就绪：每 ${(switchInterval / 1000).toFixed(1)}s 轮流点击左侧前 ${perAccount} 个任务`);
          } catch (e) {
            logger.warn(`   👀 [${account}] 观察标签页启动失败（不影响执行）: ${e.message}`);
            watcher = null;
          }
        }

        // 每一轮完成后的收尾：诊断 → 汇总 → 日志 → 回填该轮所在行。多轮里每轮各调一次。
        // runner.page 此时仍在（会话尚未 close），诊断可正常截图存档。
        const finishOne = async (result, testCase, runner) => {
          try {
            //  d. 完成后无正文：出现完成信号(footer/stable)却没抓到有效正文
            //  e. 字段需刷新才拿到：reloadRecoveredFields 记录了靠刷新页面才补上的字段（如耗时）
            const doneSignal = result.completeReason === 'footer' || result.completeReason === 'stable';
            if (doneSignal && !result.success && !String(result.answer || '').startsWith('[执行失败]')) {
              await diag.record(testCase.caseId, 'D-完成无正文', `完成信号(${result.completeReason})已出现但未抓到有效正文`, runner.page);
            }
            if (result.reloadRecoveredFields && result.reloadRecoveredFields.length) {
              await diag.record(testCase.caseId, 'E-字段需刷新',
                `以下字段第一时间抓不到、刷新页面后才拿到: ${result.reloadRecoveredFields.join(', ')}`, runner.page);
            }
          } catch (_) {}

          results.push(result);
          completed++;
          const status = result.success ? '✅' : '❌';
          const duration = (result.durationMs / 1000).toFixed(1);
          const miss = (result.missingFields && result.missingFields.length)
            ? ` ⚠️缺失:${result.missingFields.join('/')}` : '';
          // 失败时标注原因（未完成:超时/停滞/迟迟不启动/中间态），便于排查回填质量
          const why = (!result.success && result.completeReason && result.completeReason !== 'footer' && result.completeReason !== 'stable')
            ? ` 🛑未完成:${result.completeReason}` : '';
          // 多轮：日志带上「轮次」标识（第几轮），便于观察同一会话的连发进度
          const turnTag = (testCase.hasConversationId)
            ? ` 🔁${testCase.conversationId}#${(testCase.turnIndex || 0) + 1}` : '';
          logger.info(`   [${completed}/${testCases.length}] ${status} ${testCase.caseId} (${account}, ${duration}s)${miss}${why}${turnTag}`);

          // 回填：每轮完成即实时写入自己那一行；按真实行号定位，多会话并发完成互不干扰
          if (config.feishu.writeBack) {
            try {
              await sheetReader.writeResult(result);
              logger.info(`       ↳ 已回填 ${testCase.caseId}`);
            } catch (e) {
              logger.warn(`       ↳ 回填失败 ${testCase.caseId}: ${e.message}`);
            }
          }
        };

        // 处理一个「会话」：同一 DialogRunner 里按 turnIndex 顺序连发所有轮（首轮新建对话，
        // 后续轮复用同一对话形成多轮上下文）；每轮完成即经 finishOne 回填对应行。整段跑完再 close。
        // 单轮会话（未指定会话ID的普通行）也走这里：turns 只有一条，等价于原来的「一行一对话」。
        const runConversationTasks = async (turns) => {
          const runner = new DialogRunner(context, config.platform, config.execution, logger);
          runner.label = turns[0].caseId;   // 巡检/日志标识（取会话首条）
          runner.sendGate = sendGate;        // 注入账号发送门：发送段串行（跨会话也串行首轮发送）
          activeRunners.add(runner);
          try {
            await runner.runConversation(turns, async (result, testCase) => {
              result.account = account;
              await finishOne(result, testCase, runner);
              // 巡检 label 跟到当前轮，日志更贴切（会话内串行，不会并发改写）
              const next = turns[(testCase.turnIndex || 0) + 1];
              if (next) runner.label = next.caseId;
            });
          } catch (e) {
            // 会话级兜底：整个会话意外抛错也不连累其他会话/账号。已完成的轮已各自回填；
            // 这里对「尚未产出 result」的剩余轮统一记为失败，避免遗漏行不回填。
            const msg = (e && e.message) ? e.message.split('\n')[0] : String(e);
            logger.error(`   ✗ 会话 ${turns[0].conversationId} 执行异常（已按失败处理，不影响其余）: ${msg}`);
            const doneIds = new Set(results.map(r => r.caseId));
            for (const tc of turns) {
              if (doneIds.has(tc.caseId)) continue;
              await finishOne({
                caseId: tc.caseId, row: tc.row, account,
                question: tc.question, success: false, incomplete: true,
                completeReason: 'exception', answer: `[执行异常] ${msg}`,
                shareLink: '', artifactShareLink: '', reportedDuration: '', beanCost: '',
                cost: '', durationMs: 0, missingFields: [], reloadRecoveredFields: []
              }, tc, runner);
            }
          } finally {
            activeRunners.delete(runner);
            await runner.close();
          }
        };

        try {
          // 账号内并发的单元是「会话」：同会话多轮串行，不同会话各占一标签页并行（限流 perAccount）。
          await runLimited(conversations, perAccount, runConversationTasks);
        } finally {
          if (watchTimer) clearInterval(watchTimer);
          if (watcher) await watcher.stop();
        }
      };

      // 各账号并行；maxConcurrent 控制同时并行的账号数，perAccountConcurrent 控制每账号内并发任务数
      await pool.runConcurrently([...byAccount.keys()], runAccountQueue);

      const totalTime = ((Date.now() - startTime) / 1000).toFixed(1);

      // 5. 统计汇总
      const reporter = new ResultReporter(config.output);
      const summary = reporter.summarize(results);

      logger.info('\n📊 ====== 执行汇总 ======');
      logger.info(`   总用例数: ${summary.total}`);
      logger.info(`   成功: ${summary.success} | 失败: ${summary.failed}`);
      logger.info(`   成功率: ${summary.successRate}`);
      logger.info(`   平均耗时: ${(summary.avgDurationMs / 1000).toFixed(2)}s`);
      logger.info(`   总耗时: ${totalTime}s`);

      // 6. 保存结果（本地备份；飞书回填已在每条完成时实时写入）
      const outputDir = await reporter.saveOutput(results, summary);
      logger.info(`   结果已保存: ${outputDir}`);

      // 诊断报告收尾：生成 HTML 报告（有图有描述），打印路径与条数
      const diagPath = diag.finalize();
      if (diag.count > 0) {
        logger.warn(`   🩺 诊断发现 ${diag.count} 处异常${diagPath ? `，HTML 报告: ${diagPath}` : ''}`);
      } else {
        logger.info(`   🩺 诊断未发现异常${diagPath ? `（空报告: ${diagPath}）` : ''}`);
      }

      await pool.close();
      logger.info('🎉 任务完成');

    } catch (error) {
      logger.error(`任务执行失败: ${error.message}`);
      logger.error(error.stack);
      // 异常退出前也抢救诊断报告（已收集的诊断/截图不至于白费）
      try {
        const p = diag && diag.finalize();
        if (p) logger.warn(`   🩺 诊断报告已保存(${diag.count} 条): ${p}`);
      } catch (_) {}
      process.exit(1);
    }
  });

// ========== desktop 命令：打开纳米 Work 桌面客户端做对话并发验证 ==========
// 复用现有全部验证能力（读飞书用例 / 对话执行 / 分享链接·耗时·算力豆·正文抓取 / 串台诊断 / 回填 /
// 汇总），仅把「连接方式」从 chromium.launch() 换成「带调试端口重启桌面客户端 + CDP 连接」，
// 并把并发从「多标签页」换成「单窗口内新建任务连发 + 左侧列表切换观察」（见 src/desktop-runner.js）。
program
  .command('desktop')
  .description('打开纳米 Work 桌面客户端，做对话并发验证（复用全部现有测试验证能力）')
  .option('-c, --config <path>', '配置文件路径', './config/default.config.js')
  .option('-p, --concurrency <num>', '并发条数（同一窗口内同时连发几条对话），默认读配置 perAccountConcurrent 或 3')
  .option('--limit <num>', '最多跑多少条用例（默认全部读到的用例）')
  .option('--model <name>', '对话模型（覆盖配置）')
  .option('--chat-mode <name>', '对话模式（覆盖配置）：智能模式 / 计划模式 / 目标模式')
  .option('--thinking-depth <name>', '深度思考（覆盖配置）：低 / 中 / 标准 / 高 / 超高')
  .option('--attach', '只连接「已手动带调试端口开着」的客户端，绝不自动关闭/重启进程', false)
  .option('--cdp-port <num>', '远程调试端口（覆盖配置）')
  .option('--exe <path>', '客户端可执行文件路径（覆盖配置）')
  .option('--no-keep-client', '结束后关闭本程序启动的客户端（默认保留，便于下次直接连）')
  .option('--dry-run', '仅读取并展示将要并发的用例，不启动客户端、不执行', false)
  .option('--skip-writeback', '不回填飞书表格（只跑对话，结果仍存本地 JSON）', false)
  .option('--answer-only', '只抓答案正文，跳过分享链接/耗时/算力豆等字段（大幅提速）', false)
  .action(async (options) => {
    const configPath = path.resolve(options.config);
    if (!fs.existsSync(configPath)) {
      logger.error(`配置文件不存在: ${configPath}`);
      process.exit(1);
    }
    const config = require(configPath);
    config.desktop = config.desktop || {};

    // CLI 覆盖：桌面连接
    if (options.attach) config.desktop.attachOnly = true;
    if (options.cdpPort !== undefined) config.desktop.cdpPort = parseInt(options.cdpPort);
    if (options.exe) config.desktop.executablePath = options.exe;

    // CLI 覆盖：对话选项（与 run 命令同口径，走同一 DialogRunner）
    config.execution.dialogOptions = Object.assign(
      { model: '', chatMode: '', thinkingDepth: '' },
      config.execution.dialogOptions || {}
    );
    if (options.model !== undefined) config.execution.dialogOptions.model = options.model;
    if (options.chatMode !== undefined) config.execution.dialogOptions.chatMode = options.chatMode;
    if (options.thinkingDepth !== undefined) config.execution.dialogOptions.thinkingDepth = options.thinkingDepth;

    if (options.skipWriteback) { config.feishu.writeBack = false; logger.info('⏭️ 已跳过飞书回填（--skip-writeback）'); }
    if (options.answerOnly) { config.execution.answerOnly = true; logger.info('⚡ answer-only：只抓答案，跳过分享链接/耗时/算力豆等字段抓取'); }

    const concurrency = parseInt(options.concurrency) || config.browser.perAccountConcurrent || 3;

    logger.info('🖥️ 纳米 Work 桌面客户端 · 对话并发验证');
    logger.info(`   并发条数: ${concurrency}（单窗口内同时连发的对话数）`);
    {
      const d = config.execution.dialogOptions || {};
      const parts = [];
      if (d.model) parts.push(`模型=${d.model}`);
      if (d.chatMode) parts.push(`对话模式=${d.chatMode}`);
      if (d.thinkingDepth) parts.push(`深度思考=${d.thinkingDepth}`);
      if (parts.length) logger.info(`   🎛️ 对话选项: ${parts.join(' | ')}`);
    }

    let diag = null;
    let pool = null;
    process.on('SIGINT', () => {
      try {
        const p = diag && diag.finalize();
        if (p) logger.warn(`\n⛔ 已中断：诊断报告已保存(${diag.count} 条): ${p}`);
        else logger.warn('\n⛔ 已中断');
      } catch (_) {}
      // 尽量断开 CDP（不关客户端），再退出
      try { if (pool) pool.close({ keepClient: options.keepClient !== false }); } catch (_) {}
      process.exit(130);
    });

    try {
      // 1) 读取用例
      logger.info('📋 读取测试用例...');
      const sheetReader = new FeishuSheetReader(config.feishu);
      let testCases = await sheetReader.getTestCases();
      logger.info(`   共读取 ${testCases.length} 条用例`);
      if (testCases.length === 0) { logger.warn('没有读取到测试用例，任务结束'); return; }

      // 桌面并发场景聚焦「单轮并发」：每条 query 各开一个独立新对话并发跑。
      // 若表格里含多轮会话（同一 conversationId 多行），桌面场景按「独立单轮」各自处理并提示。
      const convIds = new Set(testCases.filter(t => t.hasConversationId).map(t => t.conversationId));
      if (convIds.size > 0) {
        logger.warn(`   ⚠️ 检测到 ${convIds.size} 个多轮会话；桌面并发场景按「独立单轮」各自开对话处理（多轮上下文串联请用 run 命令）`);
      }

      // --limit 限制条数
      const limit = options.limit ? parseInt(options.limit) : 0;
      if (limit > 0 && testCases.length > limit) {
        testCases = testCases.slice(0, limit);
        logger.info(`   按 --limit 取前 ${testCases.length} 条`);
      }
      testCases.forEach(tc => { tc.account = 'desktop'; });

      if (options.dryRun) {
        logger.info('【dry-run 模式】将并发以下用例（不启动客户端、不执行）:');
        testCases.forEach((tc, i) => {
          const attach = tc.attachments.length > 0 ? ` [附件×${tc.attachments.length}]` : '';
          logger.info(`   ${i + 1}. [${tc.caseId}] ${tc.question.slice(0, 40)}${attach}`);
        });
        logger.info(`   将按每批 ${concurrency} 条并发，共 ${Math.ceil(testCases.length / concurrency)} 批`);
        return;
      }

      // 2) 下载附件（复用 run 的逻辑）
      const casesWithAttachments = testCases.filter(tc => tc.attachments.length > 0);
      if (casesWithAttachments.length > 0) {
        logger.info(`📎 下载附件（${casesWithAttachments.length} 条用例含附件）...`);
        const tmpDir = path.resolve('./output/_attachments');
        for (const tc of casesWithAttachments) {
          tc.attachmentPaths = [];
          for (const att of tc.attachments) {
            const fileName = att.name || att.file_token || `file_${Date.now()}`;
            const savePath = path.join(tmpDir, tc.caseId, fileName);
            try {
              if (att.file_token) await sheetReader.downloadAttachment(att.file_token, savePath);
              else if (att.url) await sheetReader.downloadUrl(att.url, savePath);
              tc.attachmentPaths.push(savePath);
              logger.info(`   ✅ ${tc.caseId}: ${fileName}`);
            } catch (e) { logger.warn(`   ⚠️ ${tc.caseId}: 下载失败 ${fileName} - ${e.message}`); }
          }
        }
      }

      // 3) 连接桌面客户端（关闭→带调试端口重启→CDP 连接→交出主窗口）
      logger.info('🔌 连接纳米 Work 桌面客户端...');
      pool = new DesktopPool(config.desktop, config.platform, logger);
      await pool.init();

      // 4) 并发执行（分批，每批 concurrency 条同时在跑）
      logger.info('⚡ 开始桌面并发测评...');
      const startTime = Date.now();
      const results = [];
      diag = new DiagnosticReporter(config.output && config.output.outputDir, logger);
      const runner = new DesktopRunner(pool.getContext(), pool.getMainPage(), config.platform, config.execution, logger, diag);

      const onResult = async (result, testCase) => {
        results.push(result);
        if (config.feishu.writeBack) {
          try { await sheetReader.writeResult(result); logger.info(`       ↳ 已回填 ${testCase.caseId}`); }
          catch (e) { logger.warn(`       ↳ 回填失败 ${testCase.caseId}: ${e.message}`); }
        }
      };

      const batches = [];
      for (let i = 0; i < testCases.length; i += concurrency) batches.push(testCases.slice(i, i + concurrency));
      for (let b = 0; b < batches.length; b++) {
        logger.info(`\n🧩 第 ${b + 1}/${batches.length} 批：并发 ${batches[b].length} 条`);
        await runner.runConcurrent(batches[b], onResult);
      }

      const totalTime = ((Date.now() - startTime) / 1000).toFixed(1);

      // 5) 汇总
      const reporter = new ResultReporter(config.output);
      const summary = reporter.summarize(results);
      logger.info('\n📊 ====== 执行汇总 ======');
      logger.info(`   总用例数: ${summary.total}`);
      logger.info(`   成功: ${summary.success} | 失败: ${summary.failed}`);
      logger.info(`   成功率: ${summary.successRate}`);
      logger.info(`   平均耗时: ${(summary.avgDurationMs / 1000).toFixed(2)}s`);
      logger.info(`   总耗时: ${totalTime}s`);

      const outputDir = await reporter.saveOutput(results, summary);
      logger.info(`   结果已保存: ${outputDir}`);

      const diagPath = diag.finalize();
      if (diag.count > 0) logger.warn(`   🩺 诊断发现 ${diag.count} 处异常${diagPath ? `，HTML 报告: ${diagPath}` : ''}`);
      else logger.info(`   🩺 诊断未发现异常${diagPath ? `（空报告: ${diagPath}）` : ''}`);

      await pool.close({ keepClient: options.keepClient !== false });
      logger.info(`🎉 桌面并发验证完成${options.keepClient !== false ? '（客户端保持运行，端口仍开着，下次可直接连）' : ''}`);
    } catch (error) {
      logger.error(`桌面并发验证失败: ${error.message}`);
      logger.error(error.stack);
      try { const p = diag && diag.finalize(); if (p) logger.warn(`   🩺 诊断报告已保存(${diag.count} 条): ${p}`); } catch (_) {}
      try { if (pool) await pool.close({ keepClient: options.keepClient !== false }); } catch (_) {}
      process.exit(1);
    }
  });

// ========== platform 命令：平台模式（从测试管理平台拉对话测评任务→驱动桌面客户端→回写） ==========
// 大工程「对话测评链路」子项2 的集成入口。流程：PlatformClient 拉 eval-queue 待执行 → claim 认领 →
// DesktopRunner 驱动纳米 Work 桌面客户端跑单条对话（DesktopPool 已在主 page 挂 attachWsTrace 抓 WS 轨迹）→
// report 回写结果 + uploadTrace 上传会话轨迹。飞书模式（run/desktop）完全不受影响。
// 命名约定（避免撞车）：config.platformApi = 平台对接凭据(baseUrl/token/runnerId/pollMs)；
//                        config.platform    = work.n.cn 页面选择器段(现有，DesktopRunner/DesktopPool 用)。
program
  .command('platform')
  .description('平台模式:从测试管理平台拉对话测评任务,执行并回写(需配 BASE_URL/RUNNER_TOKEN/RUNNER_ID)')
  .option('-c, --config <path>', '配置文件路径', './config/default.config.js')
  .option('--limit <n>', '每轮拉取任务数', '5')
  .option('--once', '只跑一轮(默认常驻轮询)')
  .option('--exe <path>', '纳米Work 客户端 exe 路径(覆盖 config.desktop.executablePath)')
  .option('--cdp-port <port>', 'CDP 调试端口(覆盖 config.desktop.cdpPort)', '')
  .action(async (opts) => {
    const configPath = path.resolve(opts.config);
    if (!fs.existsSync(configPath)) {
      logger.error(`配置文件不存在: ${configPath}`);
      process.exit(1);
    }
    const config = require(configPath);
    config.desktop = config.desktop || {};
    // CLI 覆盖桌面连接：DesktopPool 从 config.desktop 读连接参数，故在此改 config.desktop（与 desktop 命令同口径）
    if (opts.exe) config.desktop.executablePath = opts.exe;
    if (opts.cdpPort) config.desktop.cdpPort = parseInt(opts.cdpPort, 10);

    const PlatformClient = require('../src/platform-client');
    // DesktopPool / DesktopRunner 已在文件顶部 require。
    const client = new PlatformClient(config.platformApi || {}); // 平台对接凭据（缺 BASE_URL/RUNNER_TOKEN 即抛错，快速失败）
    const pollMs = (config.platformApi && config.platformApi.pollMs) || 5000;

    const runOnce = async () => {
      const pending = await client.fetchPending(parseInt(opts.limit, 10) || 5);
      if (!pending || !pending.length) { logger.info('平台无待执行任务'); return 0; }
      logger.info(`拉到 ${pending.length} 条待执行`);
      // DesktopPool 构造签名：(desktopConfig, platformConfig=选择器段, logger)。init 后主 page 已挂 attachWsTrace。
      const pool = new DesktopPool(config.desktop, config.platform, logger);
      await pool.init();
      // Task7-Step1: 上报本机客户端设备(vm)列表,供平台前端下发时下拉选(失败不阻断执行)
      try {
        const devices = await pool.listDevices();
        if (devices.length) { await client.reportDevices(devices); logger.info(`已上报 ${devices.length} 个客户端设备`); }
      } catch (e) { logger.warn(`上报设备列表失败(不影响执行): ${e.message}`); }
      const wsTrace = pool.getWsTrace();
      // DesktopRunner 第3参 = work.n.cn 选择器段 config.platform（非 platformApi）。
      // ⚠️ 修复#3 前置:确保 config.execution 是"真对象"再构造 runner。DesktopRunner 与其内部 DialogRunner
      //   都以【引用】持有该对象(this.execution = executionConfig),且 _applyDialogOptions() 每次发送时
      //   现读 this.execution.dialogOptions。故只要"就地改本对象的 dialogOptions 属性",两个 runner 都能看到;
      //   若 config.execution 为 undefined,DesktopRunner 会另建 {} 断开引用,就地改就失效——故先兜底成对象。
      config.execution = config.execution || {};
      // 记住执行机默认 dialogOptions:某题没带快照时要还原成它,避免上一题的选项串给下一题。
      const baseDialogOptions = config.execution.dialogOptions;
      const runner = new DesktopRunner(pool.getContext(), pool.getMainPage(), config.platform, config.execution, logger);
      // 持久跟踪"当前设备"对应的 runner/wsTrace:随 pool.switchTo 演进,不再每条三元决定。
      // 混合批(先 target 后空 target)时,空 target 项复用最近一次切换后的真实 pool 状态,而非循环外 stale 引用。
      let curRunner = runner;
      let curWsTrace = wsTrace;
      // 按「会话」分组:同一 conversation_group 的多条=同一多轮会话的各轮,归一组、按 turn_index 升序;
      // 单轮(无 group)各自成组。多轮同组各轮将在【同一对话】里顺序连发(轮次0新建、后续轮复用),
      // 而非各自 runOne 新建对话——修正「轮次1 另起新对话、接不上轮次0 上下文」的问题。
      // (后端 list_pending 已保证整组不被 limit 拆到不同批次,见 eval_queue._take_whole_groups。)
      const conversations = groupIntoConversations(pending);
      const multiCount = conversations.filter(c => c.length > 1).length;
      if (multiCount > 0) logger.info(`   其中 ${multiCount} 个多轮会话(同组各轮将在同一对话内顺序连发)`);

      // 把一轮 run 的执行结果回写平台(report + uploadTrace),并清空 WS 收集器供下一轮/下一条独立抓取。
      // 多轮同一对话逐轮隔离靠此 reset:session_id 每帧都带,reset 后下一轮仍能复得同一 session_id。
      const reportRun = async (runId, result, ws) => {
        const trace = ws ? ws.buildTrace(runId) : { ws_captured: false, tool_calls: [] };
        try {
          await client.report(runId, {
            status: result.success ? 'done' : 'failed',
            share_link: result.shareLink || null, artifact_share_link: result.artifactShareLink || null,
            answer: result.answer || null, reported_duration: result.reportedDuration || null,
            bean_cost: result.beanCost || null, tokens: result.cost || null,
            session_id: trace.session_id || null,
            reason: result.success ? null : (result.completeReason || null),
            duration_ms: result.durationMs || null,
          });
          await client.uploadTrace(runId, trace);
          logger.info(`✅ 回写 run ${runId} (${result.success ? 'done' : 'failed'}, ws=${trace.ws_captured})`);
        } catch (e) { logger.error(`回写 run ${runId} 失败: ${e.message}`); }
        if (ws && ws.reset) ws.reset();
      };
      // 整组直接判失败(设备切换失败 / 带附件跳过):claim 后 report failed,不执行。
      const failWholeGroup = async (conv, reason) => {
        for (const it of conv) {
          try { await client.claim(it.run_id); } catch (_) {}
          try { await client.report(it.run_id, { status: 'failed', reason }); }
          catch (e) { logger.error(`report failed 失败 run ${it.run_id}: ${e.message}`); }
        }
      };

      for (const conv of conversations) {
        const head = conv[0];
        const headP = head.payload || {};
        // Task7-Step2: 切到本会话指定的目标设备(vm)。同组共享一个对话→必然同一设备,用组首轮 target_device 切一次。
        // 空=不切,用当前设备(向后兼容)。切换失败 fail-closed,整组标记 failed。
        const targetDevice = head.target_device || null;
        if (targetDevice) {
          try {
            await pool.switchTo(targetDevice);
          } catch (e) {
            logger.warn(`会话(首轮 run ${head.run_id})切换设备失败,fail-closed 整组标记 failed: ${e.message}`);
            await failWholeGroup(conv, `切换目标设备失败: ${e.message}`);
            continue;
          }
          // 切换成功→pool.mainPage/wsTrace 已推进到新 page/新收集器,重建持久 curRunner/curWsTrace 绑新状态。
          // ⚠️ 仍传同一个 config.execution 引用(与循环外 runner 一致),保证修复#3 的"就地改 dialogOptions"仍生效。
          curRunner = new DesktopRunner(pool.getContext(), pool.getMainPage(), config.platform, config.execution, logger);
          curWsTrace = pool.getWsTrace();
        }
        // 修复#2:附件下载在平台模式尚未支持。带附件的题裸跑会产生"无附件的假成功"污染判定;
        // 多轮里更甚(缺附件的一轮会连累整段上下文)。故整组任一轮带附件→整组 fail-closed,不静默裸跑。
        const attTurns = conv.filter(it => Array.isArray((it.payload || {}).attachments) && (it.payload || {}).attachments.length > 0);
        if (attTurns.length > 0) {
          logger.warn(`会话(首轮 run ${head.run_id})含 ${attTurns.length} 轮带附件,平台模式暂不支持附件下载,整组跳过(标记 failed)`);
          await failWholeGroup(conv, '平台模式暂不支持附件下载,未执行(避免无附件裸跑污染判定)');
          continue;
        }
        // 修复#3:应用本会话下发时快照的对话选项(模型/对话模式/深度思考)——同组共享一对话,用组首轮快照,整段统一。
        // ⚠️ 必须【就地改属性】config.execution.dialogOptions:DesktopRunner 及内部 DialogRunner 以引用持有
        //   config.execution,每次发送现读 .dialogOptions;重新赋值 config.execution 会断引用→不生效。
        //   没带快照时还原执行机默认,防串到别的会话。
        if (headP.dialog_options && typeof headP.dialog_options === 'object') {
          config.execution.dialogOptions = headP.dialog_options;
        } else {
          config.execution.dialogOptions = baseDialogOptions;
        }
        // claim:首轮 claim 失败(被他机认领)→整组跳过(不 report,交认领方处理);后续轮 claim 失败仅告警(尽力而为)。
        try {
          await client.claim(head.run_id);
        } catch (e) { logger.warn(`claim 首轮 run ${head.run_id} 失败(可能被他机认领),整组跳过: ${e.message}`); continue; }
        for (const it of conv.slice(1)) {
          try { await client.claim(it.run_id); } catch (e) { logger.warn(`claim run ${it.run_id} 失败: ${e.message}`); }
        }
        // 构造各轮 testCase(带 run_id 供逐轮回写;conversationId/turnIndex 供多轮编排)。
        const testCases = conv.map(it => {
          const p = it.payload || {};
          return {
            caseId: `RUN-${it.run_id}`, run_id: it.run_id, row: it.run_id, question: p.prompt || '',
            attachments: p.attachments || [], attachmentPaths: [],
            conversationId: p.conversation_group || `__run_${it.run_id}`, turnIndex: p.turn_index || 0,
            account: 'desktop',
          };
        });
        // 逐轮完成回调:回写该轮的 run(用当前 curWsTrace,随设备切换演进)。
        const onTurnDone = async (result, testCase) => { await reportRun(testCase.run_id, result, curWsTrace); };

        if (curWsTrace && curWsTrace.reset) curWsTrace.reset(); // 会话开始前清空 WS 收集器(为首轮),避免上一会话轨迹串进来
        try {
          if (testCases.length === 1) {
            // 单轮:走原有 runOne 路径(开干净对话→发送→扫列表判完成→抓取),行为与改前一致。
            let result;
            try { result = await curRunner.runOne(testCases[0]); }
            catch (e) { result = { success: false, incomplete: true, completeReason: 'exception', answer: `[执行异常] ${(e.message || '').split('\n')[0]}` }; }
            await onTurnDone(result, testCases[0]);
          } else {
            // 多轮:在同一对话内顺序连发(轮次0新建对话、后续轮复用),逐轮回写各自的 run。
            await curRunner.runConversationTurns(testCases, onTurnDone);
          }
        } catch (e) {
          logger.error(`会话(首轮 run ${head.run_id})执行异常: ${e.message}`);
        }
      }
      await pool.close();     // 断开 CDP（默认 keepClient，端口仍开着，下轮可直接连）
      return pending.length;
    };

    if (opts.once) { await runOnce(); return; }
    logger.info('平台模式常驻轮询(Ctrl-C 退出)...');
    for (;;) {
      try { await runOnce(); } catch (e) { logger.error(`轮询异常: ${e.message}`); }
      await new Promise(r => setTimeout(r, pollMs));
    }
  });

// ========== login 命令：录制登录态 ==========
program
  .command('login')
  .description('录制账号登录态')
  .requiredOption('-a, --account <name>', '账号名称')
  .option('-c, --config <path>', '配置文件路径', './config/default.config.js')
  .option('--cdp', 'CDP 模式：连接已开启的 Chrome（有头崩溃时用此模式）', false)
  .option('--cdp-url <url>', 'CDP 连接地址', 'http://127.0.0.1:9222')
  .option('--no-clear', 'CDP 模式录完后不清除 Chrome 登录态（默认清除，以便连续录多个账号）')
  .action(async (options) => {
    const config = require(path.resolve(options.config));
    const { chromium } = require('playwright');

    const accountPath = `./accounts/${options.account}.json`;
    fs.mkdirSync('./accounts', { recursive: true });

    // ---------- CDP 模式：连接外部已开的 Chrome ----------
    if (options.cdp) {
      logger.info(`🔐 CDP 模式录制账号【${options.account}】`);
      logger.info(`   连接地址: ${options.cdpUrl}`);
      logger.info('   请确认你已用带 --remote-debugging-port 的 Chrome 完成登录，按回车继续...');

      await new Promise(resolve => {
        process.stdin.resume();
        process.stdin.once('data', resolve);
      });

      let browser;
      try {
        browser = await chromium.connectOverCDP(options.cdpUrl);
      } catch (e) {
        logger.error(`连接 Chrome 失败: ${e.message}`);
        logger.error('请确认已执行启动命令，且 Chrome 正在运行。');
        process.exit(1);
      }

      const context = browser.contexts()[0];
      if (!context) {
        logger.error('未找到浏览器上下文，请在 Chrome 中打开至少一个页面后重试。');
        process.exit(1);
      }

      const storageState = await context.storageState();
      fs.writeFileSync(accountPath, JSON.stringify(storageState, null, 2));

      logger.success(`✅ 登录态已保存到 ${accountPath}`);

      // 连续录多个账号的关键：录完后清掉这个 Chrome 的登录态（cookies + 各页 localStorage/sessionStorage），
      // 并跳回登录页——否则同一 --user-data-dir 会复用上个账号，下一个账号连上去还是已登录态、录不了新号。
      // 若只想录一个、不希望清登录态，可加 --no-clear 跳过。
      if (options.clear !== false) {
        try {
          await context.clearCookies();
          for (const pg of context.pages()) {
            try {
              await pg.evaluate(() => { try { localStorage.clear(); sessionStorage.clear(); } catch (_) {} });
            } catch (_) { /* 个别页(如 about:blank/跨域)清不了，忽略 */ }
          }
          // 跳回登录页，方便直接登下一个账号
          const pg = context.pages()[0] || await context.newPage();
          try { await pg.goto(config.platform.chatUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }); } catch (_) {}
          logger.info('   🧹 已清除该 Chrome 的登录态并跳回登录页——可直接在同一窗口登录并录制下一个账号');
          logger.info(`      下一个：node bin\\ai-eval.js login -a <下一个账号名> --cdp`);
        } catch (e) {
          logger.warn(`   ⚠ 清除登录态失败（不影响本次保存）: ${(e.message || '').split('\n')[0]}`);
          logger.warn('      若要录下一个账号，请手动在 Chrome 里退出当前账号，或关掉 Chrome 删除 user-data-dir 后重开。');
        }
      }

      // 仅断开连接，不关闭用户的 Chrome
      await browser.close();
      return;
    }

    // ---------- 普通模式：弹窗录制 ----------
    logger.info(`🔐 开始录制账号【${options.account}】的登录态`);
    logger.info('   请在弹出的浏览器中完成登录，完成后回到命令行按回车');

    const browser = await chromium.launch({
      headless: false,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
    });
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(config.platform.chatUrl);

    await new Promise(resolve => {
      process.stdin.setRawMode(true);
      process.stdin.resume();
      process.stdin.once('data', () => {
        process.stdin.setRawMode(false);
        resolve();
      });
    });

    const storageState = await context.storageState();
    fs.writeFileSync(accountPath, JSON.stringify(storageState, null, 2));

    logger.success(`✅ 登录态已保存到 ${accountPath}`);
    await browser.close();
  });

// ========== list 命令：查看用例列表 ==========
program
  .command('list')
  .description('查看飞书表格中的测试用例')
  .option('-c, --config <path>', '配置文件路径', './config/default.config.js')
  .action(async (options) => {
    const config = require(path.resolve(options.config));
    const sheetReader = new FeishuSheetReader(config.feishu);

    logger.info('📋 读取测试用例...');
    const testCases = await sheetReader.getTestCases();

    testCases.forEach((tc, i) => {
      const attach = tc.attachments.length > 0 ? ` [附件×${tc.attachments.length}]` : '';
      console.log(`${String(i + 1).padStart(3)}. [${tc.caseId}] ${tc.question.slice(0, 40)}${attach}`);
    });
    logger.info(`\n共 ${testCases.length} 条用例`);
  });

program.parse(process.argv);
