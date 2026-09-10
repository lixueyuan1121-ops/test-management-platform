"""Shared execution liveness policy for scheduling and device statistics."""
from datetime import timedelta

from sqlalchemy import and_, func, or_


def live_run_filter(model, now):
    # New runners send per-run heartbeats. Legacy runners are bounded by the
    # existing reaper timeout, never kept online by an arbitrary old running row.
    hours = 2 if model.__tablename__ == "exec_run" else 6
    uses_heartbeat = model.heartbeat_at.isnot(None)
    if model.__tablename__ == "eval_run":
        uses_heartbeat = and_(uses_heartbeat, model.claim_token.isnot(None))
    return and_(model.status == "running", or_(
        and_(uses_heartbeat, model.heartbeat_at >= now - timedelta(minutes=3)),
        and_(~uses_heartbeat,
             func.coalesce(model.started_at, model.updated_at) >= now - timedelta(hours=hours)),
    ))
