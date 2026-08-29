"""性能红线守卫(perf 阈值告警,优化项):采集完成时逐指标比对报告集红线,超线推飞书。

对标 Lighthouse CI assertions / Grafana alerting 的阈值形态,收敛到报告集级配置:
- 阈值存 perf_report_set.thresholds_json:{metricKey: {max?: n, min?: n}}。
- metricKey 与前端 perf-report-logic 的 KPI 键对齐(cpuPeak/cpuAvg/memPeak/memDelta/
  memTrendMB/gpuPeak/ttftMs/wsRtt/ping 越低越好用 max;fpsAvg 越高越好用 min)。
- 指标值取自 run.meta_json.summary(runner 已算好的摘要,不重算)。
- 只在 run completed 且归属带红线的报告集时检查;违规推一张飞书卡(通知通道未配置静默)。
"""
import json
import logging

logger = logging.getLogger("test_platform")

# metricKey → (meta.summary 取值路径, 单位, 越低越好?)。镜像前端 perf-report-logic 的 KPI 表。
METRIC_DEFS: dict[str, tuple[tuple[str, ...], str, bool]] = {
    "ttftMs":     (("net", "ttftMs"),   "ms", True),
    "cpuPeak":    (("cpu", "peak"),     "%",  True),
    "cpuAvg":     (("cpu", "avg"),      "%",  True),
    "memDelta":   (("mem", "delta"),    "MB", True),
    "memPeak":    (("mem", "peak"),     "MB", True),
    "memTrendMB": (("memTrendMB",),     "MB", True),
    "gpuPeak":    (("gpu", "peak"),     "%",  True),
    "fpsAvg":     (("fps", "avg"),      "",   False),
    "wsRtt":      (("net", "wsRttAvg"), "ms", True),
    "ping":       (("net", "pingAvg"),  "ms", True),
}

METRIC_LABELS = {
    "ttftMs": "首token", "cpuPeak": "CPU峰值", "cpuAvg": "CPU均值",
    "memDelta": "内存增量", "memPeak": "内存峰值", "memTrendMB": "内存趋势",
    "gpuPeak": "GPU峰值", "fpsAvg": "平均FPS", "wsRtt": "WS延迟", "ping": "ping",
}


def _get_metric(summary: dict, path: tuple[str, ...]):
    cur = summary
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, (int, float)) else None


def parse_thresholds(raw: str | None) -> dict:
    """thresholds_json → {metricKey: {max?, min?}};坏 JSON/非法结构回 {}(不告警不报错)。"""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        if k not in METRIC_DEFS or not isinstance(v, dict):
            continue
        rule = {}
        for bound in ("max", "min"):
            if isinstance(v.get(bound), (int, float)):
                rule[bound] = float(v[bound])
        if rule:
            out[k] = rule
    return out


def check_violations(summary: dict | None, thresholds: dict) -> list[dict]:
    """逐指标比对,返回违规清单 [{key,label,value,bound,limit,unit}]。指标缺失跳过(不误报)。"""
    if not isinstance(summary, dict) or not thresholds:
        return []
    out = []
    for key, rule in thresholds.items():
        path, unit, _low_good = METRIC_DEFS[key]
        val = _get_metric(summary, path)
        if val is None:
            continue
        if "max" in rule and val > rule["max"]:
            out.append({"key": key, "label": METRIC_LABELS.get(key, key), "value": round(val, 2),
                        "bound": "max", "limit": rule["max"], "unit": unit})
        if "min" in rule and val < rule["min"]:
            out.append({"key": key, "label": METRIC_LABELS.get(key, key), "value": round(val, 2),
                        "bound": "min", "limit": rule["min"], "unit": unit})
    return out


def guard_perf_run(db, run) -> list[dict]:
    """入口:run completed 后调。返回违规清单(空=达标/未设红线),并推飞书告警。

    失败静默(告警是旁路,不影响采集回写主流程)。
    """
    try:
        if run.status != "completed" or not run.report_set_id:
            return []
        from app.models import PerfReportSet
        s = db.get(PerfReportSet, run.report_set_id)
        if not s:
            return []
        thresholds = parse_thresholds(s.thresholds_json)
        if not thresholds:
            return []
        try:
            meta = json.loads(run.meta_json) if run.meta_json else {}
        except (json.JSONDecodeError, ValueError):
            meta = {}
        violations = check_violations(meta.get("summary"), thresholds)
        if violations:
            _notify(run, s, violations)
        return violations
    except Exception:
        logger.exception("性能红线检查失败(不影响回写)")
        return []


def _notify(run, report_set, violations: list[dict]) -> None:
    from app.services import notify

    if not notify.is_enabled():
        return
    lines = [
        f"**报告集**：{notify._esc(report_set.name)}",
        f"**场景**：{notify._esc(run.scenario)}　**对象**：{notify._esc(run.variant)}　**设备**：{notify._esc(run.runner)}",
        f"**超线指标（{len(violations)} 项）**：",
    ]
    for v in violations[:8]:
        op = ">" if v["bound"] == "max" else "<"
        lines.append(f"• {v['label']}：{v['value']}{v['unit']} {op} 红线 {v['limit']}{v['unit']}")
    notify.send_card(
        title="性能红线告警",
        lines=lines, color=notify.COLOR_RED,
        link_path="/perf-report", link_text="查看性能报告",
    )
