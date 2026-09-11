"""模块入口路由:登记每个模块(page)从首页到达的导航链。POST 为 upsert(by project+sub+page)。
沿用信封 ok / assert_project_role 体外鉴权 / 手写 _to_out。"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ModuleEntry, SelectorKey, User
from app.schemas.common import ok
from app.schemas.module_entry import ModuleEntryIn

router = APIRouter(prefix="/api/modules", tags=["modules"])
_RW = (ProjectRole.admin, ProjectRole.member)


def _to_out(db: Session, r: ModuleEntry) -> dict:
    try:
        nav = json.loads(r.nav_keys or "[]")
    except (json.JSONDecodeError, ValueError):
        nav = []
    key_count = (db.query(SelectorKey)
                 .filter(SelectorKey.project_id == r.project_id,
                         SelectorKey.sub_product == r.sub_product,
                         SelectorKey.page == r.page).count())
    return {"id": r.id, "project_id": r.project_id, "sub_product": r.sub_product,
            "page": r.page, "nav_keys": nav if isinstance(nav, list) else [],
            "ready_key": r.ready_key or "", "desc": r.desc or "", "key_count": key_count}


@router.get("")
def list_modules(project_id: int = Query(...), sub_product: str = Query(""),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, _RW)
    rows = (db.query(ModuleEntry)
            .filter(ModuleEntry.project_id == project_id, ModuleEntry.sub_product == sub_product)
            .order_by(ModuleEntry.page).all())
    return ok([_to_out(db, r) for r in rows])


@router.post("")
def upsert_module(body: ModuleEntryIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _RW)
    if not body.page.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="page(模块名)不能为空")
    row = (db.query(ModuleEntry)
           .filter(ModuleEntry.project_id == body.project_id,
                   ModuleEntry.sub_product == body.sub_product,
                   ModuleEntry.page == body.page.strip()).first())
    if not row:
        row = ModuleEntry(project_id=body.project_id, sub_product=body.sub_product, page=body.page.strip())
        db.add(row)
    row.nav_keys = json.dumps(body.nav_keys or [], ensure_ascii=False)
    row.ready_key = (body.ready_key or "").strip()
    row.desc = body.desc or ""
    row.updated_by = user.id
    row.updated_at = datetime.utcnow()
    db.commit(); db.refresh(row)
    return ok(_to_out(db, row))


@router.delete("/{mid}")
def delete_module(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(ModuleEntry, mid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="模块入口不存在")
    assert_project_role(db, user, r.project_id, _RW)
    db.delete(r); db.commit()
    return ok({"deleted": 1})
