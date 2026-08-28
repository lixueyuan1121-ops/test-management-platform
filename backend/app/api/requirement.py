"""需求覆盖 API（Requirement）——需求↔用例↔发版的追溯链（建议项⑥）。

对标 Xray requirement↔test「covers」+ 覆盖状态(UNCOVERED/NOTRUN/NOK/OK)，
口径映射本仓库四态：
  uncovered=没挂用例 / notrun=挂了但没执行过 / failing=最新有效执行有失败或阻塞 /
  partial=部分用例通过其余未跑 / passed=全部用例最新有效执行通过。
「最新有效执行」经重试链聚合(exec_queue.effective_runs 同口径,被重试覆盖的行不计)。

沿用全项目约定：{code,msg,data} 信封、手写 _to_out、体外 assert_project_role。
用例挂链走 test_case.requirement_id 软链(AI 生成时自动 upsert+挂,亦可手动挂/摘)。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ExecRun, ReleaseRecord, Requirement, TestCase, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


class RequirementIn(BaseModel):
    project_id: int
    title: str = Field(..., max_length=512)
    url: str | None = Field(None, max_length=512)
    release_id: int | None = None


class RequirementPatch(BaseModel):
    title: str | None = Field(None, max_length=512)
    url: str | None = Field(None, max_length=512)
    release_id: int | None = None       # 传 0 表示摘除版本关联(pydantic 无法区分 None/缺省)


class ReqCasesIn(BaseModel):
    case_ids: list[int] = Field(..., min_length=1)


def upsert_requirement(db: Session, project_id: int, url: str | None,
                       title: str | None, created_by: int | None) -> int | None:
    """按 (project_id, url) 幂等 upsert 需求实体,返回 requirement_id。

    生成侧自动挂链入口:无 url(纯文本粘贴需求)不建实体返回 None——没有稳定主键的
    需求没法去重,宁缺勿滥。已存在则回填更有意义的 title(此前是裸 url 时)。
    """
    url = (url or "").strip()
    if not url:
        return None
    row = (db.query(Requirement)
           .filter(Requirement.project_id == project_id, Requirement.url == url)
           .first())
    title = (title or "").strip()[:512] or url[:512]
    if row:
        if title and (not row.title or row.title == row.url):
            row.title = title
            db.commit()
        return row.id
    row = Requirement(project_id=project_id, title=title, url=url, created_by=created_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _coverage_of(db: Session, req_ids: list[int]) -> dict[int, dict]:
    """批量算每个需求的覆盖状态(现算):用例数/已执行数/通过数/state 四态。"""
    from app.api.exec_queue import effective_runs

    out = {rid: {"case_count": 0, "executed": 0, "passed": 0, "state": "uncovered"}
           for rid in req_ids}
    if not req_ids:
        return out
    cases = (db.query(TestCase.id, TestCase.requirement_id)
             .filter(TestCase.requirement_id.in_(req_ids)).all())
    by_req: dict[int, list[int]] = {}
    for cid, rid in cases:
        by_req.setdefault(rid, []).append(cid)
    all_case_ids = [cid for cid, _ in cases]
    latest: dict[int, ExecRun] = {}
    if all_case_ids:
        runs = effective_runs(
            db.query(ExecRun).filter(ExecRun.test_case_id.in_(all_case_ids))
            .order_by(ExecRun.id).all()
        )
        for r in runs:   # id 升序,后写覆盖 → 留每用例最新一条有效执行
            if r.test_case_id:
                latest[r.test_case_id] = r
    for rid, cids in by_req.items():
        st = out[rid]
        st["case_count"] = len(cids)
        bad = 0
        for cid in cids:
            r = latest.get(cid)
            if r is None:
                continue
            st["executed"] += 1
            sval = r.status.value if hasattr(r.status, "value") else r.status
            if sval == "passed":
                st["passed"] += 1
            elif sval in ("failed", "blocked"):
                bad += 1
        if st["case_count"] == 0:
            st["state"] = "uncovered"
        elif st["executed"] == 0:
            st["state"] = "notrun"
        elif bad > 0:
            st["state"] = "failing"
        elif st["passed"] == st["case_count"]:
            st["state"] = "passed"
        else:
            st["state"] = "partial"
    return out


def _to_out(r: Requirement, cov: dict | None = None, release_ver: str | None = None) -> dict:
    d = {
        "id": r.id, "project_id": r.project_id, "title": r.title, "url": r.url,
        "release_id": r.release_id, "release_version": release_ver,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if cov is not None:
        d.update(cov)
    return d


@router.get("")
def list_requirements(
    project_id: int = Query(...),
    release_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """需求列表 + 每条的覆盖状态(uncovered/notrun/failing/partial/passed)。"""
    assert_project_role(db, user, project_id, _READ_ROLES)
    q = db.query(Requirement).filter(Requirement.project_id == project_id)
    if release_id is not None:
        q = q.filter(Requirement.release_id == release_id)
    rows = q.order_by(Requirement.id.desc()).limit(300).all()
    cov = _coverage_of(db, [r.id for r in rows])
    rel_ids = {r.release_id for r in rows if r.release_id}
    vers = {}
    if rel_ids:
        for rel in db.query(ReleaseRecord).filter(ReleaseRecord.id.in_(rel_ids)).all():
            vers[rel.id] = rel.version
    return ok([_to_out(r, cov.get(r.id), vers.get(r.release_id)) for r in rows])


@router.post("")
def create_requirement(
    body: RequirementIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if body.release_id is not None:
        rel = db.get(ReleaseRecord, body.release_id)
        if not rel or rel.project_id != body.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="发版记录不存在或不属于该项目")
    # url 提供时按 (project,url) 去重:命中即改为更新标题/版本(避免同一文档双实体)
    if body.url:
        exist = (db.query(Requirement)
                 .filter(Requirement.project_id == body.project_id,
                         Requirement.url == body.url.strip()).first())
        if exist:
            exist.title = body.title
            if body.release_id is not None:
                exist.release_id = body.release_id
            db.commit()
            db.refresh(exist)
            return ok(_to_out(exist))
    r = Requirement(project_id=body.project_id, title=body.title,
                    url=(body.url or "").strip() or None,
                    release_id=body.release_id, created_by=user.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


@router.patch("/{rid}")
def update_requirement(
    rid: int,
    body: RequirementPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(Requirement, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="需求不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if body.title is not None:
        r.title = body.title
    if body.url is not None:
        r.url = body.url.strip() or None
    if body.release_id is not None:
        if body.release_id == 0:
            r.release_id = None   # 0 = 摘除版本关联
        else:
            rel = db.get(ReleaseRecord, body.release_id)
            if not rel or rel.project_id != r.project_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="发版记录不存在或不属于该项目")
            r.release_id = body.release_id
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


@router.delete("/{rid}")
def delete_requirement(
    rid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删需求:用例的 requirement_id 置空(SQLite 无 FK 级联时手动兜底),用例本身不动。"""
    r = db.get(Requirement, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="需求不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    (db.query(TestCase).filter(TestCase.requirement_id == rid)
     .update({"requirement_id": None}, synchronize_session=False))
    db.delete(r)
    db.commit()
    return ok({"deleted": rid})


@router.get("/{rid}/cases")
def list_req_cases(
    rid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """需求下挂的用例(带最新有效执行结论)。"""
    from app.api.exec_queue import effective_runs

    r = db.get(Requirement, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="需求不存在")
    assert_project_role(db, user, r.project_id, _READ_ROLES)
    cases = (db.query(TestCase).filter(TestCase.requirement_id == rid)
             .order_by(TestCase.id.desc()).all())
    latest: dict[int, str] = {}
    ids = [c.id for c in cases]
    if ids:
        for run in effective_runs(
            db.query(ExecRun).filter(ExecRun.test_case_id.in_(ids)).order_by(ExecRun.id).all()
        ):
            if run.test_case_id:
                latest[run.test_case_id] = (
                    run.status.value if hasattr(run.status, "value") else run.status
                )
    return ok([
        {
            "id": c.id, "title": c.title, "category": c.category, "priority": c.priority,
            "exec_kind": c.exec_kind, "review_status": getattr(c.review_status, "value", c.review_status),
            "last_exec": latest.get(c.id),
        }
        for c in cases
    ])


@router.post("/{rid}/cases")
def link_cases(
    rid: int,
    body: ReqCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动把用例挂到需求(同项目校验;已挂其它需求的会改挂到本需求)。"""
    r = db.get(Requirement, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="需求不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    cases = db.query(TestCase).filter(TestCase.id.in_(body.case_ids)).all()
    found = {c.id: c for c in cases}
    for cid in body.case_ids:
        c = found.get(cid)
        if c is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不存在")
        if c.project_id != r.project_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"用例 {cid} 不属于该项目")
    linked = 0
    for c in cases:
        if c.requirement_id != rid:
            c.requirement_id = rid
            linked += 1
    db.commit()
    return ok({"linked": linked})


@router.delete("/{rid}/cases")
def unlink_cases(
    rid: int,
    body: ReqCasesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.get(Requirement, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="需求不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    n = (db.query(TestCase)
         .filter(TestCase.requirement_id == rid, TestCase.id.in_(body.case_ids))
         .update({"requirement_id": None}, synchronize_session=False))
    db.commit()
    return ok({"unlinked": n})
