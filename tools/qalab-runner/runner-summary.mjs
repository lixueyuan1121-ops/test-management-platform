// 本次 tick 执行的一批结果汇总(供 runner 结束语)。纯函数,便于单测。
// verdict:pass=通过 / fail=功能失败 / blocked=选择器·环境阻塞。
// 兼容:runner 回写前 selector 失败可能是 {verdict:"fail",fail_kind:"selector"},此处也归 blocked。
export function summarizeBatch(results) {
  const rs = Array.isArray(results) ? results : [];
  let passed = 0, failed = 0, blocked = 0, ms = 0;
  for (const r of rs) {
    ms += Number(r?.duration_ms) || 0;
    const isBlocked = r?.verdict === "blocked" || r?.fail_kind === "selector";
    if (r?.verdict === "pass") passed++;
    else if (isBlocked) blocked++;
    else failed++;
  }
  const parts = [`共 ${rs.length} 条`, `${passed} 过`, `${failed} 失`];
  if (blocked) parts.push(`${blocked} 阻塞`);
  parts.push(`耗时 ${(ms / 1000).toFixed(1)}s`);
  return { total: rs.length, passed, failed, blocked, duration_ms: ms, text: `本批完成: ${parts.join(" · ")}` };
}
