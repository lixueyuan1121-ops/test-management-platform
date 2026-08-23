const fs = require('fs');
const path = require('path');

class ResultReporter {
  constructor(outputConfig) {
    this.saveJson = outputConfig.saveJson !== false;
    this.saveSummary = outputConfig.saveSummary !== false;
    this.outputDir = outputConfig.outputDir || './output';
  }

  summarize(results) {
    const successCount = results.filter(r => r.success).length;
    const totalDuration = results.reduce((sum, r) => sum + r.durationMs, 0);
    const avgDuration = results.length > 0 ? totalDuration / results.length : 0;
    const durations = results.map(r => r.durationMs);

    return {
      total: results.length,
      success: successCount,
      failed: results.length - successCount,
      successRate: results.length > 0
        ? ((successCount / results.length) * 100).toFixed(2) + '%'
        : '0%',
      avgDurationMs: Math.round(avgDuration),
      totalDurationMs: totalDuration,
      maxDurationMs: durations.length > 0 ? Math.max(...durations) : 0,
      minDurationMs: durations.length > 0 ? Math.min(...durations) : 0
    };
  }

  async saveOutput(results, summary) {
    const now = new Date();
    const ts = now.toISOString().replace(/[T:]/g, '_').slice(0, 16);
    const dir = path.resolve(this.outputDir, ts);
    fs.mkdirSync(dir, { recursive: true });

    if (this.saveJson) {
      fs.writeFileSync(
        path.join(dir, 'result.json'),
        JSON.stringify({ summary, results }, null, 2),
        'utf-8'
      );
    }

    if (this.saveSummary) {
      const md = this._buildSummaryMd(summary, results);
      fs.writeFileSync(path.join(dir, 'summary.md'), md, 'utf-8');
    }

    return dir;
  }

  _buildSummaryMd(summary, results) {
    const incomplete = results.filter(r => r.success && r.missingFields && r.missingFields.length > 0);
    const lines = [
      '# 测评执行汇总',
      '',
      `- 执行时间: ${new Date().toLocaleString('zh-CN')}`,
      `- 总用例数: ${summary.total}`,
      `- 成功: ${summary.success} | 失败: ${summary.failed}`,
      `- 成功率: ${summary.successRate}`,
      `- 字段缺失(成功但有回填字段为空): ${incomplete.length}`,
      `- 平均耗时: ${(summary.avgDurationMs / 1000).toFixed(2)}s`,
      `- 最快: ${(summary.minDurationMs / 1000).toFixed(2)}s | 最慢: ${(summary.maxDurationMs / 1000).toFixed(2)}s`,
      '',
      '## 详细结果',
      '',
      '| 用例ID | 账号 | 状态 | 缺失字段 | 耗时 | 回答摘要 |',
      '|--------|------|------|----------|------|----------|'
    ];

    for (const r of results) {
      const status = r.success ? '✅ 通过' : '❌ 失败';
      const missing = (r.missingFields && r.missingFields.length) ? r.missingFields.join('/') : '-';
      const duration = (r.durationMs / 1000).toFixed(2) + 's';
      const excerpt = (r.answer || '').slice(0, 50).replace(/\n/g, ' ');
      lines.push(`| ${r.caseId || '-'} | ${r.account || '-'} | ${status} | ${missing} | ${duration} | ${excerpt} |`);
    }

    return lines.join('\n');
  }
}

module.exports = ResultReporter;
