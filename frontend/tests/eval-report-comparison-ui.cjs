// Offline report rendering only: no server, model calls or real evaluation runs.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright');
const { readFileSync, writeFileSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const runs = JSON.parse(readFileSync(path.join(__dirname, 'fixtures/eval-report-comparison.json')));
  const { buildEvalReportHtml } = await import('../src/utils/evalReportHtml.js');
  const task = { name: '多产品对比 · 综合评价示例', last_batch_id: 'b1', status: 'done', summary_status: 'done',
    summary_html: '<h2>综合评价</h2><p>此页使用本地示例数据验证排版。</p>' };
  const backend = execFileSync(path.join(root, 'backend/.venv/bin/python'), ['-c', `
import json, sys
from types import SimpleNamespace
from app.api.eval_report import render_report_page
from app.services.eval_report_details import render_query_details
rows = json.load(sys.stdin)
task = SimpleNamespace(name='多产品对比 · 综合评价示例', last_batch_id='b1', summary_status='done',
    summary_html='<h2>综合评价</h2><p>此页使用本地示例数据验证排版。</p>', summary_at=None,
    summary_provider='', detail_html=render_query_details(rows))
print(render_report_page(task))
`], { cwd: path.join(root, 'backend'), input: JSON.stringify(runs), encoding: 'utf8' });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [kind, html] of [['online', backend], ['export', buildEvalReportHtml({ task, groupedRuns: runs })]]) {
      writeFileSync(`/tmp/qalab-report-${kind}-preview.html`, html);
      const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.setContent(html);
      assert.equal(await page.locator('.qd-query').count(), 4);
      assert.deepEqual(await page.locator('[data-run-id]').evaluateAll(els => els.map(e => +e.dataset.runId)),
        [101, 105, 201, 301, 102, 202, 103, 203, 104, 204]);
      const first = page.locator('.qd-query').first();
      await first.getByText('查看完整 query', { exact: true }).click();
      assert(await first.getByText(runs[0].payload.prompt, { exact: true }).isVisible());
      const reason = page.locator('[data-run-id="101"] details');
      assert(!await reason.locator('.qd-reason').isVisible());
      await reason.locator('summary').click();
      assert(await reason.locator('.qd-reason').isVisible());
      assert.equal(await page.locator('a[href^="https://example.com/"]').count(), 3);
      await page.screenshot({ path: `/tmp/qalab-report-${kind}-desktop.png`, fullPage: true });
      for (const width of [1440, 768, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${kind}: body overflows at ${width}`);
        assert(await page.locator('.qd-scroll').evaluateAll(els => els.every(e => {
          const r = e.getBoundingClientRect(); return r.x >= 0 && r.right <= innerWidth;
        })), `${kind}: comparison table wrapper outside viewport`);
        if (width === 390) {
          const scroller = first.locator('.qd-scroll');
          assert(await scroller.evaluate(el => el.scrollWidth > el.clientWidth));
          await scroller.evaluate(el => { el.scrollLeft = el.scrollWidth; });
          assert(await reason.locator('.qd-reason').isVisible());
          await scroller.evaluate(el => { el.scrollLeft = 0; });
          await page.screenshot({ path: `/tmp/qalab-report-${kind}-mobile.png`, fullPage: true });
        }
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('PASS online + export: paired product rows, every trial/turn retained, expandable query/reason, links, desktop/tablet/mobile contained scrolling');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
