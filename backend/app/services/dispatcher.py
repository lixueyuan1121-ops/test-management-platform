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


def touch_runner_heartbeat(db: Session, runner_id: str | None) -> int:
    """共享 token 拉取时,按 runner_id 反查登记设备并刷新 last_seen_at。返回刷新条数。

    根因修复:心跳原本只在设备 token 分支(ctx.device 直接刷)更新,共享 token 拉取
    从不更新任何设备心跳。于是「用共享 token 正常工作的设备」在调度眼里永远离线——
    pick_runner 不选它、reassign 误判它离线抢走 pending、离线巡检误报、看板误显示离线。
    统一口径:任何 token 的拉取都代表「该 runner_id 的机器活着」,据字符串反查刷心跳。

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
    db.commit()
    return len(devices)


def online_eval_runners(db: Session) -> list[str]:
    """在线的对话测评执行机 runner_id 列表(测评分片下发用)。

    与 exec 侧 _online_devices 同「在线」口径(ONLINE_WINDOW_SEC 内有心跳),但:
    - 不按 platform 过滤(对话测评是桌面客户端,无移动端平台之分);
    - 「忙=在线」用 EvalRun 的 running(不是 ExecRun),因为测评执行期设备也不轮询队列、
      心跳会滞后,正在跑测评的设备必然活着,与设备看板 eval 计数同源。
    返回按 runner_id 升序(稳定),供轮转分片时确定性分配。
    """
    from app.api.devices import ONLINE_WINDOW_SEC
    from app.core.enums import EvalRunStatus
    from app.models import EvalRun

    devices = db.query(RunnerDevice).all()
    if not devices:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    busy = {r for (r,) in db.query(EvalRun.runner)
            .filter(EvalRun.status == EvalRunStatus.running).distinct().all()}
    online = [d.runner_id for d in devices
              if (d.last_seen_at and d.last_seen_at >= cutoff) or d.runner_id in busy]
    return sorted(set(online))


def _online_devices(db: Session, platform: str | None = None) -> list[RunnerDevice]:
    """在线设备列表(与看板同口径:窗口内有心跳,或有 running)。platform 传入时精确匹配。"""
    from app.api.devices import ONLINE_WINDOW_SEC

    q = db.query(RunnerDevice)
    if platform:
        q = q.filter(RunnerDevice.platform == platform)
    devices = q.all()
    if not devices:
        return []
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SEC)
    busy_runners = {r for (r,) in db.query(ExecRun.runner)
                    .filter(ExecRun.status == "running").distinct().all()}
    return [d for d in devices
            if (d.last_seen_at and d.last_seen_at >= cutoff) or d.runner_id in busy_runners]


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
