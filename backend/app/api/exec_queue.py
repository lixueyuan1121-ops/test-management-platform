"""执行队列路由：勾选用例下发 → runner 拉取/认领/回写 的闭环。

四个接口（详见 tools/qalab-runner/HANDOFF.md）：
- POST /api/exec-queue/enqueue   前端「发送到本地执行」按钮调；用户 JWT + 项目 member/admin。
- GET  /api/exec-queue           runner 拉取 pending；runner token。
- POST /api/exec-queue/{id}/claim runner 认领防重跑；runner token。
- PATCH /api/exec-queue/{id}      runner 回写 pass/fail，并同步 checklist_item.exec_status；runner token。

沿用全项目约定：{code,msg,data} 信封（ok/fail）、手写 _to_out、体外 assert_project_role。
"""
import json
import os
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ChecklistStatus, ExecKind, ExecStatus, ProjectRole
from app.db.session import get_db
from app.models import ChecklistItem, ExecRun, RunnerDevice, TestCase, User
from app.schemas.common import ok
from app.schemas.exec_queue import EnqueueExecIn, EnqueueCasesIn, ExecReportIn, ExecCorrectIn

router = APIRouter(prefix="/api/exec-queue", tags=["exec-queue"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def notify_batch_if_done(db: Session, batch_id: str) -> None:
    """批次完成检测 + 飞书告警钩子（reaper / runner 回写均可调）。

    只对 auto 触发的 feedback 批次生效（定时回归需有人知道失败）；manual 批次用户在页面上看着不必打扰。
    - 批次状态："所有 run 都已终态"才算完成（pending/running 仍在→不发）。
    - 失败才告警：全 passed 不发；有 failed/blocked 才推卡片。
    - 幂等：允许重复调用（reaper 收口多条、runner 同批最后几条并发回写），飞书已去重靠通道总开关控制频率。
    """
    from app.services import notify

    if not notify.is_enabled() or not batch_id:
        return
    # 先判完成：该批有 pending/running → 批次未完，直接返回
    pending_or_running = (
        db.query(ExecRun)
        .filter(ExecRun.batch_id == batch_id,
                ExecRun.status.in_([ExecStatus.pending, ExecStatus.running]))
        .first()
    )
    if pending_or_running:
        return
    # 凑齐终态了，查 feedback_run 确认 trigger
    from app.models import FeedbackRun, Project
    fr = db.query(FeedbackRun).filter(FeedbackRun.batch_id == batch_id).first()
    if not fr or fr.trigger != "auto":
        return  # manual 批次或无 feedback_run 元数据 → 不发
    # 聚合结果
    runs = db.query(ExecRun).filter(ExecRun.batch_id == batch_id).all()
    if not runs:
        return
    total = len(runs)
    passed = sum(1 for r in runs if r.status == ExecStatus.passed)
    failed = sum(1 for r in runs if r.status == ExecStatus.failed)
    blocked = sum(1 for r in runs if r.status == ExecStatus.blocked)
    if failed <= 0 and blocked <= 0:
        return  # 全 passed 不发
    proj = db.get(Project, runs[0].project_id)
    # 提取失败用例标题（payload 快照里的 title）
    failed_titles = []
    for r in runs:
        if r.status in (ExecStatus.failed, ExecStatus.blocked):
            try:
                p = json.loads(r.payload or "{}")
                t = p.get("title") or f"用例#{r.test_case_id or '?'}"
                failed_titles.append(t)
            except (json.JSONDecodeError, ValueError):
                failed_titles.append(f"run#{r.id}")
    notify.notify_batch_result(
        batch_id=batch_id,
        project_name=proj.name if proj else f"项目#{runs[0].project_id}",
        total=total, passed=passed, failed=failed, blocked=blocked,
        trigger="auto", failed_titles=failed_titles,
    )


def _new_batch_id() -> str:
    """一次 enqueue 的批次号:YYYYmmdd-HHMMSS-<4hex>,人读友好 + 同批唯一。"""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _get_runner_platform(db: Session, runner_id: str, owner_id: int | None = None) -> str | None:
    """取 runner 设备的 platform；若未登记（旧 runner/未注册设备）返回 None 不阻塞。

    优先按 (runner_id, owner_id) 精确匹配当前用户的设备；若 owner_id 未传则全局首条兜底，
    但此时只在明确移动端时才参与校验（见 _check_platform），避免跨用户同名设备误判。
    """
    q = db.query(RunnerDevice).filter(RunnerDevice.runner_id == runner_id)
    if owner_id is not None:
        rd = q.filter(RunnerDevice.owner_id == owner_id).first()
        if rd:
            return rd.platform
    # 兜底：全局找同名设备（可能跨用户）；只返回明确移动端值，web 统一视为不阻塞
    rd = q.first()
    return rd.platform if rd else None


def _check_platform(runner_id: str, tc_platform: str, db: Session, owner_id: int | None = None) -> None:
    """派单前校验 runner 平台 vs 用例平台；仅在双方均为移动端且不一致时拒绝。

    规则：
    - 未登记设备（rd_platform=None）→ 不阻塞（旧 runner/外部 runner 向后兼容）
    - 任一方为 web → 不阻塞（存量 PC 用例默认 web，不影响现有 PC 端流程）
    - 双方都是移动端（android/ios）且不同 → 400 拒绝（如 android 用例发给 ios 设备）
    """
    rd_platform = _get_runner_platform(db, runner_id, owner_id)
    if rd_platform is None:
        return   # 未登记设备，不阻塞
    if rd_platform == "web" or tc_platform == "web":
        return   # 任一方为 PC/Web，不阻塞
    if rd_platform != tc_platform:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"用例平台({tc_platform})与执行机平台({rd_platform})不匹配，请选择同平台设备",
        )


# test_case.exec_kind（若存在）→ ExecKind；缺省 gui。exec_kind 列由 migrate 补，
# 老库/未设值的用例回落到 gui（GUI 是被测客户端的主要形态）。
def _kind_of(tc: TestCase | None) -> ExecKind:
    raw = getattr(tc, "exec_kind", None) if tc else None
    try:
        return ExecKind(raw) if raw else ExecKind.gui
    except ValueError:
        return ExecKind.gui


def _payload_of(tc: TestCase | None, db: Session) -> dict:
    """把用例快照成 runner/Claude 要用的 payload（steps/expected/title/params + 结构化 script）。

    api 用例额外带 api_env 快照（base_url/auth）——执行器确定性执行需要，
    且用"下发那一刻的配置快照"避免执行时配置漂移（见设计稿 §6.4）。
    """
    if not tc:
        return {}
    # script 落库是 JSON 字符串;runner 的 StepExecutor 需要**数组**(Array.isArray 判定)。
    # 解析回对象放进 payload;解析失败/无 script 则给 None(runner 回退 claude 兜底)。
    script = None
    raw = getattr(tc, "script", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                script = parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            script = None
    payload = {
        "test_case_id": tc.id,
        "title": tc.title,
        "category": tc.category,
        "steps": tc.steps,
        "expected": tc.expected,
        "priority": tc.priority,
        "script": script,
        "project_id": tc.project_id,   # runner 按此拉该项目的合并选择器注册表(DB 单源)
    }
    # 仅 api 用例注入 api_env 快照（省 payload 体积;执行不需要 contract）。
    if _kind_of(tc) == ExecKind.api:
        from app.services.api_env import get_api_env
        env = get_api_env(db, tc.project_id) or {}
        payload["api_env"] = {
            "base_url": env.get("base_url", ""),
            "auth_type": env.get("auth_type", "fixed"),
            "auth": env.get("auth", {}),
        }
    return payload


def _to_out(r: ExecRun) -> dict:
    return {
        "run_id": r.id,
        "checklist_item_id": r.checklist_item_id,
        "case_id": r.test_case_id,     # 对齐 runner 端字段名 case_id
        "test_case_id": r.test_case_id,
        "task_id": r.task_id,
        "project_id": r.project_id,
        "batch_id": r.batch_id,
        "runner": r.runner,
        # 防御:kind/status 正常是枚举(有 .value),但历史/脏数据可能是裸字符串;
        # 用 getattr 兼容两者,避免一行坏数据让 runner 的 GET 轮询整个 500(实测踩过)。
        "kind": getattr(r.kind, "value", r.kind),
        "status": getattr(r.status, "value", r.status),
        "payload": json.loads(r.payload or "{}"),
        "verdict": r.verdict,
        "fail_kind": r.fail_kind,
        "reason": r.reason,
        "evidence_url": r.evidence_url,
        "report": _load_report(r.report),
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _load_report(raw):
    """report TEXT-JSON → 对象(数组/字典);空或坏 JSON → None(前端回落旧证据展示)。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


# ---- ① 前端「发送到本地执行」：把勾选的清单项入队 ----
@router.post("/enqueue")
def enqueue(
    body: EnqueueExecIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把选中的验收清单项下发到指定 runner。项目 member/admin 可操作。

    整体校验：任一清单项不存在或跨项目 → 400，整批拒绝。
    """
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)

    ids = list(dict.fromkeys(body.checklist_item_ids))  # 去重保序
    items = db.query(ChecklistItem).filter(ChecklistItem.id.in_(ids)).all()
    found = {it.id: it for it in items}
    for cid in ids:
        it = found.get(cid)
        if it is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"清单项 {cid} 不存在")
        if it.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"清单项 {cid} 不属于该项目")

    created = []
    batch_id = _new_batch_id()   # 本次下发一个批次号,该批所有 run 共享(结果页按批汇总)
    for cid in ids:
        it = found[cid]
        tc = db.get(TestCase, it.test_case_id)
        if _kind_of(tc) == ExecKind.manual:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"清单项 {cid} 对应用例为『人工/不可自动化(manual)』,不能下发到执行机",
            )
        tc_platform = getattr(tc, "platform", "web") or "web"
        _check_platform(body.runner, tc_platform, db, owner_id=user.id)
        row = ExecRun(
            checklist_item_id=it.id,
            test_case_id=it.test_case_id,
            task_id=it.task_id,
            project_id=it.project_id,
            batch_id=batch_id,
            runner=body.runner,
            kind=_kind_of(tc),
            status=ExecStatus.pending,
            payload=json.dumps(_payload_of(tc, db), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row)
        db.flush()
        created.append(row.id)
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


@router.post("/enqueue-cases")
def enqueue_cases(
    body: EnqueueCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回归执行:直接按用例 id 下发,不经验收清单(不依赖任务/采纳)。

    与 /enqueue 的区别:ExecRun.checklist_item_id=None(runner 回写时不回流清单,见 report 的判空);
    task_id 取用例自带的(可为 None)。整体校验:任一用例不存在/跨项目/为 manual → 400 整批拒绝。
    """
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)

    ids = list(dict.fromkeys(body.test_case_ids))  # 去重保序
    cases = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    for cid in ids:
        tc = found.get(cid)
        if tc is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不存在")
        if tc.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不属于该项目")
        if _kind_of(tc) == ExecKind.manual:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"用例 {cid} 为『人工/不可自动化(manual)』,不能下发到执行机",
            )
        tc_platform = getattr(tc, "platform", "web") or "web"
        _check_platform(body.runner, tc_platform, db, owner_id=user.id)

    created = []
    batch_id = _new_batch_id()   # 回归批次号,该批所有 run 共享(结果页按批汇总)
    for cid in ids:
        tc = found[cid]
        row = ExecRun(
            checklist_item_id=None,          # 回归执行不挂清单项 → 回写不回流清单
            test_case_id=tc.id,
            task_id=getattr(tc, "task_id", None),
            project_id=tc.project_id,
            batch_id=batch_id,
            runner=body.runner,
            kind=_kind_of(tc),
            status=ExecStatus.pending,
            payload=json.dumps(_payload_of(tc, db), ensure_ascii=False),
            enqueued_by=user.id,
        )
        db.add(row)
        db.flush()
        created.append(row.id)
    db.commit()
    return ok({"run_ids": created, "batch_id": batch_id})


# ---- 执行历史查询(用户侧,独立"执行结果"页用)----
# exec_run 每次执行一行、不覆盖;这里按条件查全部历史,支持复测追溯。
@router.get("/history")
def list_history(
    project_id: int = Query(...),
    task_id: int | None = Query(None),
    runner: str | None = Query(None),
    verdict: str | None = Query(None),        # pass / fail
    status_: str | None = Query(None, alias="status"),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(ExecRun).filter(ExecRun.project_id == project_id)
    if task_id is not None:
        q = q.filter(ExecRun.task_id == task_id)
    if runner:
        q = q.filter(ExecRun.runner == runner)
    if verdict:
        q = q.filter(ExecRun.verdict == verdict)
    if status_:
        q = q.filter(ExecRun.status == status_)
    rows = q.order_by(ExecRun.id.desc()).limit(limit).all()

    # 批量补用例标题(payload 里有 title,优先用;缺失再查 test_case),避免 N+1
    out = []
    for r in rows:
        title = None
        try:
            title = (json.loads(r.payload or "{}") or {}).get("title")
        except (json.JSONDecodeError, ValueError):
            title = None
        d = _to_out(r)
        d["title"] = title
        d["enqueued_by"] = r.enqueued_by
        out.append(d)
    return ok(out)


# ---- ② runner 拉取待执行 ----
@router.get("")
def list_pending(
    runner: str = Query("mac-01"),
    limit: int = Query(5, le=20),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    # 设备 token:runner 锁定为该设备的 runner_id(忽略 query,防拿他人 token 冒充别的设备);
    # 共享 token(兜底):沿用 query 的 runner。
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow()   # 记录设备活跃
        db.commit()
    rows = (
        db.query(ExecRun)
        .filter(ExecRun.status == ExecStatus.pending, ExecRun.runner == runner)
        .order_by(ExecRun.id)
        .limit(limit)
        .all()
    )
    return ok([_to_out(r) for r in rows])


# ---- ③ runner 认领（防重跑）----
@router.post("/{run_id}/claim")
def claim(
    run_id: int,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ExecRun, run_id)
    if not r or r.status != ExecStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该执行项不可认领")
    # 归属校验：只能认领派给自己的执行项，避免多台 runner 串扰
    # （设备 token 下 runner 已锁定为设备 runner_id;共享 token 下靠 query runner 区分）。
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    r.status = ExecStatus.running
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


# ---- ④ runner 回写结果，并同步验收清单项状态 ----
@router.patch("/{run_id}")
def report(
    run_id: int,
    body: ExecReportIn,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ExecRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    # 归属校验：只能回写派给自己的执行项（见 claim 说明）。
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")

    is_pass = body.verdict == "pass"
    # L2 失败分类:fail_kind=selector(选择器/环境阻塞)记 blocked,不计入功能失败率;
    # business 或缺 fail_kind(旧 runner)记 failed(真功能 bug)。pass 照常 passed。
    is_blocked = (not is_pass) and body.fail_kind == "selector"
    r.verdict = "blocked" if is_blocked else body.verdict
    r.fail_kind = body.fail_kind
    r.status = ExecStatus.passed if is_pass else (ExecStatus.blocked if is_blocked else ExecStatus.failed)
    r.reason = body.reason
    r.evidence_url = body.evidence_url
    r.duration_ms = body.duration_ms
    if body.report is not None:
        r.report = json.dumps(body.report, ensure_ascii=False)   # 逐步执行报告(含截图 URL)

    # 闭环落点:把结果同步回验收清单项(pass 通过 / selector 阻塞记 blocked / 其余 fail 记 failed)。
    # 复用现有清单展示、checklist-summary 统计、失败转遗留问题等下游能力。
    if r.checklist_item_id:
        item = db.get(ChecklistItem, r.checklist_item_id)
        if item:
            item.exec_status = (ChecklistStatus.passed if is_pass
                                else ChecklistStatus.blocked if is_blocked
                                else ChecklistStatus.failed)
            item.executed_by = None  # 机器自动执行，无归属用户
            item.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    # 批次完成检测钩子：reaper 和 runner 回写都调，幂等，只对 auto 触发的 feedback 批次生效
    if r.batch_id:
        try:
            notify_batch_if_done(db, r.batch_id)
        except Exception:
            pass  # 通知失败不影响回写主流程
    return ok(_to_out(r))


# ---- ⑥ 人工纠偏执行结果（用户 JWT，非 runner）----
# 机器判定可能误判（如误报 blocked、把真 bug 判过），人工复核后可修正结果。三态 pass/fail/blocked，
# reason 打「[人工纠偏]」前缀留痕，并同步 checklist_item.exec_status（与 runner 回写同一套映射）。
_CORRECT_MARK = "[人工纠偏]"


@router.patch("/{run_id}/verdict")
def correct_verdict(
    run_id: int,
    body: ExecCorrectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """人工修正一条执行结果。项目 admin/member 可操作（按 run 所属项目鉴权）。

    verdict 三态:pass→passed;blocked→selector 环境阻塞;fail→business 真 bug。
    reason 存「[人工纠偏] <备注>」;同步回填清单项 exec_status(有清单项时)。
    """
    r = db.get(ExecRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)

    is_pass = body.verdict == "pass"
    is_blocked = body.verdict == "blocked"
    # 与 report 端点一致:blocked→fail_kind=selector;fail→business;pass→None。
    r.verdict = body.verdict
    r.fail_kind = None if is_pass else ("selector" if is_blocked else "business")
    r.status = ExecStatus.passed if is_pass else (ExecStatus.blocked if is_blocked else ExecStatus.failed)
    note = (body.reason or "").strip()
    r.reason = f"{_CORRECT_MARK} {note}" if note else _CORRECT_MARK

    # 同步清单项(与 report 端点同一映射);裸执行记录(无清单项)跳过。
    if r.checklist_item_id:
        item = db.get(ChecklistItem, r.checklist_item_id)
        if item:
            item.exec_status = (ChecklistStatus.passed if is_pass
                                else ChecklistStatus.blocked if is_blocked
                                else ChecklistStatus.failed)
            item.executed_by = user.id      # 人工纠偏:记纠偏人(区别于机器执行的 None)
            item.executed_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


# ---- ⑦ runner 上传执行截图（二进制,独立于 report TEXT 通道)----
# 同 probe:截图大,base64 塞 report TEXT 会撑爆 MySQL 5.6 的 64KB。走 multipart 存磁盘,report 里只放 URL。
_UPLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_EXEC_SHOT_ROOT = os.path.join(_UPLOADS_DIR, "execs")
_MAX_SHOT_BYTES = 10 * 1024 * 1024  # 10MB
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# 执行截图保留天数(惰性清理):超期的旧批目录在有新执行上传时删。≤0 不清理。
_SHOT_RETENTION_DAYS = int(os.getenv("EXEC_SHOT_RETENTION_DAYS", "14"))


def _cleanup_old_exec_shots() -> None:
    """删超过保留期的执行截图子目录(惰性:上传新图时触发)。只清磁盘,不动 DB 的 report URL。

    整个函数吞异常——清理尽力而为,绝不影响上传主流程。过期后前端显示裂图但记录不丢。
    """
    if _SHOT_RETENTION_DAYS <= 0:
        return
    try:
        cutoff = time.time() - _SHOT_RETENTION_DAYS * 86400
        for name in os.listdir(_EXEC_SHOT_ROOT):
            sub = os.path.join(_EXEC_SHOT_ROOT, name)
            try:
                if os.path.isdir(sub) and os.path.getmtime(sub) < cutoff:
                    for f in os.listdir(sub):
                        try:
                            os.remove(os.path.join(sub, f))
                        except OSError:
                            continue
                    os.rmdir(sub)
            except OSError:
                continue
    except OSError:
        pass   # 根目录不存在等:本就无可清理


@router.post("/{run_id}/screenshot")
async def upload_exec_screenshot(
    run_id: int,
    file: UploadFile = File(...),
    idx: int = Query(0),                 # 第几步截图,决定文件名(同一 run 多张)
    runner: str = Query("mac-01"),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """runner 上传某执行步的截图(PNG)。存 uploads/execs/<run_id>/<idx>.png,返回可访问 URL。

    runner token 鉴权 + 归属校验(只能给派给自己的执行项传图);仅 PNG;≤10MB。
    URL 由 runner 收集后写进回写 report 的对应步骤,DB 不额外记(report JSON 里带)。
    """
    if ctx.device is not None:
        runner = ctx.device.runner_id   # 设备 token:以设备身份为准,防冒充
    r = db.get(ExecRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    if r.runner != runner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="该执行项未派给此执行机")
    data = await file.read()
    if len(data) > _MAX_SHOT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"截图过大（>{_MAX_SHOT_BYTES // 1024 // 1024}MB）")
    if not data.startswith(_PNG_MAGIC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅支持 PNG 截图")
    sub = os.path.join(_EXEC_SHOT_ROOT, str(run_id))
    os.makedirs(sub, exist_ok=True)
    safe_idx = max(0, int(idx))
    rel = f"execs/{run_id}/{safe_idx}.png"
    with open(os.path.join(_UPLOADS_DIR, rel), "wb") as f:
        f.write(data)
    _cleanup_old_exec_shots()   # 顺手清过期旧批(惰性)
    return ok({"screenshot_url": f"/uploads/{rel}"})
