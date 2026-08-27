"""上线 checklist 路由：漏斗末端。回归用例库勾选用例加入 → 本项目上线清单 → 可执行/移除。

沿用全项目约定：{code,msg,data} 信封（ok/fail）、手写 _to_out、体外 assert_project_role。
执行不在此实现——前端勾选后走现有 /api/exec-queue/enqueue-cases（按 test_case_id 下发）。
移除只删本表行，不影响 test_case（回归用例/总用例照旧）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import ReleaseChecklistItem, TestCase, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/release-checklist", tags=["release-checklist"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
_READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


class AddItemsIn(BaseModel):
    """把回归用例加入上线清单。"""
    test_case_ids: list[int] = Field(..., min_length=1)


class RemoveItemsIn(BaseModel):
    """从上线清单移除（只删清单行，不动用例）。"""
    test_case_ids: list[int] = Field(..., min_length=1)


def _item_out(item: ReleaseChecklistItem, tc: TestCase | None) -> dict:
    """清单项 + 引用用例的展示字段。用例被删则 tc=None（理论上 CASCADE 已清，防御）。"""
    return {
        "id": item.id,
        "test_case_id": item.test_case_id,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "title": tc.title if tc else None,
        "category": tc.category if tc else None,
        "priority": tc.priority if tc else None,
        "exec_kind": (tc.exec_kind if tc else None),
        "page": tc.page if tc else None,
        "is_regression": bool(getattr(tc, "is_regression", False)) if tc else False,
    }


@router.get("")
def list_items(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """某项目的上线清单（关联用例展示字段一并返回）。"""
    assert_project_role(db, user, project_id, _READ_ROLES)
    items = (db.query(ReleaseChecklistItem)
             .filter(ReleaseChecklistItem.project_id == project_id)
             .order_by(ReleaseChecklistItem.id.desc()).all())
    tc_ids = [it.test_case_id for it in items]
    tc_map = {}
    if tc_ids:
        for tc in db.query(TestCase).filter(TestCase.id.in_(tc_ids)).all():
            tc_map[tc.id] = tc
    return ok([_item_out(it, tc_map.get(it.test_case_id)) for it in items])


@router.post("/add")
def add_items(
    body: AddItemsIn,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """勾选回归用例加入上线清单（幂等：已在清单/跨项目/不存在的跳过）。"""
    assert_project_role(db, user, project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.test_case_ids))
    # 只接受属于本项目的用例
    valid = {c.id for c in db.query(TestCase.id)
             .filter(TestCase.id.in_(ids), TestCase.project_id == project_id).all()}
    existing = {r.test_case_id for r in db.query(ReleaseChecklistItem.test_case_id)
                .filter(ReleaseChecklistItem.project_id == project_id,
                        ReleaseChecklistItem.test_case_id.in_(ids)).all()}
    added = 0
    for cid in ids:
        if cid in valid and cid not in existing:
            db.add(ReleaseChecklistItem(project_id=project_id, test_case_id=cid, created_by=user.id))
            added += 1
    db.commit()
    return ok({"added": added})


@router.post("/remove")
def remove_items(
    body: RemoveItemsIn,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从上线清单移除（只删本表行，不影响 test_case）。"""
    assert_project_role(db, user, project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.test_case_ids))
    n = (db.query(ReleaseChecklistItem)
         .filter(ReleaseChecklistItem.project_id == project_id,
                 ReleaseChecklistItem.test_case_id.in_(ids))
         .delete(synchronize_session=False))
    db.commit()
    return ok({"removed": n})
