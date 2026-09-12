"""Explicit denominators shared by evaluation reports and comparison views."""
from collections import Counter


def outcome_metrics(rows):
    rows = list(rows)
    total = len(rows)
    verdicts = Counter(r.verdict for r in rows)
    states = Counter(getattr(r.status, "value", r.status) for r in rows)
    passed, failed = verdicts["pass"], verdicts["fail"]
    judged = passed + failed
    completed = sum(states[s] for s in ("done", "judged", "judging"))
    rate = lambda n, d: round(n / d * 100, 1) if d else None
    scores = [r.score for r in rows if r.verdict in ("pass", "fail") and r.score is not None]
    config_errors = sum(getattr(r.status, "value", r.status) == "failed" and
                        str(r.reason or "").startswith("[CONFIG_ERROR]") for r in rows)
    evidence_missing = sum(r.verdict == "error" and any(word in str(getattr(r, "verdict_reason", "") or "")
        for word in ("证据不足", "无法定论", "未采集", "未取得")) for r in rows)
    return {"total": total, "judged": judged, "passed": passed, "failed": failed,
        "pass_rate": rate(passed, judged), "coverage_rate": rate(judged, total),
        "confirmed_success_rate": rate(passed, total), "completion_rate": rate(completed, total),
        "completed": completed, "judge_errors": verdicts["error"],
        "insufficient_evidence": evidence_missing, "judge_service_errors": verdicts["error"] - evidence_missing,
        "config_errors": config_errors, "execution_errors": states["failed"] - config_errors,
        "cancelled": states["cancelled"], "pending": states["pending"], "running": states["running"],
        "missing": states["missing"],
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None}
