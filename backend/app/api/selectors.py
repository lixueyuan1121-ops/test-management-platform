"""选择器注册表管理路由：语义选择器（SelectorKey）的增删改查 + 作用域（SelectorScope）配置。

沿用全项目约定：{code,msg,data} 信封（ok）、手写 _key_out、体外 assert_project_role
（project_id 来自请求体/query，非路径）。写操作限项目 admin/member。

按 (project_id, sub_product) 分域：sub_product="" 为共享（shared），非空须命中
SUB_PRODUCTS 白名单。candidates 以 JSON 字符串落库（兼容 MySQL 5.6 无原生 JSON）。

后续 Task 4 会在本文件“追加区”末尾续加只读路由（GET resolved + import-legacy）。
"""
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import SelectorKey, SelectorScope, TestCase, User
from app.schemas.common import ok
from app.schemas.selector import SelectorKeyIn, SelectorKeyPatch, SelectorScopeIn
from app.services.selectors import resolved_registry
from app.services.claude_runner import _SELECTOR_FIX_MARK
from app.api.release import SUB_PRODUCTS  # 复用子产品白名单

router = APIRouter(prefix="/api/selectors", tags=["selectors"])
_RW = (ProjectRole.admin, ProjectRole.member)


def _valid_sub(v: str) -> str:
    if v and v not in SUB_PRODUCTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="子产品取值非法")
    return v or ""


def _key_out(r: SelectorKey) -> dict:
    return {"id": r.id, "project_id": r.project_id, "sub_product": r.sub_product,
            "platform": r.platform,
            "key": r.key, "frame": r.frame, "page": r.page, "desc": r.desc,
            "candidates": json.loads(r.candidates or "[]"),
            "updated_by": r.updated_by,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/manage")
def manage(project_id: int = Query(...), db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, _RW)
    rows = db.query(SelectorKey).filter(SelectorKey.project_id == project_id).order_by(SelectorKey.key).all()
    shared, by_sub = [], {}
    for r in rows:
        (shared if r.sub_product == "" else by_sub.setdefault(r.sub_product, [])).append(_key_out(r))
    return ok({"shared": shared, "by_sub": by_sub})


@router.post("")
def create_key(body: SelectorKeyIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _RW)
    sub = _valid_sub(body.sub_product)
    exists = (db.query(SelectorKey)
              .filter(SelectorKey.project_id == body.project_id,
                      SelectorKey.sub_product == sub, SelectorKey.key == body.key).first())
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"该作用域下 key「{body.key}」已存在")
    r = SelectorKey(project_id=body.project_id, sub_product=sub, key=body.key.strip(),
                    platform=body.platform,
                    frame=body.frame or "auto", page=body.page or "", desc=body.desc or "",
                    candidates=json.dumps(body.candidates, ensure_ascii=False),
                    updated_by=user.id, updated_at=datetime.utcnow())
    db.add(r); db.commit(); db.refresh(r)
    return ok(_key_out(r))


@router.patch("/{kid}")
def patch_key(kid: int, body: SelectorKeyPatch, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    if body.platform is not None: r.platform = body.platform
    if body.frame is not None: r.frame = body.frame
    if body.page is not None: r.page = body.page
    if body.desc is not None: r.desc = body.desc
    if body.candidates is not None: r.candidates = json.dumps(body.candidates, ensure_ascii=False)
    r.updated_by = user.id; r.updated_at = datetime.utcnow()
    db.commit(); db.refresh(r)
    return ok(_key_out(r))


@router.delete("/{kid}")
def delete_key(kid: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    # 联动降级:删 key 前把仍引用它的可执行 gui/e2e 用例降为 manual + 写标准「选择器待补」标,
    # 而非任由 script 静默失效(执行机跑到才 fail)。格式与 parse_testcases 一致 → 待补筛选/
    # badge/一键重生/批量回填自动适用;script 保留,重新加回 key 即可批量回填复活。
    affected = _cases_using_key(db, r.project_id, r.key)
    for tc in affected:
        tc.kind_reason = f"{_SELECTOR_FIX_MARK} 补齐选择器 key:{r.key} 后即可执行 {tc.exec_kind}"[:500]
        tc.exec_kind = "manual"
    db.delete(r); db.commit()
    return ok({"deleted": kid, "downgraded": len(affected)})


def _cases_using_key(db: Session, project_id: int, key: str) -> list[TestCase]:
    """项目内 script 引用了 key 的可执行(gui/e2e)用例。

    SQL LIKE 粗筛(script 是大 TEXT,先缩小候选集)再 Python 解析确认 target.key 恰等,
    避免子串误伤(如 navHome 命中 navHomeBadge)。manual 用例不收:已不可执行,降无可降。
    """
    rows = (db.query(TestCase)
            .filter(TestCase.project_id == project_id,
                    TestCase.exec_kind.in_(("gui", "e2e")),
                    TestCase.script.like(f"%{key}%"))
            .all())
    out = []
    for tc in rows:
        try:
            steps = json.loads(tc.script or "[]")
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(steps, list):
            continue
        for st in steps:
            tgt = st.get("target") if isinstance(st, dict) else None
            if isinstance(tgt, dict) and tgt.get("key") == key:
                out.append(tc)
                break
    return out


@router.get("/{kid}/usage")
def key_usage(kid: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """该 key 被哪些可执行用例引用(删除前的影响范围预览,前端确认框展示)。"""
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    cases = _cases_using_key(db, r.project_id, r.key)
    return ok({"count": len(cases),
               "cases": [{"id": c.id, "title": c.title, "exec_kind": c.exec_kind} for c in cases]})


@router.put("/scope")
def set_scope(body: SelectorScopeIn, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _RW)
    sub = _valid_sub(body.sub_product)
    sc = (db.query(SelectorScope)
          .filter(SelectorScope.project_id == body.project_id, SelectorScope.sub_product == sub).first())
    if not sc:
        sc = SelectorScope(project_id=body.project_id, sub_product=sub)
        db.add(sc)
    sc.vm_iframe = body.vm_iframe or ""
    sc.updated_at = datetime.utcnow()
    db.commit(); db.refresh(sc)
    return ok({"id": sc.id, "project_id": sc.project_id, "sub_product": sc.sub_product, "vm_iframe": sc.vm_iframe})


# ---- Task 4 追加区：GET /resolved（合并解析）+ POST /import-legacy（迁移旧常量）----


@router.get("")
def resolved(project_id: int = Query(...), sub_product: str = Query(""),
             db: Session = Depends(get_db), ctx: RunnerCtx = Depends(require_runner_ctx)):
    """runner 拉合并后有效注册表(runner token 鉴权)。"""
    return ok(resolved_registry(db, project_id, _valid_sub(sub_product)))


# 内置旧注册表路径(仓库内 selectors.json),供一次性导入
_LEGACY = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "tools", "qalab-runner", "gui-mcp", "selectors.json"))


@router.post("/import-legacy")
def import_legacy(project_id: int = Query(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """把内置 selectors.json 导入为该项目【项目级共享】。幂等:同名 key 跳过。仅项目 admin。"""
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    try:
        with open(_LEGACY, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"读取内置注册表失败:{e}")
    reg = data.get("registry", {})
    have = {k[0] for k in db.query(SelectorKey.key).filter(
        SelectorKey.project_id == project_id, SelectorKey.sub_product == "").all()}
    imported = skipped = 0
    for k, v in reg.items():
        if k in have:
            skipped += 1; continue
        db.add(SelectorKey(project_id=project_id, sub_product="", key=k,
                           frame=v.get("frame", "auto"), desc=v.get("desc", ""),
                           candidates=json.dumps(v.get("candidates", []), ensure_ascii=False),
                           updated_by=user.id, updated_at=datetime.utcnow()))
        imported += 1
    # vmIframe 写入共享 scope
    vm = data.get("vmIframe", "")
    if vm:
        sc = (db.query(SelectorScope).filter(SelectorScope.project_id == project_id,
                                             SelectorScope.sub_product == "").first())
        if not sc:
            sc = SelectorScope(project_id=project_id, sub_product=""); db.add(sc)
        sc.vm_iframe = vm; sc.updated_at = datetime.utcnow()
    db.commit()
    return ok({"imported": imported, "skipped": skipped})
