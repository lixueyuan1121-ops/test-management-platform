"""判定使用下发时的基准；旧记录仅在字段缺失时回落题库。"""
import json

from app.models import EvalQuery, EvalRun


def payload_of(run):
    try:
        value = json.loads(getattr(run, "payload", None) or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def context_of(db, run):
    """Only prior turns from the same frozen execution group may inform a judgment."""
    payload = payload_of(run)
    query = db.get(EvalQuery, run.eval_query_id) if run.eval_query_id and "prompt" not in payload else None
    try:
        turn_index = max(0, int(payload.get("turn_index") or 0))
    except (TypeError, ValueError):
        turn_index = 0
    context = {"prompt": payload.get("prompt", query.prompt if query else ""),
               "attachments": payload.get("attachments", []),
               "turn_index": turn_index, "previous_turns": [],
               "context_available": "prompt" in payload or query is not None}
    group = payload.get("conversation_group")
    if not group or not getattr(run, "batch_id", None):
        return context
    rows = db.query(EvalRun).filter(
        EvalRun.project_id == run.project_id, EvalRun.batch_id == run.batch_id,
        EvalRun.target_engine == run.target_engine, EvalRun.id != run.id).all()
    for prior in rows:
        p = payload_of(prior)
        if (p.get("conversation_group") != group
                or p.get("compare_group") != payload.get("compare_group")
                or p.get("trial_index", 1) != payload.get("trial_index", 1)):
            continue
        index = p.get("turn_index", 0)
        if not isinstance(index, int) or index >= context["turn_index"]:
            continue
        context["previous_turns"].append({"run_id": prior.id, "turn_index": index,
            "prompt": p.get("prompt", ""), "answer": prior.answer or "",
            "attachments": p.get("attachments", []),
            "status": getattr(prior.status, "value", prior.status)})
    context["previous_turns"].sort(key=lambda item: (item["turn_index"], item["run_id"]))
    return context


def rubric_of(db, run):
    payload = payload_of(run)
    query = None
    if run.eval_query_id and ("expected" not in payload or "dimension" not in payload):
        query = db.get(EvalQuery, run.eval_query_id)
    expected = payload.get("expected", query.expected if query else "") or ""
    dimension = payload.get("dimension", query.dimension if query else None)
    return expected, dimension
