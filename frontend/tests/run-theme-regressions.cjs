const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const cases = [
  'navigation-responsive', 'login-war-room', 'test-missions', 'workspace', 'eval-workspace', 'eval-results',
  'eval-dispatch-stop', 'eval-import-attachments', 'eval-dialog-matrix', 'eval-qwork',
  'eval-multica', 'eval-summary-status', 'generation', 'functional-workspace',
  'feedback-actions', 'feedback-results-import', 'performance-report',
  'admin-devices', 'admin-permissions', 'automation-tools-permissions', 'device-errors',
  'requirements-actions', 'release-checklist', 'release-reads', 'release-writes',
  'statistics-states', 'ai-wall', 'rts', 'fail-clusters', 'selector-states',
  'selector-actions', 'api-env', 'tool-admin', 'issues',
  'eval-dispatch-picker', 'exec-cancel', 'release-pagination-task-assignees',
  'selector-description', 'probe-save-status', 'selector-selection-export', 'locator-config',
];
const output = path.resolve(process.env.THEME_TEST_OUTPUT || 'artifacts/ui-themes-2026-10-05/regressions');
fs.mkdirSync(output, { recursive: true });
const retry = process.argv.includes('--retry-failed');
const results = retry && fs.existsSync(path.join(output, 'results.json')) ? JSON.parse(fs.readFileSync(path.join(output, 'results.json'), 'utf8')) : [];
for (const theme of ['fresh', 'tech']) {
  for (const name of cases) {
    const previous = results.findIndex(row => row.theme === theme && row.name === name);
    if (retry && previous >= 0 && results[previous].pass) continue;
    const result = spawnSync(process.execPath, ['--require', path.join(__dirname, 'theme-preload.cjs'), path.join(__dirname, name + '-ui.cjs')], {
      encoding: 'utf8', timeout: 180000,
      env: { ...process.env, PLAYWRIGHT_MODULE: require.resolve('../../tools/qalab-runner/eval/node_modules/playwright'), PLAYWRIGHT_PATH: require.resolve('../../tools/qalab-runner/eval/node_modules/playwright'), UI_THEME_TEST: theme, UI_BASE_URL: process.env.UI_BASE_URL || 'http://127.0.0.1:5189' },
    });
    fs.writeFileSync(path.join(output, theme + '-' + name + '.log'), (result.stdout || '') + (result.stderr || '') + (result.error ? String(result.error) : ''));
    const pass = result.status === 0;
    const entry = { theme, name, pass, status: result.status };
    if (previous >= 0) results[previous] = entry; else results.push(entry);
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
    console.log(`${pass ? 'PASS' : 'FAIL'} ${theme} ${name}`);
  }
}
console.log(`${results.filter(r => r.pass).length}/${results.length} passed. Logs: ${output}`);
process.exitCode = results.some(r => !r.pass) ? 1 : 0;
