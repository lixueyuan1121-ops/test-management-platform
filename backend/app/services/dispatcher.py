"""设备池自动调度(建议项⑩):按平台挑在线空闲设备 + 离线设备 pending 改派。

对标 TestRail/LambdaTest 的 agent pool 与 Selenium Grid 的按能力匹配,收敛到
本仓库的轻形态:runner="auto" 时платform匹配 + 在线优先 + 负载最小;
调度器周期把「派给离线设备的 pending」改派到同平台在线设备(无可用则原地等待)。

在线判定与设备看板同口径(ONLINE_WINDOW_SEC 内有心跳,或有 running 在跑)。
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ExecRun, RunnerDevice

logger = logging.getLogger("test_platform")

AUTO_RUNNER = "auto"   # enqueue 传这个值即触发自动挑设备


def _exec_running_runners(db: Session) -> set[str]:
    """当前有 running 功能测试(exec_run)的 runner 集合。执行期设备不轮询队列,
    last_exec_at 会滞后,用 running 补偿:正在跑功能用例的机必然在跑功能 runner。"""
    return {r for (r,) in db.query(ExecRun.runner)
            .filter(ExecRun.status == "running").distinct().all()}


def _eval_running_runners(db: Session) -> set[str]:
    """当前有 running 对话测评(eval_run)的 runner 集合(补偿测评执行期心跳滞后)。"""
    from app.core.enums import EvalRunStatus
    from app.models import EvalRun
    return {r for (r,) in db.query(EvalRun.runner)
            .filter(EvalRun.status == EvalRunStatus.running).distinct().all()}


def current_kind(d: RunnerDevice, cutoff: datetime,
                 exec_running: set, eval_running: set) -> str | None:
    """设备此刻在跑哪类 runner:'func'(功能)/ 'eval'(测评)/ None(未启动任何 runner)。

    一台机同时刻只能跑一类(抢同一客户端不能并行),故返回【单一】类型:
    - 功能 runner 轮询 exec-queue 刷 last_exec_at、测评 runner 轮询 eval-queue 刷 last_eval_at;
    - 两个时间戳都在在线窗口内(切换 runner 的重叠瞬间)→ 取【更晚】的那个 = 当前真正在跑的,
      使切换后立即反映最新(而非 3 分钟内两类都显示,困惑用户);
    - 都过期 → running 补偿(执行期不轮询、心跳滞后,有 running 必在跑对应 runner);
    - 全无 → None(空闲)。看板/派单/手动拦截统一据此,口径一致。
    """
    exec_fresh = bool(d.last_exec_at and d.last_exec_at >= cutoff)
    eval_fresh = bool(d.last_eval_at and d.last_eval_at >= cutoff)
    if exec_fresh and eval_fresh:
        return "eval" if d.last_eval_at >= d.last_exec_at else "func"
    if exec_fresh:
        return "func"
    if eval_fresh:
        return "eval"
    if d.runner_id in exec_running:
        return "func"
    if d.runner_id in eval_running:
        return "eval"
    return None


def device_conflicts_kind(db: Session, runner_id: str, needed_kind: str) -> bool:
    """手动下发拦截判据:目标设备当前在跑的 runner 与 needed_kind 冲突(在跑另一类)。

    needed_kind: 'exec'(功能)/ 'eval'(测评)。一台机同时刻只能跑一类;若它此刻在跑另一类
    runner,下发本类任务它拉不到、必卡住 → 拦截(True)。
    - 未登记设备 → False(不拦,兼容旧共享 token runner);
    - 空闲(没启动任何 runner)→ False(不拦,用户可能随后启动对应 runner);
    - 正在跑本类 → False(不拦)。
    """
    dev = db.query(RunnerDevice).filter(RunnerDevice.runner_id == runner_id).first()
    if dev is None:
        return False   # 未登记,不拦
    from app.api.devices import ONLINE_WINDOW_SEC
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    cur = current_kind(dev, cutoff, _exec_running_runners(db), _eval_running_runners(db))
    want = "func" if needed_kind == "exec" else "eval"
    return cur is not None and cur != want


def touch_runner_heartbeat(db: Session, runner_id: str | None, kind: str | None = None) -> int:
    """共享 token 拉取时,按 runner_id 反查登记设备并刷新心跳。返回刷新条数。

    根因修复:心跳原本只在设备 token 分支(ctx.device 直接刷)更新,共享 token 拉取
    从不更新任何设备心跳。于是「用共享 token 正常工作的设备」在调度眼里永远离线——
    pick_runner 不选它、reassign 误判它离线抢走 pending、离线巡检误报、看板误显示离线。
    统一口径:任何 token 的拉取都代表「该 runner_id 的机器活着」,据字符串反查刷心跳。

    kind:本次拉取来自哪类队列 —— 'exec'(功能 runner 拉 exec-queue)刷 last_exec_at、
    'eval'(测评 runner 拉 eval-queue)刷 last_eval_at,None(如 perf)只刷 last_seen_at。
    据此运行时感知「该机当前在跑哪类 runner」(看板显示/精准派单/手动拦截)。

    - 未登记(纯老 runner,无 RunnerDevice 行)→ 反查为空、无副作用,行为完全不变;
    - 同名 runner_id 跨 owner 多台 → 全部刷新(与看板/调度按 runner_id 聚合的既有口径一致,
      本无法区分是哪台物理机在拉);
    - 设备 token 分支不调本函数(它已有 ctx.device 精确刷,且不该被同名设备误连带)。
    """
    if not runner_id:
        return 0
    devices = db.query(RunnerDevice).filter(RunnerDevice.runner_id == runner_id).all()
    if not devices:
        return 0
    now = datetime.utcnow()
    for d in devices:
        d.last_seen_at = now
        if kind == "exec":
            d.last_exec_at = now
        elif kind == "eval":
            d.last_eval_at = now
    db.commit()
    return len(devices)


def online_eval_runners(db: Session) -> list[str]:
    """在线且【当前在跑测评 runner】的执行机 runner_id 列表(测评分片下发用)。

    运行时感知:一台机同时刻只能跑一类 runner(功能/测评抢同一客户端不能并行),故「能接测评」
    = 它此刻正跑测评 runner —— 判据:last_eval_at 在在线窗口内(测评 runner 每 5s 轮询 eval-queue),
    或有 running 的 eval_run(执行期不轮询、心跳滞后,用 running 补偿)。这样测评任务只会派到
    真正在跑测评 runner 的机,从根上杜绝「派到只跑功能测试的机器」。
    返回按 runner_id 升序(稳定),供轮转分片时确定性分配。
    """
    from app.api.devices import ONLINE_WINDOW_SEC

    devices = db.query(RunnerDevice).all()
    if not devices:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    exec_running = _exec_running_runners(db)
    eval_running = _eval_running_runners(db)   # 执行期心跳滞后补偿:正在跑测评的机必然在跑测评 runner
    online = [d.runner_id for d in devices
              if current_kind(d, cutoff, exec_running, eval_running) == "eval"]
    return sorted(set(online))


def _online_devices(db: Session, platform: str | None = None) -> list[RunnerDevice]:
    """在线且【当前在跑功能 runner】的设备列表(功能测试点 exec_run 派单/改派专用)。

    运行时感知:功能 runner 每 5s 轮询 exec-queue 刷 last_exec_at;当前在跑类型 = current_kind
    (切换重叠期取更晚的时间戳,执行期用 running 补偿)。只挑当前在跑功能 runner 的机,避免落到
    「此刻在跑测评 runner」或「没启动任何 runner」的机器。platform 传入时按被测端平台精确匹配。
    """
    from app.api.devices import ONLINE_WINDOW_SEC

    q = db.query(RunnerDevice)
    if platform:
        q = q.filter(RunnerDevice.platform == platform)
    devices = q.all()
    if not devices:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    exec_running = _exec_running_runners(db)
    eval_running = _eval_running_runners(db)
    return [d for d in devices
            if current_kind(d, cutoff, exec_running, eval_running) == "func"]


def _load_of(db: Session, runner_ids: list[str]) -> dict[str, int]:
    """各 runner 的当前负载(pending+running 条数)。"""
    if not runner_ids:
        return {}
    rows = (db.query(ExecRun.runner, func.count(ExecRun.id))
            .filter(ExecRun.runner.in_(runner_ids),
                    ExecRun.status.in_(["pending", "running"]))
            .group_by(ExecRun.runner).all())
    return {r: n for r, n in rows}


def pick_runner(db: Session, platform: str = "web") -> str | None:
    """自动挑设备:同平台在线设备里选负载(pending+running)最小的;并列取 id 小的(稳定)。

    无在线同平台设备 → None(调用方决定报错还是回落)。
    """
    candidates = _online_devices(db, platform or "web")
    if not candidates:
        return None
    load = _load_of(db, [d.runner_id for d in candidates])
    best = min(candidates, key=lambda d: (load.get(d.runner_id, 0), d.id))
    return best.runner_id


def reassign_stranded_runs(db: Session) -> int:
    """把「派给离线设备的 pending」改派到同平台在线且负载最小的设备。返回改派条数。

    - 只动 pending(running 表示设备曾活着认领过,可能还会回写,不抢);
    - 目标设备离线才改派(在线设备的 pending 它自己会拉,不折腾);
    - 无同平台在线设备 → 原地等待(设备上线自然消化,不盲目改派);
    - 改派 reason 打标留痕(不覆盖既有 reason——pending 本无 reason)。
    """
    pending_runners = [r for (r,) in db.query(ExecRun.runner)
                       .filter(ExecRun.status == "pending").distinct().all()]
    if not pending_runners:
        return 0
    # 每台待判定 runner:查设备与在线态
    devices = {d.runner_id: d for d in db.query(RunnerDevice)
               .filter(RunnerDevice.runner_id.in_(pending_runners)).all()}
    online_ids = {d.runner_id for d in _online_devices(db)}
    moved = 0
    for rid in pending_runners:
        dev = devices.get(rid)
        if dev is None:
            continue   # 未登记设备(共享 token 旧 runner):无平台信息,不动
        if rid in online_ids:
            continue   # 目标设备在线:自己会消化
        target = pick_runner(db, dev.platform)
        if not target or target == rid:
            continue   # 无可改派目标:原地等
        n = (db.query(ExecRun)
             .filter(ExecRun.runner == rid, ExecRun.status == "pending")
             .update({"runner": target,
                      "reason": f"[自动改派] 原设备 {rid} 离线,改派到 {target}"},
                     synchronize_session=False))
        moved += n
        logger.info("自动改派 %d 条 pending: %s → %s(原设备离线)", n, rid, target)
    if moved:
        db.commit()
    return moved
