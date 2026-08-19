// 复制自 D:\git\test\nami-perfdog\report-logic.mjs —— 保持胜负/KPI/维度口径与本地报告一致，勿在此改逻辑。
// 报表纯计算逻辑（Node 可单测；同一份源码由 renderReportHtml 剥离 export 后内联进 HTML）。
'use strict';

// 冷启动"墙钟"取数：可用(readyMs) > 手动确认(manualMs) > 首屏 > 进程出现
function coldOf(m) {
  const d = m.durations || {};
  return d.readyMs != null ? d.readyMs : (d.manualMs != null ? d.manualMs : (d.firstPaintMs != null ? d.firstPaintMs : (d.procSpawnMs != null ? d.procSpawnMs : null)));
}

// 维度定义 + 胜负方向（lowGood: true 越低越好 / false 越高越好 / null 中性不判胜负）。改这里即可调方向。
var DIMENSIONS = [
  { key: 'totalMs',    label: '总耗时/墙钟', unit: 'ms', lowGood: true,  get: function (m) { return m.durations && m.durations.totalMs; } },
  { key: 'coldTotal',  label: '冷启动',      unit: 'ms', lowGood: true,  get: coldOf },
  { key: 'activateMs', label: '激活',        unit: 'ms', lowGood: true,  get: function (m) { return m.durations && m.durations.activateMs; } },
  { key: 'closeMs',    label: '关闭',        unit: 'ms', lowGood: true,  get: function (m) { return m.durations && m.durations.closeMs; } },
  { key: 'ttftMs',     label: '首token',     unit: 'ms', lowGood: true,  get: function (m) { return m.summary && m.summary.net && m.summary.net.ttftMs; } },
  { key: 'cpuPeak',    label: 'CPU峰值',     unit: '%',  lowGood: true,  get: function (m) { return m.summary && m.summary.cpu && m.summary.cpu.peak; } },
  { key: 'cpuAvg',     label: 'CPU均值',     unit: '%',  lowGood: true,  get: function (m) { return m.summary && m.summary.cpu && m.summary.cpu.avg; } },
  { key: 'memDelta',   label: '内存增量',    unit: 'MB', lowGood: true,  get: function (m) { return m.summary && m.summary.mem && m.summary.mem.delta; } },
  { key: 'memPeak',    label: '内存峰值',    unit: 'MB', lowGood: true,  get: function (m) { return m.summary && m.summary.mem && m.summary.mem.peak; } },
  { key: 'memTrendMB', label: '内存趋势',    unit: 'MB', lowGood: true,  get: function (m) { return m.summary && m.summary.memTrendMB; } },
  { key: 'gpuPeak',    label: 'GPU峰值',     unit: '%',  lowGood: true,  get: function (m) { return m.summary && m.summary.gpu && m.summary.gpu.peak; } },
  { key: 'fpsAvg',     label: '平均FPS',     unit: '',   lowGood: false, get: function (m) { return m.summary && m.summary.fps && m.summary.fps.avg; } },
  { key: 'wsRtt',      label: 'WS延迟',      unit: 'ms', lowGood: true,  get: function (m) { return m.summary && m.summary.net && m.summary.net.wsRttAvg; } },
  { key: 'ping',       label: 'ping',        unit: 'ms', lowGood: true,  get: function (m) { return m.summary && m.summary.net && m.summary.net.pingAvg; } },
  { key: 'sampleCount', label: '事件总量',   unit: '',   lowGood: null,  get: function (m) { return m.sampleCount; } },
];

function fmtVal(v, unit) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  if (unit === 'ms') return Math.round(v) + 'ms';
  if (unit === 'MB') return (v > 0 ? '+' : '') + v + 'MB';
  if (unit === '%') return v + '%';
  return String(v);
}

// 两对象某维度对比。winner: 'A'|'B'|'tie'|null（缺值或中性维度）。
// fasterPct=优者比劣者好的整数百分比(封顶99，含非正值时为 null)；foldRatio=悬殊时优者是劣者的倍数(否则 null)。
function cmp2(a, b, dim) {
  var aVal = dim.get(a), bVal = dim.get(b);
  var okA = typeof aVal === 'number' && isFinite(aVal);
  var okB = typeof bVal === 'number' && isFinite(bVal);
  var ratio = (okA && okB && bVal !== 0) ? +(aVal / bVal).toFixed(2) : null;
  if (!okA || !okB || dim.lowGood == null) return { aVal: aVal, bVal: bVal, ratio: ratio, winner: null, fasterPct: null, foldRatio: null };
  var lo = Math.min(aVal, bVal), hi = Math.max(aVal, bVal);
  var diffPct = hi === 0 ? 0 : Math.round((hi - lo) / hi * 100);
  if (diffPct < 5) return { aVal: aVal, bVal: bVal, ratio: ratio, winner: 'tie', fasterPct: diffPct, foldRatio: null };
  var aWins = dim.lowGood ? aVal < bVal : aVal > bVal;
  var winner = aWins ? 'A' : 'B';
  // 含非正值(≤0)：百分比/倍数无意义，只判胜负
  if (lo <= 0) return { aVal: aVal, bVal: bVal, ratio: ratio, winner: winner, fasterPct: null, foldRatio: null };
  // 悬殊(优者≤劣者一半)：改用倍数表述，避免"快99%~100%"这类荒谬百分比
  var fold = +(hi / lo).toFixed(hi / lo >= 10 ? 0 : 1);
  if (fold >= 2) return { aVal: aVal, bVal: bVal, ratio: ratio, winner: winner, fasterPct: Math.min(diffPct, 99), foldRatio: fold };
  return { aVal: aVal, bVal: bVal, ratio: ratio, winner: winner, fasterPct: diffPct, foldRatio: null };
}

// KPI 绝对判定阈值（仅用于速览色点）。
var KPI_THRESH = {
  coldTotal: { t: [5000, 10000], lowGood: true }, activateMs: { t: [500, 1500], lowGood: true },
  closeMs: { t: [1000, 3000], lowGood: true }, totalMs: { t: [null, null], lowGood: true },
  cpuPeak: { t: [50, 80], lowGood: true }, memDelta: { t: [100, 400], lowGood: true },
  memTrendMB: { t: [30, 100], lowGood: true }, fpsAvg: { t: [55, 30], lowGood: false },
};
function rateOf(key, v) {
  var c = KPI_THRESH[key]; if (!c || typeof v !== 'number') return '';
  var a = c.t[0], b = c.t[1]; if (a == null || b == null) return '';
  if (c.lowGood) return v < a ? 'good' : (v <= b ? 'mid' : 'bad');
  return v >= a ? 'good' : (v >= b ? 'mid' : 'bad');
}

// payload(loadSessions 后数组) → [{scenario, objects:[{variant,meta,samples}]}]；组内同 variant 取最近一次。
function groupByScenario(payload) {
  var byS = new Map();
  for (var i = 0; i < payload.length; i++) {
    var s = payload[i];
    var sc = (s.meta && s.meta.scenario) || '对话';
    var v = (s.meta && s.meta.variant) || 'default';
    if (!byS.has(sc)) byS.set(sc, new Map());
    var cur = byS.get(sc).get(v);
    if (!cur || (s.meta.startedAt || 0) >= (cur.meta.startedAt || 0)) byS.get(sc).set(v, s);
  }
  var out = [];
  byS.forEach(function (m, scenario) {
    var objects = [];
    m.forEach(function (s) { objects.push({ variant: s.meta.variant || 'default', meta: s.meta, samples: s.samples }); });
    out.push({ scenario: scenario, objects: objects });
  });
  return out;
}

// 对"恰好2对象"组逐维度 cmp2，生成人话对比短句。
function winnerText(dim, r) {
  var verb = dim.lowGood ? '快' : '高';
  if (r.foldRatio != null) return verb + r.foldRatio + '×';   // 悬殊用倍数
  if (r.fasterPct != null) return verb + r.fasterPct + '%';   // 常规用百分比
  return '更优';                                               // 含非正值：只标更优
}

function buildSummaryLines(groups) {
  var lines = [];
  for (var i = 0; i < groups.length; i++) {
    var g = groups[i];
    if (g.objects.length !== 2) continue;
    var A = g.objects[0], B = g.objects[1];
    for (var j = 0; j < DIMENSIONS.length; j++) {
      var dim = DIMENSIONS[j];
      if (dim.lowGood == null) continue;
      var r = cmp2(A.meta, B.meta, dim);
      if (r.winner === 'A' || r.winner === 'B') {
        var win = r.winner === 'A' ? A.variant : B.variant;
        lines.push(win + ' 在【' + g.scenario + '】' + dim.label + winnerText(dim, r));
      }
    }
  }
  return lines;
}

// ── 版本排序：大者为新版 ───────────────────────────────────────
function parseVer(s) { return String(s).split('.').map(function (x) { return parseInt(x, 10) || 0; }); }
function cmpVer(a, b) {
  var A = parseVer(a), B = parseVer(b), n = Math.max(A.length, B.length);
  for (var i = 0; i < n; i++) { var d = (A[i] || 0) - (B[i] || 0); if (d) return d; }
  return 0;
}
function orderAB(objects) {
  var s = objects.slice().sort(function (x, y) { return cmpVer(y.variant, x.variant); }); // 版本大在前
  return { A: s[0], B: s[1] };
}

// ── 每场景主指标 → 新版胜负 ─────────────────────────────────────
var PRIMARY_BY_SCENARIO = { '冷启动': 'coldTotal', '首次安装': 'totalMs', '热启动': 'activateMs', '杀进程': 'closeMs', '长监控': 'memTrendMB', '对话': 'cpuPeak', '切换对话': 'cpuPeak' };

function dimByKey(key) { for (var i = 0; i < DIMENSIONS.length; i++) if (DIMENSIONS[i].key === key) return DIMENSIONS[i]; return null; }

// 返回 {A(新),B(旧),dim,cmp,newWins:'win'|'lose'|'tie'} 或 null（非2对象/主指标缺值）。
function sceneVerdict(scene) {
  if (!scene || scene.objects.length !== 2) return null;
  var dim = dimByKey(PRIMARY_BY_SCENARIO[scene.scenario] || 'cpuPeak');
  if (!dim) return null;
  var ab = orderAB(scene.objects);
  if (!ab.A || !ab.B) return null;
  var c = cmp2(ab.A.meta, ab.B.meta, dim);
  if (c.winner == null) return null; // 主指标缺值 → 不判该场景
  var newWins = c.winner === 'A' ? 'win' : (c.winner === 'B' ? 'lose' : 'tie');
  return { A: ab.A, B: ab.B, dim: dim, cmp: c, newWins: newWins };
}

// ── 全局结论（单段，不显比分，方向从数据现算）──────────────────
function buildVerdict(groups) {
  var verdicts = [];
  for (var i = 0; i < groups.length; i++) { var v = sceneVerdict(groups[i]); if (v) verdicts.push(Object.assign({ scenario: groups[i].scenario }, v)); }
  if (!verdicts.length) return null;
  var wins = verdicts.filter(function (v) { return v.newWins === 'win'; });
  var loses = verdicts.filter(function (v) { return v.newWins === 'lose'; });
  var mag = function (v) { return v.cmp.foldRatio != null ? v.cmp.foldRatio : (v.cmp.fasterPct != null ? v.cmp.fasterPct / 100 : 0); };
  var A = verdicts[0].A.variant, B = verdicts[0].B.variant;
  var tone, toneWord;
  if (wins.length > loses.length) { tone = (wins.length - loses.length) >= Math.ceil(verdicts.length / 2) ? 'better' : 'slightly-better'; toneWord = tone === 'better' ? '整体更优' : '整体略优'; }
  else if (wins.length < loses.length) { tone = 'worse'; toneWord = '整体退步'; }
  else { tone = 'mixed'; toneWord = '互有胜负'; }
  var parts = ['新版 ' + A + ' 相比旧版 ' + B + ' ' + toneWord + '。'];
  // 维度 label 与场景名相同时（如冷启动主指标 label 也叫"冷启动"）去重，避免"【冷启动】冷启动慢"
  var labelOf = function (v) { return v.dim.label === v.scenario ? '' : v.dim.label; };
  if (wins.length) { var best = wins.slice().sort(function (a, b) { return mag(b) - mag(a); })[0]; parts.push('最大提升【' + best.scenario + '】' + labelOf(best) + winnerText(best.dim, best.cmp) + '。'); }
  if (loses.length) {
    var worst = loses.slice().sort(function (a, b) { return mag(b) - mag(a); })[0];
    var w = worst.cmp, word = worst.dim.lowGood ? '慢' : '差';
    var amt = w.foldRatio != null ? (word + w.foldRatio + '×') : (w.fasterPct != null ? (word + w.fasterPct + '%') : '更差');
    parts.push('最大退步【' + worst.scenario + '】' + labelOf(worst) + amt + '，建议关注。');
  }
  return { text: parts.join(''), tone: tone };
}

// ── 速览卡：2对象出 新值/旧值/差值；单对象出单值 ────────────────
function pickKpis(groups) {
  var out = [];
  for (var i = 0; i < groups.length; i++) {
    var g = groups[i];
    if (g.objects.length === 2) {
      var v = sceneVerdict(g); if (!v) continue;
      var aVal = v.dim.get(v.A.meta), bVal = v.dim.get(v.B.meta);
      var delta = (typeof aVal === 'number' && typeof bVal === 'number') ? +(aVal - bVal).toFixed(2) : null;
      var word = v.dim.lowGood ? (delta > 0 ? '慢' : '快') : (delta > 0 ? '高' : '低');
      var amt = v.cmp.foldRatio != null ? (v.cmp.foldRatio + '×') : (v.cmp.fasterPct != null ? (v.cmp.fasterPct + '%') : '');
      var absStr = delta == null ? '—' : (delta > 0 ? '+' : '-') + fmtVal(Math.abs(delta), v.dim.unit).replace(/^\+/, '');
      var deltaText = absStr + (amt ? '（' + word + amt + '）' : '');
      var rate = v.newWins === 'lose' ? 'bad' : (v.newWins === 'win' ? 'good' : 'mid');
      out.push({ scenario: g.scenario, label: v.dim.label, unit: v.dim.unit, aVer: v.A.variant, bVer: v.B.variant, aVal: aVal, bVal: bVal, delta: delta, deltaText: deltaText, rate: rate });
    } else {
      var first = g.objects[0]; if (!first) continue;
      var key = PRIMARY_BY_SCENARIO[g.scenario] || 'cpuPeak';
      var dim = dimByKey(key); if (!dim) continue;
      var val = dim.get(first.meta);
      if (typeof val !== 'number' || !isFinite(val)) continue;
      out.push({ scenario: g.scenario, label: dim.label, value: val, unit: dim.unit, rate: rateOf(key, val), single: true });
    }
  }
  return out;
}

export { DIMENSIONS, coldOf, fmtVal, cmp2, winnerText, rateOf, groupByScenario, buildSummaryLines, pickKpis, parseVer, cmpVer, orderAB, PRIMARY_BY_SCENARIO, sceneVerdict, buildVerdict };
