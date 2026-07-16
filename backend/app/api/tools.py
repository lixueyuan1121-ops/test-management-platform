"""测试工具广场 API：分类管理 + 工具管理 + 上下线。

- 分类 CRUD：仅平台管理员
- 工具 CRUD：仅平台管理员
- 工具广场浏览（GET 在线工具+分类）：所有登录用户
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.deps import get_current_user, require_platform_admin
from app.core.enums import ToolStatus
from app.db.session import get_db
from app.models import ToolCategory, TestTool, User
from app.schemas.common import ok

router = APIRouter(prefix="/api/tools", tags=["tools"])


# ===== Schemas =====

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0

class CategoryUpdate(BaseModel):
    name: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None

class CategoryOut(BaseModel):
    id: int
    name: str
    sort_order: int
    is_active: bool
    class Config: pass

class ToolCreate(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    download_url: str | None = None
    doc_url: str | None = None
    icon: str | None = None
    version: str | None = None
    sort_order: int = 0

class ToolUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = None
    description: str | None = None
    download_url: str | None = None
    doc_url: str | None = None
    icon: str | None = None
    version: str | None = None
    sort_order: int | None = None
    status: str | None = None

class ToolOut(BaseModel):
    id: int
    category_id: int
    category_name: str
    name: str
    description: str | None = None
    download_url: str | None = None
    doc_url: str | None = None
    icon: str | None = None
    version: str | None = None
    status: str
    sort_order: int
    class Config: pass


def _cat_out(c: ToolCategory) -> dict:
    return {"id": c.id, "name": c.name, "sort_order": c.sort_order, "is_active": c.is_active}

def _tool_out(db: Session, t: TestTool) -> dict:
    cat = db.get(ToolCategory, t.category_id)
    return {
        "id": t.id, "category_id": t.category_id,
        "category_name": cat.name if cat else "",
        "name": t.name, "description": t.description,
        "download_url": t.download_url, "doc_url": t.doc_url,
        "icon": t.icon, "version": t.version,
        "status": t.status.value, "sort_order": t.sort_order,
    }


# ===== 分类 CRUD（仅平台管理员）=====

@router.get("/categories")
def list_categories(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(ToolCategory)
    if not include_inactive:
        q = q.filter(ToolCategory.is_active == True)
    rows = q.order_by(ToolCategory.sort_order, ToolCategory.id).all()
    return ok([_cat_out(c) for c in rows])

@router.post("/categories")
def create_category(body: CategoryCreate, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    c = ToolCategory(name=body.name, sort_order=body.sort_order)
    db.add(c); db.commit(); db.refresh(c)
    return ok(_cat_out(c))

@router.patch("/categories/{cid}")
def update_category(cid: int, body: CategoryUpdate, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    c = db.get(ToolCategory, cid)
    if not c: raise HTTPException(404, "分类不存在")
    if body.name is not None: c.name = body.name
    if body.sort_order is not None: c.sort_order = body.sort_order
    if body.is_active is not None: c.is_active = body.is_active
    db.commit()
    return ok(_cat_out(c))

@router.delete("/categories/{cid}")
def delete_category(cid: int, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    c = db.get(ToolCategory, cid)
    if not c: raise HTTPException(404, "分类不存在")
    db.delete(c); db.commit()
    return ok({"deleted": cid})


# ===== 工具 CRUD（仅平台管理员）=====

@router.get("")
def list_tools(
    category_id: int | None = None,
    online_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """所有登录用户可浏览；online_only=true 只返回在线工具（广场用）。"""
    q = db.query(TestTool)
    if category_id: q = q.filter(TestTool.category_id == category_id)
    if online_only: q = q.filter(TestTool.status == ToolStatus.online)
    rows = q.order_by(TestTool.sort_order, TestTool.id).all()
    return ok([_tool_out(db, t) for t in rows])

@router.post("")
def create_tool(body: ToolCreate, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    if not db.get(ToolCategory, body.category_id):
        raise HTTPException(404, "分类不存在")
    t = TestTool(**body.model_dump(), status=ToolStatus.online)
    db.add(t); db.commit(); db.refresh(t)
    return ok(_tool_out(db, t))

@router.patch("/{tid}")
def update_tool(tid: int, body: ToolUpdate, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    t = db.get(TestTool, tid)
    if not t: raise HTTPException(404, "工具不存在")
    for f in ("category_id","name","description","download_url","doc_url","icon","version","sort_order"):
        v = getattr(body, f, None)
        if v is not None: setattr(t, f, v)
    if body.status is not None:
        t.status = ToolStatus(body.status)
    db.commit()
    return ok(_tool_out(db, t))

@router.delete("/{tid}")
def delete_tool(tid: int, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    t = db.get(TestTool, tid)
    if not t: raise HTTPException(404, "工具不存在")
    db.delete(t); db.commit()
    return ok({"deleted": tid})

@router.patch("/{tid}/toggle")
def toggle_tool(tid: int, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    """上下线切换"""
    t = db.get(TestTool, tid)
    if not t: raise HTTPException(404, "工具不存在")
    t.status = ToolStatus.offline if t.status == ToolStatus.online else ToolStatus.online
    db.commit()
    return ok({"id": t.id, "status": t.status.value})
