"""判定使用下发时的基准；旧记录仅在字段缺失时回落题库。"""
import json

from app.models import EvalQuery, EvalRun

JUDGE_RULES_VERSION = "context-v4-multiturn"

def payload_of(run):
    try:
        value = json.loads(getattr(run, "payload", None) or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _turn_index(payload):
    value = payload.get("turn_index")
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 10:
        return int(value)
    return None


def context_of(db, run):
    """Only prior turns from the same frozen execution group may inform a judgment."""
    payload = payload_of(run)
    query = db.get(EvalQuery, run.eval_query_id) if run.eval_query_id and "prompt" not in payload else None
    turn_index = _turn_index(payload)
    context = {"prompt": payload.get("prompt", query.prompt if query else ""),
               "attachments": payload.get("attachments", []),
               "turn_index": turn_index, "previous_turns": [],
               "context_available": "prompt" in payload or query is not None}
    group = payload.get("conversation_group")
    if not group and not turn_index:
        return context
    if group and turn_index == 0:
        if "dimension" in payload:
            context["continuity"] = "first"
        return context
    context["continuity"] = "missing_history"
    if not group or turn_index is None or not getattr(run, "batch_id", None):
        return context
    rows = db.query(EvalRun).filter(
        EvalRun.project_id == run.project_id, EvalRun.batch_id == run.batch_id,
        EvalRun.eval_task_id == run.eval_task_id, EvalRun.target_device == run.target_device,
        EvalRun.target_engine == run.target_engine, EvalRun.id != run.id).all()
    for prior in rows:
        p = payload_of(prior)
        if prior.claim_token and run.claim_token and prior.claim_token != run.claim_token:
            continue
        if (p.get("conversation_group") != group
                or p.get("compare_group") != payload.get("compare_group")
                or p.get("configuration_id") != payload.get("configuration_id")
                or p.get("trial_index", 1) != payload.get("trial_index", 1)):
            continue
        index = _turn_index(p)
        if index is None or index >= turn_index:
            continue
        context["previous_turns"].append({"run_id": prior.id, "turn_index": index,
            "prompt": p.get("prompt", ""), "answer": prior.answer or "",
            "attachments": p.get("attachments", []),
            "status": getattr(prior.status, "value", prior.status)})
    context["previous_turns"].sort(key=lambda item: (item["turn_index"], item["run_id"]))
    history = context["previous_turns"]
    if len(history) == turn_index and all(
        turn["turn_index"] == i and turn["status"] in ("done", "judged", "judging")
        and str(turn["prompt"] or "").strip() and turn["answer"].strip()
        for i, turn in enumerate(history)
    ):
        context["continuity"] = "follow_up"
    return context


def rubric_of(db, run):
    payload = payload_of(run)
    query = None
    if run.eval_query_id and ("expected" not in payload or "dimension" not in payload):
        query = db.get(EvalQuery, run.eval_query_id)
    expected = payload.get("expected", query.expected if query else "") or ""
    dimension = payload.get("dimension", query.dimension if query else None)
    return expected, dimension
