// scripts/verify-workbuddy-pool.js —— 真机验证：能 attach + 拿到就绪 page（不发对话，零积分）
const { WorkbuddyPool } = require('../src/workbuddy-pool');
const logger = { info: (m) => console.log(m), warn: (m) => console.warn(m), error: (m) => console.error(m) };
(async () => {
  const pool = new WorkbuddyPool({ cdpPort: 9335 }, logger);
  await pool.init();
  const page = pool.getMainPage();
  const hasInput = await page.locator('[contenteditable="true"][role="textbox"]').count();
  console.log(`[assert] mainPage url = ${page.url().slice(0,60)}`);
  console.log(`[assert] 输入框数量 = ${hasInput}（应 >= 1）`);
  if (!hasInput) { console.error('FAIL: 未找到输入框'); process.exit(1); }
  await pool.close();  // 只断连
  console.log('PASS: WorkbuddyPool attach + page 就绪');
  process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
