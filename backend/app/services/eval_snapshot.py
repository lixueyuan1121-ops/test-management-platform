"""判定使用下发时的基准；旧记录仅在字段缺失时回落题库。"""
import json

from app.models import EvalQuery


def rubric_of(db, run):
    try:
        payload = json.loads(getattr(run, "payload", None) or "{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    query = None
    if run.eval_query_id and ("expected" not in payload or "dimension" not in payload):
        query = db.get(EvalQuery, run.eval_query_id)
    expected = payload.get("expected", query.expected if query else "") or ""
    dimension = payload.get("dimension", query.dimension if query else None)
    return expected, dimension
