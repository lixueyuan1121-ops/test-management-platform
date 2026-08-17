"""发版记录 API:列表/看板/详情所有登录用户,增改删仅平台管理员。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, require_platform_admin
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import Project, ProjectMember, ReleaseRecord, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/releases", tags=["releases"])

_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)

# 子产品固定枚举：按项目平台类型分两套。前端 ReleaseNotes.vue 的常量须与此保持一致。
SUB_PRODUCTS_BY_TYPE = {
    "pc": ("纳米Work云端版", "纳米Work桌面版", "360安全龙虾云端版", "360安全龙虾WSL"),
    "app": ("纳米Work Android端", "纳米Work iOS端", "360安全龙虾Android端", "360安全龙虾iOS端"),
}

# 全部子产品合集（两套并集），供 selectors 等仅按"值是否合法"校验的模块复用。
SUB_PRODUCTS = SUB_PRODUCTS_BY_TYPE["pc"] + SUB_PRODUCTS_BY_TYPE["app"]

# 渠道拼接后的最大存储长度，与 release_record.channel 列宽一致。
_CHANNEL_MAXLEN = 255


def _sub_products_for(platform_type: str | None) -> tuple[str, ...]:
    """取某项目类型对应的子产品白名单；未分类(None/其它)按 PC 端处理。"""
    return SUB_PRODUCTS_BY_TYPE["app"] if platform_type == "app" else SUB_PRODUCTS_BY_TYPE["pc"]


def _norm_sub_product(v: str | None, platform_type: str | None) -> str | None:
    """校验并规整子产品：空/空串 → None；非该项目类型白名单值 → 400。"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if v not in _sub_products_for(platform_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="子产品取值非法")
    return v


def _norm_channel(items: list[str] | None, platform_type: str | None) -> str | None:
    """规整发版渠道（多选，支持手填，不做白名单校验）。

    仅 APP 端项目保留渠道；其它类型一律清空（PC 端不展示渠道）。清洗：去首尾空白、
    丢空项、按序去重；半角逗号是存储分隔符，手填值里的半角逗号替换为全角避免破坏。
    逗号拼接后超列宽 → 400。
    """
    if platform_type != "app" or not items:
        return None
    seen: list[str] = []
    for it in items:
        s = (it or "").strip().replace(",", "，")
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return None
    joined = ",".join(seen)
    if len(joined) > _CHANNEL_MAXLEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="发版渠道过长")
    return joined


class ReleaseCreate(BaseModel):
    project_id: int
    version: str = Field(min_length=1, max_length=64)
    sub_product: str | None = Field(default=None, max_length=32)
    channel: list[str] | None = None
    release_date: date
    req_count: int = Field(default=0, ge=0)
    content: str | None = None
    memo: str | None = None

class ReleaseUpdate(BaseModel):
    version: str | None = Field(default=None, max_length=64)
    sub_product: str | None = Field(default=None, max_length=32)
    channel: list[str] | None = None
    release_date: date | None = None
    req_count: int | None = Field(default=None, ge=0)
    content: str | None = None
    memo: str | None = None


def _visible_project_ids(db: Session, user: User) -> list[int]:
    if user.is_platform_admin:
        return [pid for (pid,) in db.query(Project.id).all()]
    return [pid for (pid,) in
            db.query(ProjectMember.project_id).filter(ProjectMember.user_id == user.id).all()]


def _to_out(db: Session, r: ReleaseRecord, name_map: dict | None = None) -> dict:
    if name_map is not None:
        name = name_map.get(r.created_by, "")
    else:
        u = db.get(User, r.created_by) if r.created_by else None
        name = u.name if u else ""
    return {
        "id": r.id, "project_id": r.project_id, "version": r.version,
        "sub_product": r.sub_product,
        "channel": r.channel.split(",") if r.channel else [],
        "release_date": str(r.release_date), "req_count": r.req_count,
        "content": r.content, "memo": r.memo,
        "created_by": r.created_by, "created_by_name": name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("")
def list_releases(
    project_id: int = Query(...),
    sub_product: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_project_role(db, user, project_id, _ALL_ROLES)
    base = db.query(ReleaseRecord).filter(ReleaseRecord.project_id == project_id)
    if sub_product:
        base = base.filter(ReleaseRecord.sub_product == sub_product)
    total = base.with_entities(func.count(ReleaseRecord.id)).scalar() or 0
    rows = (base.order_by(ReleaseRecord.release_date.desc(), ReleaseRecord.id.desc())
            .limit(limit).offset(offset).all())
    uids = {r.created_by for r in rows if r.created_by}
    name_map = dict(db.query(User.id, User.name).filter(User.id.in_(uids)).all()) if uids else {}
    return ok({"items": [_to_out(db, r, name_map=name_map) for r in rows], "total": total})


@router.get("/stats")
def release_stats(
    project_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if project_id is not None:
        assert_project_role(db, user, project_id, _ALL_ROLES)
        pids = [project_id]
    else:
        pids = _visible_project_ids(db, user)
    empty = {"total_releases": 0, "total_reqs": 0, "this_month": 0, "latest_date": None, "trend": []}
    if not pids:
        return ok(empty)
    base = db.query(ReleaseRecord).filter(ReleaseRecord.project_id.in_(pids))
    total_releases = base.with_entities(func.count(ReleaseRecord.id)).scalar() or 0
    total_reqs = base.with_entities(func.coalesce(func.sum(ReleaseRecord.req_count), 0)).scalar() or 0
    latest_date = base.with_entities(func.max(ReleaseRecord.release_date)).scalar()
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    month_set = set(months)
    rows = base.with_entities(ReleaseRecord.release_date, ReleaseRecord.req_count).all()
    rel_by_month = {mm: 0 for mm in months}
    req_by_month = {mm: 0 for mm in months}
    this_month_key = f"{today.year:04d}-{today.month:02d}"
    this_month = 0
    for rd, rc in rows:
        key = f"{rd.year:04d}-{rd.month:02d}"
        if key == this_month_key:
            this_month += 1
        if key in month_set:
            rel_by_month[key] += 1
            req_by_month[key] += (rc or 0)
    trend = [{"month": mm, "releases": rel_by_month[mm], "reqs": req_by_month[mm]} for mm in months]
    return ok({
        "total_releases": int(total_releases), "total_reqs": int(total_reqs),
        "this_month": this_month, "latest_date": str(latest_date) if latest_date else None,
        "trend": trend,
    })


@router.get("/{rid}")
def get_release(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(ReleaseRecord, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="发版记录不存在")
    assert_project_role(db, user, r.project_id, _ALL_ROLES)
    return ok(_to_out(db, r))


@router.post("")
def create_release(body: ReleaseCreate, db: Session = Depends(get_db), user: User = Depends(require_platform_admin)):
    proj = db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    r = ReleaseRecord(
        project_id=body.project_id, version=body.version.strip(), release_date=body.release_date,
        sub_product=_norm_sub_product(body.sub_product, proj.platform_type),
        channel=_norm_channel(body.channel, proj.platform_type),
        req_count=body.req_count or 0, content=body.content, memo=body.memo, created_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return ok(_to_out(db, r))


@router.patch("/{rid}")
def update_release(rid: int, body: ReleaseUpdate, db: Session = Depends(get_db), user: User = Depends(require_platform_admin)):
    r = db.get(ReleaseRecord, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="发版记录不存在")
    proj = db.get(Project, r.project_id)
    ptype = proj.platform_type if proj else None
    if body.version is not None:
        r.version = body.version.strip()
    if "sub_product" in body.model_fields_set:
        # 显式传入才更新：传值→按项目类型校验白名单；传 null/空→清为未指定。未传则保持原值。
        r.sub_product = _norm_sub_product(body.sub_product, ptype)
    if "channel" in body.model_fields_set:
        # 显式传入才更新：APP 端项目规整存储；非 APP 端一律清空；未传则保持原值。
        r.channel = _norm_channel(body.channel, ptype)
    if body.release_date is not None:
        r.release_date = body.release_date
    if body.req_count is not None:
        r.req_count = body.req_count
    if body.content is not None:
        r.content = body.content
    if body.memo is not None:
        r.memo = body.memo
    db.commit()
    db.refresh(r)
    return ok(_to_out(db, r))


@router.delete("/{rid}")
def delete_release(rid: int, db: Session = Depends(get_db), user: User = Depends(require_platform_admin)):
    r = db.get(ReleaseRecord, rid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="发版记录不存在")
    db.delete(r)
    db.commit()
    return ok({"deleted": rid})
