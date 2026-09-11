"""对话测评执行机的引擎心跳；时间与设备心跳一致，使用应用 UTC。"""
from sqlalchemy.exc import IntegrityError

from app.models.runner_device import RunnerEvalHeartbeat
from app.services.eval_engines import DEFAULT_ENGINE, runner_supported_engines


def touch_eval_engine(db, device, engine, now):
    # 旧执行器不传 engine，按原协议视为纳米Work；非法值不覆盖已有声明。
    engine = engine or DEFAULT_ENGINE
    supported = runner_supported_engines(engine)
    if not supported:
        return
    device.eval_engine = engine  # 最近的能力声明，workbuddy = 纳米Work + WorkBuddy
    for supported_engine in supported:
        query = db.query(RunnerEvalHeartbeat).filter_by(device_id=device.id, engine=supported_engine)
        if query.update({"last_seen_at": now}, synchronize_session=False):
            continue
        try:
            # 首次启动可能并发注册；只回滚插入，保留本次设备心跳事务。
            with db.begin_nested():
                db.add(RunnerEvalHeartbeat(device_id=device.id, engine=supported_engine, last_seen_at=now))
                db.flush()
        except IntegrityError:
            query.update({"last_seen_at": now}, synchronize_session=False)


def eval_engines_by_device(db, devices, cutoff):
    """按有效心跳判定能力；WorkBuddy 心跳同时证明默认的纳米Work能力。"""
    from app.models import EvalRun
    from app.db.clock import db_now
    from app.services.run_activity import live_run_filter

    by_id = {d.id: set() for d in devices}
    if not by_id:
        return by_id
    tracked = set()
    for row in db.query(RunnerEvalHeartbeat).filter(RunnerEvalHeartbeat.device_id.in_(by_id)).all():
        tracked.add(row.device_id)
        if row.last_seen_at >= cutoff:
            by_id[row.device_id].update(runner_supported_engines(row.engine))
    # 升级后第一次心跳到来前，沿用历史能力声明，workbuddy 也包含默认纳米Work。
    for d in devices:
        if d.id not in tracked:
            by_id[d.id].update(runner_supported_engines(d.eval_engine))

    by_name = {}
    for d in devices:
        by_name.setdefault(d.runner_id, []).append(d.id)
    for did, runner, engine in db.query(EvalRun.runner_device_id, EvalRun.runner, EvalRun.target_engine).filter(
            live_run_filter(EvalRun, db_now(db))).distinct().all():
        engine = engine or DEFAULT_ENGINE
        ids = [did] if did is not None else by_name.get(runner, [])
        # 旧共享身份重名时无法确定实际设备，不把同一条任务算到多台机器。
        if len(ids) == 1 and ids[0] in by_id:
            by_id[ids[0]].update(runner_supported_engines(engine))
    return by_id
