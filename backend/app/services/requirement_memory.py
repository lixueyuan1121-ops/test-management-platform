"""Human-confirmed project context and deterministic review differences."""
import json
from app.models import RequirementBaseline, RequirementAnalysis

FIELDS = ("module", "platform", "condition", "action", "expected", "forbidden", "boundaries", "evidence")


def history(db, project_id, analysis_id, limit=20):
    return (db.query(RequirementBaseline, RequirementAnalysis)
            .join(RequirementAnalysis, RequirementAnalysis.id == RequirementBaseline.analysis_id)
            .filter(RequirementAnalysis.project_id == project_id)
            .order_by(RequirementBaseline.id.desc()).limit(limit).all())


def context(db, row):
    items = []
    for baseline, analysis in history(db, row.project_id, row.id, 10):
        if analysis.id == row.id or baseline.revision != analysis.revision:
            continue
        payload = json.loads(baseline.payload)
        for rule in payload.get("rules", []):
            if rule.get("status") != "confirmed":
                continue
            items.append({"baseline_id": baseline.id, "confirmed_by": baseline.confirmed_by,
                "scope": payload.get("scope", "")[:500], "title": rule["title"],
                "condition": rule.get("condition", "")[:500], "action": rule.get("action", "")[:500],
                "expected": rule.get("expected", "")[:500]})
            if len(items) >= 12:
                return items
    return items


def focus(db, row):
    if not row.draft:
        return None
    draft = json.loads(row.draft)
    prior = []
    for baseline, analysis in history(db, row.project_id, row.id):
        if baseline.analysis_id == row.id and baseline.revision >= row.revision:
            continue
        # Within the same document compare to the previous confirmed revision;
        # for other documents only use their current confirmed version.
        if analysis.id != row.id and baseline.revision != analysis.revision:
            continue
        payload = json.loads(baseline.payload)
        for rule in payload.get("rules", []):
            if rule.get("status") == "confirmed":
                prior.append((baseline, payload, rule))
    rules = []
    for rule in draft.get("rules", []):
        match = next(((b, p, r) for b, p, r in prior if r.get("title") == rule.get("title")
                      and r.get("module") == rule.get("module") and r.get("platform") == rule.get("platform")), None)
        changed = []
        item = {"id": rule["id"], "kind": "new", "changed_fields": changed}
        if match:
            baseline, payload, old = match
            changed.extend(k for k in FIELDS if rule.get(k) != old.get(k))
            if draft.get("scope") != payload.get("scope"):
                changed.append("scope")
            if [c["text"] for c in rule.get("criteria", [])] != [c["text"] for c in old.get("criteria", [])]:
                changed.append("criteria")
            item.update(kind="changed" if changed else "unchanged", baseline_id=baseline.id,
                        confirmed_by=baseline.confirmed_by, confirmed_at=baseline.created_at.isoformat())
        rules.append(item)
    return {"rules": rules, "unanswered": [q["id"] for q in draft.get("questions", []) if q.get("blocking") and not q.get("answer")],
            "inferred": [r["id"] for r in draft.get("rules", []) if r.get("source_type") == "inferred"],
            "note": "历史确认仅供比较；本次范围、图文依据与验收条件仍需确认，不自动沿用产品决定。"}
