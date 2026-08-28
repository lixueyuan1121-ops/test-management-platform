"""反馈测试模块路由。

机器人推 md/zip（X-Bot-Token 鉴权）→ 结构化解析 → feedback_import + feedback_case。
用例/集/结果/三触发端点。沿用全项目约定：{code,msg,data} 信封（ok/fail）、
手写 _to_out、体外 assert_project_role。反馈用例归属固定专用项目（settings.FEEDBACK_PROJECT_CODE）。

三个执行触发源共用下发内核 _dispatch_cases（Task 6 调度器也复用）：
  ① 定时自动回归（调度器）  ② 手动整集回归 POST /sets/{id}/run  ③ 勾选用例执行 POST /cases/run
均把 feedback_case 快照成 exec_run（test_case_id=None，payload 带 feedback_case_id 软关联），
runner 拉取/回写完全复用 exec-queue 端点；结果按 batch_id 聚合 exec_run 现算。
"""
import json
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_project_role, get_current_user
from app.core.enums import (
    ExecKind, ExecStatus, FeedbackCaseStatus, FeedbackImportStatus, ProjectRole,
)
from app.core.security import decode_token
from app.db.session import get_db
from app.models import (
    ExecRun, FeedbackCase, FeedbackImport, FeedbackRegressionSet, FeedbackRun,
    FeedbackSetCase, Project, User,
)
from app.schemas.common import ok
from app.schemas.feedback import (
    CaseUpdateIn, RunCasesIn, ScheduleIn, SetCasesIn, SetCreateIn, SetUpdateIn,
)
from app.services.feedback_parser import iter_zip, parse_md
from app.services.feedback_script import fill_scripts_for_import

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


def require_ingest_auth(
    x_bot_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """机器人对接鉴权：X-Bot-Token 或 平台账号 JWT，任一通过即可。

    - 现有机器人调 tasks 用的是平台账号 JWT，直接带同一个 `Authorization: Bearer <token>`
      即可调本接口，零改动。
    - 也兼容独立 X-Bot-Token（FEEDBACK_BOT_TOKEN），供无账号的机器人使用。
    两者都没有/都不对 → 401。
    """
    # ① X-Bot-Token（配了才校验）
    if settings.FEEDBACK_BOT_TOKEN and x_bot_token == settings.FEEDBACK_BOT_TOKEN:
        return
    # ② 平台账号 JWT（access token）——与机器人调 tasks 同一套鉴权
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            uid = payload.get("sub")
            user = db.get(User, int(uid)) if uid else None
            if user and user.status.value == "active":
                return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                        detail="需要有效的 X-Bot-Token 或平台账号令牌")


def get_feedback_project(db: Session) -> Project:
    """反馈用例归属的固定专用项目（startup 已 ensure；防御性再查）。"""
    proj = db.query(Project).filter_by(code=settings.FEEDBACK_PROJECT_CODE).first()
    if not proj:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="反馈测试专用项目未初始化")
    return proj


def selector_project_id(db: Session, fallback_project_id: int) -> int:
    """选择器来源项目 id：配了 FEEDBACK_SELECTOR_PROJECT_CODE 就用被测产品项目，否则回退反馈项目自身。

    反馈项目自己不建选择器；补 script / 下发时借用被测产品（功能测试）项目的选择器库。
    """
    code = settings.FEEDBACK_SELECTOR_PROJECT_CODE
    if code:
        p = db.query(Project).filter_by(code=code).first()
        if p:
            return p.id
    return fallback_project_id


def _clip(s: str | None, n: int) -> str | None:
    return s[:n] if s else s


def _safe_url(u: str | None) -> str | None:
    """只保留 http/https 的 URL 落库；其余（javascript:/data: 等）丢弃，防前端渲染成可点链接时 XSS。"""
    return u if (u and re.match(r"^https?://", u, re.I)) else None


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    note: str | None = Form(default=None),
    source_bot: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(require_ingest_auth),
):
    """机器人推 md/zip：解析落 feedback_import + feedback_case。立即返回，不等补 script。"""
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件过大（>20MB）")
    proj = get_feedback_project(db)
    fname = file.filename or "upload"

    # 拆成 [(名, md 文本)]
    if fname.lower().endswith(".zip"):
        try:
            docs = iter_zip(data)
        except Exception as e:  # 非法 zip
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"zip 解析失败：{e}")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", errors="replace")
        docs = [(fname, text)]

    if not docs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未从上传内容找到 .md 文件")

    imp = FeedbackImport(
        project_id=proj.id, source_bot=source_bot, filename=fname,
        file_count=len(docs), status=FeedbackImportStatus.parsing, note=note,
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)

    case_count = 0
    script_total = 0
    errors: list[str] = []
    for name, text in docs:
        try:
            parsed = parse_md(text)
        except Exception as e:  # 单文件解析异常不中断整批
            errors.append(f"{name}: 解析异常 {e}")
            continue
        for c in parsed["cases"]:
            fc = FeedbackCase(
                import_id=imp.id, project_id=proj.id,
                req_title=_clip(parsed["req_title"], 512),
                req_url=_clip(_safe_url(parsed["req_url"]), 1024),
                feedback_summary=parsed["feedback_summary"],
                point_code=_clip(c["point_code"], 32),
                point_title=_clip(c["point_title"], 255),
                case_no=_clip(c["case_no"], 16),
                title=_clip(c["title"], 512) or "(无标题)",
                precondition=c["precondition"], steps=c["steps"], expected=c["expected"],
                category=_clip(c["category"], 16), priority=_clip(c["priority"], 8),
                auto_feasible=c["auto_feasible"], auto_reason=c["auto_reason"],
                exec_kind=c["exec_kind"], status=FeedbackCaseStatus.draft,
            )
            db.add(fc)
            case_count += 1
            if c["auto_feasible"] in ("yes", "partial"):
                script_total += 1

    imp.case_count = case_count
    imp.script_total = script_total
    imp.status = FeedbackImportStatus.done
    if errors:
        imp.error = "\n".join(errors)[:4000]
    db.commit()

    # 后台线程自动对可自动化用例补 script（不阻塞 ingest 返回；引擎不可用则线程内优雅跳过）
    if script_total > 0:
        threading.Thread(target=fill_scripts_for_import, args=(imp.id,), daemon=True).start()

    return ok({
        "import_id": imp.id,
        "file_count": len(docs),
        "case_count": case_count,
        "script_total": script_total,
    })


# ==================== 序列化 ====================

def _import_out(db: Session, imp: FeedbackImport) -> dict:
    # 进度实时算：script_done = 已成功补 script 的可自动化用例数（幂等，不受中断/重跑影响）
    done = (db.query(func.count(FeedbackCase.id))
            .filter(FeedbackCase.import_id == imp.id,
                    FeedbackCase.auto_feasible.in_(["yes", "partial"]),
                    FeedbackCase.script.isnot(None)).scalar() or 0)
    return {
        "id": imp.id, "source_bot": imp.source_bot, "filename": imp.filename,
        "file_count": imp.file_count, "case_count": imp.case_count,
        "status": imp.status.value if hasattr(imp.status, "value") else imp.status,
        "script_done": done, "script_total": imp.script_total,
        "note": imp.note, "error": imp.error,
        "created_at": imp.created_at.isoformat() if imp.created_at else None,
    }


def _case_out(c: FeedbackCase) -> dict:
    return {
        "id": c.id, "import_id": c.import_id,
        "req_title": c.req_title, "req_url": c.req_url, "feedback_summary": c.feedback_summary,
        "point_code": c.point_code, "point_title": c.point_title, "case_no": c.case_no,
        "title": c.title, "precondition": c.precondition, "steps": c.steps, "expected": c.expected,
        "category": c.category, "priority": c.priority,
        "auto_feasible": c.auto_feasible, "auto_reason": c.auto_reason,
        "exec_kind": c.exec_kind, "has_script": bool(c.script), "script_error": c.script_error,
        "page": c.page,
        "status": c.status.value if hasattr(c.status, "value") else c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _set_out(db: Session, s: FeedbackRegressionSet) -> dict:
    case_n = db.query(func.count(FeedbackSetCase.id)).filter(FeedbackSetCase.set_id == s.id).scalar() or 0
    return {
        "id": s.id, "name": s.name, "description": s.description,
        "schedule_cron": s.schedule_cron, "schedule_enabled": bool(s.schedule_enabled),
        "runner": s.runner, "case_count": case_n,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "created_by": s.created_by,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ==================== 下发内核（三触发源共用） ====================

def _new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _fb_payload(c: FeedbackCase, selector_pid: int) -> dict:
    """反馈用例 → runner payload。带 feedback_case_id 软关联（exec_run.test_case_id 保持 None）。

    payload.project_id 用**选择器来源项目**（被测产品），runner 据此拉选择器注册表——
    反馈项目自己不建选择器，借用被测产品的库。
    """
    script = None
    if c.script:
        try:
            parsed = json.loads(c.script)
            if isinstance(parsed, list) and parsed:
                script = parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            script = None
    return {
        "feedback_case_id": c.id,
        "title": c.title,
        "category": c.category,
        "steps": c.steps,
        "expected": c.expected,
        "priority": c.priority,
        "script": script,
        "project_id": selector_pid,   # runner 按此拉选择器注册表（被测产品项目，非反馈项目）
    }


def _dispatch_cases(
    db: Session, project_id: int, case_ids: list[int], runner: str,
    trigger: str, set_id: int | None = None, started_by: int | None = None,
) -> dict:
    """把反馈用例快照成 exec_run 下发 + 建 feedback_run 汇总。三触发源共用（含调度器）。

    整体校验：任一用例不存在/跨项目/为 manual → 400 整批拒绝。
    返回 {batch_id, run_ids, feedback_run_id}。
    """
    ids = list(dict.fromkeys(case_ids))   # 去重保序
    if not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="没有可下发的用例")
    cases = db.query(FeedbackCase).filter(FeedbackCase.id.in_(ids)).all()
    found = {c.id: c for c in cases}
    for cid in ids:
        c = found.get(cid)
        if c is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不存在")
        if c.project_id != project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不属于反馈项目")
        if c.exec_kind == "manual":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"用例 {cid}「{c.title}」为人工/不可自动化(manual)，不能下发到执行机",
            )

    batch_id = _new_batch_id()
    sel_pid = selector_project_id(db, project_id)   # 选择器来源项目（被测产品），runner 据此拉选择器
    run_ids = []
    for cid in ids:
        c = found[cid]
        try:
            kind = ExecKind(c.exec_kind)
        except ValueError:
            kind = ExecKind.gui
        row = ExecRun(
            checklist_item_id=None,   # 反馈执行不挂验收清单 → 回写不回流清单
            test_case_id=None,        # 非 test_case 体系；软关联在 payload.feedback_case_id
            task_id=None,
            project_id=project_id,
            batch_id=batch_id,
            runner=runner,
            kind=kind,
            status=ExecStatus.pending,
            payload=json.dumps(_fb_payload(c, sel_pid), ensure_ascii=False),
            enqueued_by=started_by,
        )
        db.add(row)
        db.flush()
        run_ids.append(row.id)

    fr = FeedbackRun(
        project_id=project_id, set_id=set_id, batch_id=batch_id,
        trigger=trigger, case_count=len(ids), started_by=started_by,
    )
    db.add(fr)
    db.commit()
    db.refresh(fr)
    return {"batch_id": batch_id, "run_ids": run_ids, "feedback_run_id": fr.id}


# ==================== 导入记录 ====================

@router.get("/imports")
def list_imports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _READ_ROLES)
    rows = (db.query(FeedbackImport)
            .filter(FeedbackImport.project_id == proj.id)
            .order_by(FeedbackImport.id.desc()).limit(100).all())
    return ok([_import_out(db, r) for r in rows])


@router.post("/imports/{iid}/refill")
def refill_scripts(
    iid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重新触发该批次的补 script（续补：只补还没 script 的用例）。用于中断后重启。"""
    imp = db.get(FeedbackImport, iid)
    if not imp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="导入记录不存在")
    assert_project_role(db, user, imp.project_id, _WRITE_ROLES)
    pending = (db.query(func.count(FeedbackCase.id))
               .filter(FeedbackCase.import_id == iid,
                       FeedbackCase.auto_feasible.in_(["yes", "partial"]),
                       FeedbackCase.script.is_(None)).scalar() or 0)
    if pending == 0:
        return ok({"started": False, "pending": 0, "msg": "该批次可自动化用例已全部补齐"})
    threading.Thread(target=fill_scripts_for_import, args=(iid,), daemon=True).start()
    return ok({"started": True, "pending": pending})


# ==================== 反馈用例 ====================

@router.get("/cases")
def list_cases(
    import_id: int | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    auto_feasible: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """反馈用例列表（默认全量；可按 import/status/auto_feasible 过滤）。"""
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _READ_ROLES)
    q = db.query(FeedbackCase).filter(FeedbackCase.project_id == proj.id)
    if import_id is not None:
        q = q.filter(FeedbackCase.import_id == import_id)
    if status_:
        q = q.filter(FeedbackCase.status == status_)
    if auto_feasible:
        q = q.filter(FeedbackCase.auto_feasible == auto_feasible)
    rows = q.order_by(FeedbackCase.id).all()
    return ok([_case_out(c) for c in rows])


@router.get("/cases/{cid}")
def get_case(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.get(FeedbackCase, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用例不存在")
    assert_project_role(db, user, c.project_id, _READ_ROLES)
    d = _case_out(c)
    try:
        d["script"] = json.loads(c.script) if c.script else None
    except (json.JSONDecodeError, ValueError):
        d["script"] = None
    return ok(d)


@router.patch("/cases/{cid}")
def update_case(
    cid: int,
    body: CaseUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.get(FeedbackCase, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用例不存在")
    assert_project_role(db, user, c.project_id, _WRITE_ROLES)
    for f in ("title", "precondition", "steps", "expected", "category", "priority", "exec_kind"):
        v = getattr(body, f, None)
        if v is not None:
            setattr(c, f, v)
    db.commit()
    db.refresh(c)
    return ok(_case_out(c))


@router.post("/cases/{cid}/gen-script")
def regen_case_script(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对单条用例重新补 script（同步调引擎）。复用 feedback_script 的单条逻辑。"""
    from app.services import generators
    from app.services.claude_runner import pages_for_script

    c = db.get(FeedbackCase, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用例不存在")
    assert_project_role(db, user, c.project_id, _WRITE_ROLES)
    if c.exec_kind == "manual":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="manual 用例不生成 script")
    engine = generators.get_provider(generators.DEFAULT_PROVIDER)
    if not engine.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="生成引擎不可用")
    # 取字段快照后关闭 session，避免 AI 阻塞期间持连接（同 ai.py 教训）
    # pid 用选择器来源项目（被测产品），反馈项目自己无选择器
    title, steps, expected = c.title, c.steps or "", c.expected or ""
    pid = selector_project_id(db, c.project_id)
    kind = c.exec_kind if c.exec_kind in ("gui", "e2e", "api") else "gui"
    db.close()

    script, err = engine.generate_script(kind, title, steps, expected, project_id=pid)

    from app.db.session import SessionLocal
    s = SessionLocal()
    try:
        c2 = s.get(FeedbackCase, cid)
        if not c2:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用例已删除")
        if err or not script:
            c2.script_error = (err or "空 script")[:2000]
            s.commit()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"生成 script 失败：{err}")
        c2.script = json.dumps(script, ensure_ascii=False)
        c2.script_error = None
        p = pages_for_script(script, pid)
        if p:
            c2.page = p
        c2.status = FeedbackCaseStatus.ready
        s.commit()
        s.refresh(c2)
        return ok(_case_out(c2))
    finally:
        s.close()


@router.delete("/cases/{cid}")
def delete_case(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.get(FeedbackCase, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="用例不存在")
    assert_project_role(db, user, c.project_id, _WRITE_ROLES)
    db.delete(c)
    db.commit()
    return ok({"deleted": cid})


# ==================== ③ 勾选用例直接执行（ad-hoc） ====================

@router.post("/cases/run")
def run_cases(
    body: RunCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """勾选若干反馈用例，当场下发执行（不入集，set_id=None，trigger=manual）。"""
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _WRITE_ROLES)
    res = _dispatch_cases(db, proj.id, body.case_ids, body.runner,
                          trigger="manual", set_id=None, started_by=user.id)
    return ok(res)


# ==================== 回归用例集 ====================

@router.get("/sets")
def list_sets(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _READ_ROLES)
    rows = (db.query(FeedbackRegressionSet)
            .filter(FeedbackRegressionSet.project_id == proj.id)
            .order_by(FeedbackRegressionSet.id.desc()).all())
    return ok([_set_out(db, s) for s in rows])


@router.post("/sets")
def create_set(
    body: SetCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _WRITE_ROLES)
    s = FeedbackRegressionSet(
        project_id=proj.id, name=body.name, description=body.description,
        runner=body.runner, created_by=user.id,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


@router.patch("/sets/{sid}")
def update_set(
    sid: int,
    body: SetUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    for f in ("name", "description", "runner"):
        v = getattr(body, f, None)
        if v is not None:
            setattr(s, f, v)
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


@router.delete("/sets/{sid}")
def delete_set(
    sid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    # 删调度 job（若已挂）
    try:
        from app.services.scheduler import sync_set_job
        sync_set_job(sid, None, False)
    except Exception:
        pass
    db.delete(s)
    db.commit()
    return ok({"deleted": sid})


@router.get("/sets/{sid}/cases")
def list_set_cases(
    sid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _READ_ROLES)
    rows = (db.query(FeedbackCase)
            .join(FeedbackSetCase, FeedbackSetCase.case_id == FeedbackCase.id)
            .filter(FeedbackSetCase.set_id == sid)
            .order_by(FeedbackCase.id).all())
    return ok([_case_out(c) for c in rows])


@router.post("/sets/{sid}/cases")
def add_set_cases(
    sid: int,
    body: SetCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把用例加入集（幂等：已在集内的跳过）。"""
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    existing = {r.case_id for r in db.query(FeedbackSetCase.case_id).filter(FeedbackSetCase.set_id == sid).all()}
    added = 0
    for cid in dict.fromkeys(body.case_ids):
        c = db.get(FeedbackCase, cid)
        if not c or c.project_id != s.project_id or cid in existing:
            continue
        db.add(FeedbackSetCase(set_id=sid, case_id=cid))
        added += 1
    db.commit()
    return ok({"added": added})


@router.delete("/sets/{sid}/cases")
def remove_set_cases(
    sid: int,
    body: SetCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.case_ids))
    n = (db.query(FeedbackSetCase)
         .filter(FeedbackSetCase.set_id == sid, FeedbackSetCase.case_id.in_(ids))
         .delete(synchronize_session=False))
    db.commit()
    return ok({"removed": n})


# ==================== ② 手动整集回归 ====================

def _auto_case_ids_of_set(db: Session, set_id: int) -> list[int]:
    """集内**可自动化**用例 id（跳过 manual）。整集回归/定时共用——不因个别 manual 整批失败。"""
    rows = (db.query(FeedbackCase.id)
            .join(FeedbackSetCase, FeedbackSetCase.case_id == FeedbackCase.id)
            .filter(FeedbackSetCase.set_id == set_id, FeedbackCase.exec_kind != "manual")
            .all())
    return [r.id for r in rows]


@router.post("/sets/{sid}/run")
def run_set(
    sid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """立即回归整集（下发集内所有可自动化用例，自动跳过 manual，trigger=manual）。"""
    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    case_ids = _auto_case_ids_of_set(db, sid)
    if not case_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="集内无可自动化用例（manual 用例不下发）")
    res = _dispatch_cases(db, s.project_id, case_ids, s.runner,
                          trigger="manual", set_id=sid, started_by=user.id)
    return ok(res)


@router.patch("/sets/{sid}/schedule")
def set_schedule(
    sid: int,
    body: ScheduleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """设置集的定时回归。cron 存库 + 联动调度器 add/remove job + 回填 next_run_at。"""
    from app.services.scheduler import sync_set_job

    s = db.get(FeedbackRegressionSet, sid)
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="集不存在")
    assert_project_role(db, user, s.project_id, _WRITE_ROLES)
    # 校验 cron（enabled 时必须给合法 5 段 cron）
    if body.enabled:
        if not body.cron:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="启用定时需提供 cron 表达式")
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(body.cron)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"cron 表达式非法：{body.cron}")
    s.schedule_cron = body.cron
    s.schedule_enabled = body.enabled
    next_run = sync_set_job(sid, body.cron, body.enabled)
    # next_run_time 带时区，去 tz 存 naive DATETIME（与库内其它时间一致）
    s.next_run_at = next_run.replace(tzinfo=None) if next_run else None
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


# ==================== 回归结果 ====================

def _aggregate_batch(db: Session, project_id: int, batch_id: str) -> dict:
    """按 batch_id 聚合 exec_run 现算 total/passed/failed/blocked/pending/是否完成。

    重试链聚合:被自动重试覆盖的原始行不计,以链上最终结果为准(与门禁/告警同口径,
    见 exec_queue.effective_runs);flaky=重试后通过的条数。
    """
    from app.api.exec_queue import effective_runs
    rows = effective_runs(
        db.query(ExecRun)
        .filter(ExecRun.project_id == project_id, ExecRun.batch_id == batch_id).all()
    )
    counts: dict = {}
    flaky = 0
    for r in rows:
        key = r.status.value if hasattr(r.status, "value") else r.status
        counts[key] = counts.get(key, 0) + 1
        if getattr(r, "flaky", False):
            flaky += 1
    total = sum(counts.values())
    done = total - counts.get("pending", 0) - counts.get("running", 0)
    return {
        "total": total,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "blocked": counts.get("blocked", 0),
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "flaky": flaky,
        "finished": total > 0 and done == total,
    }


@router.get("/runs")
def list_runs(
    set_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回归/执行批次列表，每条按 batch_id 聚合 exec_run 现算结果。"""
    proj = get_feedback_project(db)
    assert_project_role(db, user, proj.id, _READ_ROLES)
    q = db.query(FeedbackRun).filter(FeedbackRun.project_id == proj.id)
    if set_id is not None:
        q = q.filter(FeedbackRun.set_id == set_id)
    rows = q.order_by(FeedbackRun.id.desc()).limit(100).all()
    # 集名映射
    set_ids = {r.set_id for r in rows if r.set_id}
    names = {}
    if set_ids:
        for s in db.query(FeedbackRegressionSet).filter(FeedbackRegressionSet.id.in_(set_ids)).all():
            names[s.id] = s.name
    out = []
    for r in rows:
        d = {
            "id": r.id, "set_id": r.set_id, "set_name": names.get(r.set_id),
            "batch_id": r.batch_id, "trigger": r.trigger, "case_count": r.case_count,
            "started_by": r.started_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "stats": _aggregate_batch(db, proj.id, r.batch_id),
        }
        out.append(d)
    return ok(out)


@router.get("/runs/{rid}")
def get_run(
    rid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单次批次详情：逐条 exec_run（含 payload/verdict/report）。"""
    fr = db.get(FeedbackRun, rid)
    if not fr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="回归记录不存在")
    assert_project_role(db, user, fr.project_id, _READ_ROLES)
    runs = (db.query(ExecRun)
            .filter(ExecRun.project_id == fr.project_id, ExecRun.batch_id == fr.batch_id)
            .order_by(ExecRun.id).all())
    items = []
    for r in runs:
        try:
            payload = json.loads(r.payload or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        try:
            report = json.loads(r.report) if r.report else None
        except (json.JSONDecodeError, ValueError):
            report = None
        items.append({
            "run_id": r.id,
            "feedback_case_id": payload.get("feedback_case_id"),
            "title": payload.get("title"),
            "kind": getattr(r.kind, "value", r.kind),
            "status": getattr(r.status, "value", r.status),
            "verdict": r.verdict, "fail_kind": r.fail_kind, "reason": r.reason,
            "evidence_url": r.evidence_url, "report": report,
            "duration_ms": r.duration_ms,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return ok({
        "id": fr.id, "set_id": fr.set_id, "batch_id": fr.batch_id,
        "trigger": fr.trigger, "case_count": fr.case_count,
        "created_at": fr.created_at.isoformat() if fr.created_at else None,
        "stats": _aggregate_batch(db, fr.project_id, fr.batch_id),
        "items": items,
    })


@router.get("/defense-calendar")
def defense_calendar(
    weeks: int = 12,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回归防线日历:GitHub 贡献墙式，按天展示反馈回归跑批状态(绿/红/灰)及连续值守天数。

    green=当天有跑批且 failed+blocked==0；red=有跑批但有失败；gray=当天没跑批。
    streak:从今天往回数连续非 gray 天数（今天若 gray 从昨天起算）。
    全用户可见;无 project_id 过滤(反馈是平台级模块,专属项目)。
    """
    from datetime import date, timedelta
    from sqlalchemy import func
    from app.models.feedback import FeedbackRun

    if weeks <= 0 or weeks > 26:
        weeks = 12
    today = date.today()
    d_from = today - timedelta(days=weeks * 7 - 1)

    # 窗口内每天的 FeedbackRun → batch_id 集合
    fb_rows = (
        db.query(func.date(FeedbackRun.created_at), FeedbackRun.batch_id,
                 func.count(FeedbackRun.id))
        .filter(func.date(FeedbackRun.created_at) >= d_from,
                func.date(FeedbackRun.created_at) <= today)
        .group_by(func.date(FeedbackRun.created_at), FeedbackRun.batch_id)
        .all()
    )
    by_day: dict[str, dict] = {}  # date_str → {runs, batches, cases}
    for dt_str, batch_id, cnt in fb_rows:
        rec = by_day.setdefault(str(dt_str), {"runs": 0, "batches": set(), "cases": 0})
        rec["runs"] += cnt
        rec["batches"].add(batch_id)
        rec["cases"] += cnt  # 近似：FeedbackRun.case_count 未在这里聚合

    # 这些 batch 下 exec_run 的 failed/blocked 数
    all_batches: list[str] = []
    for v in by_day.values():
        all_batches.extend(v["batches"])

    fail_by_batch: dict[str, int] = {}
    if all_batches:
        for batch, cnt in (
            db.query(ExecRun.batch_id, func.count(ExecRun.id))
            .filter(ExecRun.batch_id.in_(all_batches),
                    ExecRun.status.in_(["failed", "blocked"]))
            .group_by(ExecRun.batch_id).all()
        ):
            fail_by_batch[batch] = cnt

    # 构建每天列表
    days = []
    total_guard = 0
    for i in range(weeks * 7):
        dt = d_from + timedelta(days=i)
        ds = str(dt)
        rec = by_day.get(ds)
        if rec is None:
            days.append({"date": ds, "runs": 0, "cases": 0, "failed": 0, "state": "gray"})
        else:
            failed = sum(fail_by_batch.get(b, 0) for b in rec["batches"])
            state = "red" if failed > 0 else "green"
            total_guard += 1
            days.append({"date": ds, "runs": rec["runs"], "cases": rec["cases"],
                         "failed": failed, "state": state})

    # streak:从今天往回连续非 gray（今天若 gray 从昨天起）
    streak = 0
    for i in range(len(days) - 1, -1, -1):
        if days[i]["state"] != "gray":
            streak += 1
        elif i == len(days) - 1:
            continue  # 今天 gray，从昨天起算，继续向前
        else:
            break

    return ok({
        "from": str(d_from), "to": str(today),
        "days": days,
        "streak": streak,
        "total_guard_days": total_guard,
    })
