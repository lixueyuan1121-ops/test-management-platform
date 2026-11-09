// Isolated UI contract test. The connected physical phones are covered by the runner separately.
const { chromium } = require('../../tools/qalab-runner/eval/node_modules/playwright')
const assert = require('node:assert/strict')

;(async () => {
  const base = process.env.UI_BASE_URL || 'http://127.0.0.1:5194'
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    await page.addInitScript(() => localStorage.setItem('tp_token', 'isolated-ui-test'))
    const errors = [], writes = []
    page.on('pageerror', error => errors.push(error.message))
    const devices = ['android', 'ios'].map((platform, i) => ({
      id: i + 1, runner_id: platform + '-runner', name: platform, platform,
      mobile_target: { platform, online: true, model: platform, build: '101' },
    }))
    const sample = { id: 1, title: '从历史恢复会话', kind: 'gui', platform: 'mobile', mobile_scenario: 'history',
      mobile_contract: { title: '从历史恢复会话' }, hash: 'test-hash', criterion_ids: ['r'], provenance: [] }
    const mission = { id: 7, project_id: 1, goal: '检查双端会话', revision: 1, phase: 'awaiting_approval',
      events: [], policy: { mobile_dialog: true, max_cases: 4 }, plan: { cases: [sample],
        quality: { status: 'passed', version: 'mission-quality-v1', cases: [{ case_id: 1, case_hash: 'test-hash', findings: [] }] } } }
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), path = new URL(req.url()).pathname
      if (req.method() !== 'GET') writes.push({ path, body: req.postDataJSON() })
      let data = []
      if (path.endsWith('/auth/me')) data = { user: { id: 1, name: 'test' }, is_platform_admin: true, memberships: [] }
      if (path.endsWith('/projects')) data = [{ id: 1, name: 'QA' }]
      if (path.endsWith('/devices')) data = devices
      if (path.endsWith('/test-missions')) data = [mission]
      if (path.endsWith('/test-missions/7') || path.endsWith('/decisions')) data = mission
      const rows = devices.map((device, i) => ({
        run_id: i + 1, case_id: 1, batch_id: 'sample', title: sample.title,
        status: i ? 'failed' : 'passed', verdict: i ? 'fail' : 'pass',
        runner: device.runner_id, mobile_target: device.mobile_target, kind: 'gui', has_report: true,
      }))
      if (path.endsWith('/exec-queue/history')) data = { items: rows, total: 2, page: 1, page_size: 20, runners: [] }
      if (path.endsWith('/exec-queue/2')) data = { ...rows[1], report: [{ desc: '历史核验', ok: false }] }
      await route.fulfill({ json: { code: 0, data } })
    })
    await page.goto(base + '/test-plans')
    await page.getByRole('button', { name: '新建计划', exact: true }).click()
    await page.getByPlaceholder('如 每日冒烟回归').fill('双端回归')
    await page.getByText('双真机对话', { exact: true }).click()
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    const expected = { android: 'android-runner', ios: 'ios-runner' }
    assert.deepEqual(writes.at(-1).body.mobile_targets, expected)
    assert.equal(writes.at(-1).body.seed_mobile_smoke, false)
    await page.goto(base + '/commander?project_id=1&mission=7')
    await page.getByText('我已核对所选用例的前置、步骤、预期及验收关联', { exact: false }).click()
    await Promise.all([page.waitForResponse(res => res.url().endsWith('/decisions')),
      page.getByRole('button', { name: '确认方案并开始执行' }).click()])
    assert.deepEqual(writes.at(-1).body.mobile_targets, expected)
    assert.equal(writes.at(-1).body.save_mobile_plan, true)
    await page.goto(base + '/exec-results?project_id=1&batch_id=sample')
    await page.getByText('0 / 1 条双端通过').waitFor()
    const matrix = page.locator('.mobile-pairs')
    await matrix.getByText('查看步骤与证据').last().click()
    await page.getByRole('dialog', { name: '执行报告' }).getByText('历史核验').waitFor()
    assert.deepEqual(errors, [])
    console.log('PASS 双端目标授权、计划创建、默认选机和分端步骤报告')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
