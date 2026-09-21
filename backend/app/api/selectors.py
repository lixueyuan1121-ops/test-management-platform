"""选择器注册表管理路由：语义选择器（SelectorKey）的增删改查 + 作用域（SelectorScope）配置。

沿用全项目约定：{code,msg,data} 信封（ok）、手写 _key_out、体外 assert_project_role
（project_id 来自请求体/query，非路径）。写操作限项目 admin/member。

按 (project_id, sub_product) 分域：sub_product="" 为共享（shared），非空须命中
SUB_PRODUCTS 白名单。candidates 以 JSON 字符串落库（兼容 MySQL 5.6 无原生 JSON）。

后续 Task 4 会在本文件“追加区”末尾续加只读路由（GET resolved + import-legacy）。
"""
from app.services.script_keys import referenced_keys
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import SelectorKey, SelectorScope, TestCase, User
from app.schemas.common import ok
from app.schemas.selector import (
    SelectorKeyIn, SelectorKeyPatch, SelectorScopeIn,
    SelectorBatchDeleteIn, SelectorBatchPageIn, SelectorImportIn,
)
from app.services.selectors import resolved_registry, scoped_key_rows
from app.services.selector_history import revision as selector_revision, remember as remember_selector
from app.services.selector_ranking import is_valid_candidate, candidate_identity, merge_candidates
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
            "revision": selector_revision(r),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


@router.get("/manage")
def manage(project_id: int = Query(...), sub_product: str = Query(""),
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, _RW)
    rows = db.query(SelectorKey).filter(SelectorKey.project_id == project_id).order_by(SelectorKey.key).all()
    shared, by_sub = [], {}
    for r in rows:
        (shared if r.sub_product == "" else by_sub.setdefault(r.sub_product, [])).append(_key_out(r))
    # 当前作用域的 scope 配置（vm_iframe + 主动探测扫描分支），供前端回显/编辑。
    sub = _valid_sub(sub_product)
    sc = (db.query(SelectorScope)
          .filter(SelectorScope.project_id == project_id, SelectorScope.sub_product == sub).first())
    scope = {"sub_product": sub,
             "vm_iframe": sc.vm_iframe if sc else "",
             "scan_branch": sc.scan_branch if sc else ""}
    return ok({"shared": shared, "by_sub": by_sub, "scope": scope})


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


# ---- 运行时自学习候选(self-healing 上报 + 评审;注册在 /{kid} 动态路由之前避免吞路径)----


def _learned_out(r) -> dict:
    from app.models import SelectorLearned  # noqa: F401 (类型提示用)
    try:
        cand = json.loads(r.candidate or "{}")
    except (json.JSONDecodeError, ValueError):
        cand = {}
    try:
        ev = json.loads(r.evidence or "{}")
    except (json.JSONDecodeError, ValueError):
        ev = {}
    return {"id": r.id, "project_id": r.project_id, "sub_product": r.sub_product,
            "key": r.key, "candidate": cand, "evidence": ev,
            "runner": r.runner, "run_id": r.run_id, "status": r.status,
            "hit_count": r.hit_count,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.post("/learned")
def report_learned(body: dict, db: Session = Depends(get_db),
                   ctx: RunnerCtx = Depends(require_runner_ctx)):
    """runner 上报自愈记录(runner token 鉴权)。

    body: {project_id, sub_product, runner, run_id, items:[{key, candidates:[...], evidence:{}}]}
    行为：候选只进入待评审区，批准后才发布到执行注册表。
    """
    from app.models import SelectorLearned
    from app.services.selector_ranking import is_valid_candidate

    project_id = body.get("project_id")
    if not isinstance(project_id, int):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="缺 project_id")
    sub = _valid_sub(str(body.get("sub_product") or ""))
    runner = str(body.get("runner") or "")[:64]
    run_id = body.get("run_id") if isinstance(body.get("run_id"), int) else None
    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="items 为空")

    accepted = appended = bumped = 0
    for it in items[:20]:   # 单次上限,防异常 runner 灌爆
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        cands = [c for c in (it.get("candidates") or []) if is_valid_candidate(c)]
        if not key or not cands:
            continue
        best = {**cands[0], "src": "learned"}
        possible = (db.query(SelectorLearned)
               .filter(SelectorLearned.project_id == project_id,
                       SelectorLearned.sub_product == sub,
                       SelectorLearned.key == key,
                       SelectorLearned.cand_by == best.get("by"),
                       SelectorLearned.cand_value == str(best.get("value"))[:255])
               .all())
        row = next((r for r in possible if candidate_identity(json.loads(r.candidate or "{}")) == candidate_identity(best)), None)
        if row:
            row.hit_count += 1
            row.runner = runner or row.runner
            row.run_id = run_id or row.run_id
            row.updated_at = datetime.utcnow()
            bumped += 1
            db.commit()
            continue   # rejected/approved/pending 均不重复追加注册表
        row = SelectorLearned(
            project_id=project_id, sub_product=sub, key=key,
            cand_by=best.get("by"), cand_value=str(best.get("value"))[:255],
            candidate=json.dumps(best, ensure_ascii=False),
            all_candidates=json.dumps(cands, ensure_ascii=False),
            evidence=json.dumps(it.get("evidence") or {}, ensure_ascii=False),
            runner=runner, run_id=run_id, status="pending",
        )
        db.add(row)
        accepted += 1
        # 待评审候选只保存在 SelectorLearned，不改变执行注册表。
        db.commit()
    return ok({"accepted": accepted, "appended": appended, "deduped": bumped})


@router.get("/learned")
def list_learned(project_id: int = Query(...), status_f: str = Query("pending", alias="status"),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """自学习候选评审列表(默认 pending)。"""
    from app.models import SelectorLearned
    assert_project_role(db, user, project_id, _RW)
    q = db.query(SelectorLearned).filter(SelectorLearned.project_id == project_id)
    if status_f:
        q = q.filter(SelectorLearned.status == status_f)
    rows = q.order_by(SelectorLearned.id.desc()).limit(200).all()
    # 带上 key 的 desc/page 便于评审时理解语义
    keys = {r.key for r in rows}
    meta = {}
    if keys:
        for sk in db.query(SelectorKey).filter(SelectorKey.project_id == project_id,
                                               SelectorKey.key.in_(keys)).all():
            meta.setdefault(sk.key, {"desc": sk.desc, "page": sk.page})
    out = []
    for r in rows:
        d = _learned_out(r)
        d.update(meta.get(r.key, {}))
        out.append(d)
    return ok(out)


@router.patch("/learned/{lid}")
def review_learned(lid: int, body: dict, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """评审自学习候选:action=approve 转正(去掉试用标,永久保留)/ reject 拒绝(从注册表移除)。"""
    from app.models import SelectorLearned
    row = db.query(SelectorLearned).filter(SelectorLearned.id == lid).populate_existing().with_for_update().first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    assert_project_role(db, user, row.project_id, _RW)
    action = (body or {}).get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="action 须为 approve/reject")
    sk = (db.query(SelectorKey)
          .filter(SelectorKey.project_id == row.project_id,
                  SelectorKey.sub_product == row.sub_product,
                  SelectorKey.key == row.key).populate_existing().with_for_update().first())
    cands = []
    if sk:
        try:
            cands = json.loads(sk.candidates or "[]")
        except (json.JSONDecodeError, ValueError):
            cands = []
        if not isinstance(cands, list):
            cands = []
    candidate = json.loads(row.candidate or "{}")
    identity = candidate_identity(candidate)
    if action == "approve":
        if not sk:
            raise HTTPException(409, detail="原 key 已不存在，请先恢复或新建后再采纳候选")
        remember_selector(db, sk, user.id)
        row.status = "approved"
        sk.updated_by = user.id
        approved = {k: v for k, v in candidate.items() if k not in ("src", "status", "disabled")}
        # 清理历史试用副本；明确批准的候选可以进入有效链。
        kept = [c for c in cands if candidate_identity(c) != identity]
        sk.candidates = json.dumps(merge_candidates([approved], kept), ensure_ascii=False)
        sk.updated_at = datetime.utcnow()
    else:
        row.status = "rejected"
        if sk:
            kept = [c for c in cands if not (candidate_identity(c) == identity and c.get("src") == "learned")]
            if len(kept) != len(cands):
                remember_selector(db, sk, user.id)
                sk.updated_by = user.id
                sk.candidates = json.dumps(kept, ensure_ascii=False)
                sk.updated_at = datetime.utcnow()
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    db.commit()
    return ok(_learned_out(row))


@router.patch("/{kid}")
def patch_key(kid: int, body: SelectorKeyPatch, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    r = db.query(SelectorKey).filter(SelectorKey.id == kid).populate_existing().with_for_update().first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    if not body.expected_revision:
        raise HTTPException(428, detail="请先读取当前选择器版本再保存")
    if body.expected_revision != selector_revision(r):
        raise HTTPException(409, detail="选择器已被其他操作更新，请刷新后重新合并；你的修改尚未覆盖服务器")
    remember_selector(db, r, user.id)
    if body.platform is not None: r.platform = body.platform
    if body.frame is not None: r.frame = body.frame
    if body.page is not None: r.page = body.page
    if body.desc is not None: r.desc = body.desc
    if body.candidates is not None: r.candidates = json.dumps(body.candidates, ensure_ascii=False)
    r.updated_by = user.id; r.updated_at = datetime.utcnow()
    db.commit(); db.refresh(r)
    return ok(_key_out(r))


@router.get("/{kid}/history")
def key_history(kid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.models import SelectorRevision
    row = db.get(SelectorKey, kid)
    if not row:
        raise HTTPException(404, detail="key 不存在")
    assert_project_role(db, user, row.project_id, _RW)
    changes = db.query(SelectorRevision).filter(SelectorRevision.key_id == kid).order_by(SelectorRevision.id.desc()).limit(50).all()
    return ok({"current": _key_out(row), "history": [{"id": r.id, "snapshot": json.loads(r.snapshot),
        "revision": r.revision, "changed_by": r.changed_by, "created_at": r.created_at.isoformat()} for r in changes]})


@router.post("/{kid}/restore")
def restore_key(kid: int, body: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.models import SelectorRevision
    row = db.get(SelectorKey, kid)
    if not row:
        raise HTTPException(404, detail="key 不存在")
    assert_project_role(db, user, row.project_id, _RW)
    old = db.query(SelectorRevision).filter(SelectorRevision.id == body.get("history_id"), SelectorRevision.key_id == kid).first()
    if not old:
        raise HTTPException(404, detail="历史版本不存在")
    return patch_key(kid, SelectorKeyPatch(**json.loads(old.snapshot), expected_revision=body.get("expected_revision")), db, user)


def _downgrade_cases_for_key(db: Session, r: SelectorKey) -> int:
    """删 key 前把仍引用它的可执行 gui/e2e 用例降为 manual + 写标准「选择器待补」标。

    而非任由 script 静默失效(执行机跑到才 fail)。格式与 parse_testcases 一致 → 待补筛选/
    badge/一键重生/批量回填自动适用;script 保留,重新加回 key 即可批量回填复活。
    返回被降级的用例数（不 commit，由调用方统一提交）。
    """
    affected = _cases_using_row(db, r)
    for tc in affected:
        tc.kind_reason = f"{_SELECTOR_FIX_MARK} 补齐选择器 key:{r.key} 后即可执行 {tc.exec_kind}"[:500]
        tc.exec_kind = "manual"
    return len(affected)


@router.delete("/{kid}")
def delete_key(kid: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    downgraded = _downgrade_cases_for_key(db, r)
    db.delete(r); db.commit()
    return ok({"deleted": kid, "downgraded": downgraded})


@router.post("/batch-delete")
def batch_delete(body: SelectorBatchDeleteIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """批量删除选择器 key。逐个复用单删的联动降级(引用它的可执行用例降为「选择器待补」)。

    权限:按每个 key 各自项目校验(通常同项目)。返回 {deleted:实际删除数, downgraded:降级用例总数,
    missing:不存在的 id 列表}。整批尽力而为——不存在的 id 跳过计入 missing,不中断其余。
    """
    deleted, downgraded, missing = 0, 0, []
    for kid in dict.fromkeys(body.ids):   # 去重保序
        r = db.get(SelectorKey, kid)
        if not r:
            missing.append(kid); continue
        assert_project_role(db, user, r.project_id, _RW)
        downgraded += _downgrade_cases_for_key(db, r)
        db.delete(r)
        db.flush()
        deleted += 1
    db.commit()
    return ok({"deleted": deleted, "downgraded": downgraded, "missing": missing})


@router.post("/batch-page")
def batch_set_page(body: SelectorBatchPageIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """批量设置选择器 key 的 page（页面分组）；空串→清空为未分类。按各自项目校验权限。"""
    page = (body.page or "").strip()[:64]
    updated, missing = 0, []
    for kid in dict.fromkeys(body.ids):
        r = db.get(SelectorKey, kid)
        if not r:
            missing.append(kid); continue
        assert_project_role(db, user, r.project_id, _RW)
        remember_selector(db, r, user.id)
        r.page = page
        r.updated_by = user.id
        r.updated_at = datetime.utcnow()
        updated += 1
    db.commit()
    return ok({"updated": updated, "page": page, "missing": missing})


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
        if key in referenced_keys(steps):
            out.append(tc)
    return out


def _cases_using_row(db: Session, row: SelectorKey) -> list[TestCase]:
    # 同名 key 的子产品覆盖只能影响使用此版本的用例。
    effective = {}
    out = []
    for tc in _cases_using_key(db, row.project_id, row.key):
        sub = tc.sub_product or ""
        if sub not in effective:
            effective[sub] = {r.key: r.id for r in scoped_key_rows(db, row.project_id, sub)}
        if effective[sub].get(row.key) == row.id:
            out.append(tc)
    return out


@router.get("/{kid}/usage")
def key_usage(kid: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """该 key 被哪些可执行用例引用(删除前的影响范围预览,前端确认框展示)。"""
    r = db.get(SelectorKey, kid)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key 不存在")
    assert_project_role(db, user, r.project_id, _RW)
    cases = _cases_using_row(db, r)
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
    if body.scan_branch is not None:   # None=本次不改（仅保存 vm_iframe 时不清分支）
        sc.scan_branch = body.scan_branch.strip()
    sc.updated_at = datetime.utcnow()
    db.commit(); db.refresh(sc)
    return ok({"id": sc.id, "project_id": sc.project_id, "sub_product": sc.sub_product,
               "vm_iframe": sc.vm_iframe, "scan_branch": sc.scan_branch})


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


# ---- 主动探测 / 手动导入：扫描分支配置读取 + 通用注册表导入（任意作用域）----


@router.get("/scan-config")
def scan_config(project_id: int = Query(...), sub_product: str = Query(""),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """读取某作用域的主动探测配置（扫描分支 + vm_iframe）。

    供前端回显，以及本地扫描脚本 scan_selectors_from_branch.py 取分支——分支集中配在平台，
    脚本不写死分支、发给他人即可用（git 凭据用本机已有的，不需在服务端配凭据）。
    """
    assert_project_role(db, user, project_id, _RW)
    sub = _valid_sub(sub_product)
    sc = (db.query(SelectorScope)
          .filter(SelectorScope.project_id == project_id, SelectorScope.sub_product == sub).first())
    return ok({"project_id": project_id, "sub_product": sub,
               "vm_iframe": sc.vm_iframe if sc else "",
               "scan_branch": sc.scan_branch if sc else ""})


@router.post("/import")
def import_selectors(body: SelectorImportIn, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """手动/脚本导入注册表到指定作用域 (project_id, sub_product)。

    格式见 docs/选择器格式说明.md：registry={key:{frame,page,desc,candidates}}。
    同名 key：overwrite=False 跳过、True 则 PATCH 覆盖。逐 key 校验候选合法性，
    非法(候选结构不对/key 名超 64/值非对象)只跳过该 key 计入 invalid，不整批失败。
    vm_iframe 非空时写入该作用域 scope。返回 {imported, updated, skipped, invalid}。
    """
    assert_project_role(db, user, body.project_id, _RW)
    res = _apply_import(db, user, body.project_id, _valid_sub(body.sub_product),
                        body.registry or {}, body.vm_iframe, body.overwrite)
    return ok(res)


def _apply_import(db: Session, user: User, project_id: int, sub: str,
                  reg: dict, vm_iframe: str, overwrite: bool) -> dict:
    """把 registry 导入 (project_id, sub) 作用域的核心逻辑(手动导入 + 扫描导入共用)。

    逐 key 校验候选合法性,非法只跳过计入 invalid;同名 overwrite 决定跳过/覆盖;vm_iframe 非空写 scope。
    调用方负责鉴权。返回 {imported, updated, skipped, invalid}。
    """
    have = {r.key: r for r in db.query(SelectorKey).filter(
        SelectorKey.project_id == project_id, SelectorKey.sub_product == sub).all()}
    imported = updated = skipped = 0
    invalid = []
    for k, v in reg.items():
        key = (k or "").strip()
        cands = v.get("candidates", []) if isinstance(v, dict) else None
        if (not key or len(key) > 64 or not isinstance(v, dict)
                or not isinstance(v.get("frame", "auto"), str) or len(v.get("frame") or "auto") > 2048
                or not isinstance(cands, list) or not all(is_valid_candidate(c) for c in cands)):
            invalid.append(k)
            continue
        plat = v.get("platform", "web")
        plat = plat if plat in ("web", "android", "ios") else "web"
        payload = dict(frame=v.get("frame") or "auto", page=v.get("page") or "",
                       desc=v.get("desc") or "", platform=plat,
                       candidates=json.dumps(cands, ensure_ascii=False))
        existing = have.get(key)
        if existing:
            if not overwrite:
                skipped += 1
                continue
            remember_selector(db, existing, user.id)
            existing.frame, existing.page = payload["frame"], payload["page"]
            existing.desc, existing.platform = payload["desc"], payload["platform"]
            existing.candidates = payload["candidates"]
            existing.updated_by, existing.updated_at = user.id, datetime.utcnow()
            updated += 1
        else:
            row = SelectorKey(project_id=project_id, sub_product=sub, key=key,
                              updated_by=user.id, updated_at=datetime.utcnow(), **payload)
            db.add(row)
            db.flush()
            have[key] = row
            imported += 1
    vm = (vm_iframe or "").strip()
    if vm:
        sc = (db.query(SelectorScope).filter(SelectorScope.project_id == project_id,
                                             SelectorScope.sub_product == sub).first())
        if not sc:
            sc = SelectorScope(project_id=project_id, sub_product=sub); db.add(sc)
        sc.vm_iframe = vm; sc.updated_at = datetime.utcnow()
    db.commit()
    return {"imported": imported, "updated": updated, "skipped": skipped, "invalid": invalid}


# ---- 「扫描并导入」按钮:平台机器本机拉分支代码扫 testid 直接入库(需平台机器有 openclaw360-web 副本 + git)----


def _resolve_scan_repo() -> str:
    """定位平台机器上的 openclaw360-web 工作副本(内含 src/test-ids/bindings.ts)。

    优先用 SELECTOR_SCAN_REPO;留空则探测常见路径。找不到返回 ""(调用方给"未配置"提示)。
    """
    from app.core.config import settings
    cands = [settings.SELECTOR_SCAN_REPO] if settings.SELECTOR_SCAN_REPO else []
    cands += [r"D:\git\openclaw360-web\openclaw360-web", r"D:\git\openclaw360-web",
              os.path.expanduser("~/git/openclaw360-web/openclaw360-web")]
    for p in cands:
        if p and os.path.exists(os.path.join(p, "src", "test-ids", "bindings.ts")):
            return p
    return ""


class ScanBranchIn(BaseModel):
    project_id: int
    sub_product: str = ""
    overwrite: bool = False
    no_git: bool = False       # True=不 fetch/checkout,直接扫当前工作副本(副本已在目标分支/离线时用)


@router.post("/scan-branch")
def scan_branch_import(body: ScanBranchIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """一键:按作用域已配的扫描分支,在平台机器本机拉代码扫 data-testid → 导入当前作用域 → 联动回填。

    未满足前置(没配分支/找不到 openclaw360-web 副本/没装 git/git 操作失败)时返回 400 + 明确中文提示,
    前端直接弹出;满足则正常执行(等价于本地 scan_selectors_from_branch.py --import)。
    """
    import shutil
    assert_project_role(db, user, body.project_id, _RW)
    sub = _valid_sub(body.sub_product)

    # 1) 分支:必须已在「选择器管理」保存
    sc = (db.query(SelectorScope)
          .filter(SelectorScope.project_id == body.project_id, SelectorScope.sub_product == sub).first())
    branch = (sc.scan_branch if sc else "").strip()
    if not branch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="未配置扫描分支:请先在上方填「扫描分支」并点「保存分支」")

    # 2) 代码副本:平台机器本机须有 openclaw360-web 工作副本
    repo = _resolve_scan_repo()
    if not repo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="未找到 openclaw360-web 代码副本:请在后端 .env 配置 "
                                   "SELECTOR_SCAN_REPO=<本机 openclaw360-web 目录>(须含 src/test-ids/bindings.ts)")

    # 3) git:非 no_git 模式需要 git 拉分支
    from scripts.scan_selectors_from_branch import (
        parse_testids, build_registry, load_known_map, _checkout_branch,
    )
    head = ""
    if not body.no_git:
        if not shutil.which("git"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="平台机器未安装 git(或不在 PATH):无法拉取分支。装好 git 或勾选「不切分支」直接扫当前副本")
        try:
            head = _checkout_branch(repo, branch) or ""
        except Exception as exc:   # git 认证失败/分支不存在/工作区脏 → 明确回传
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"git 拉取分支「{branch}」失败:{str(exc)[:300]}("
                                       "常见:工作区有未提交改动、分支名错、无内网 GitLab 凭据)") from exc

    # 4) 扫 bindings.ts → 注册表
    bindings = os.path.join(repo, "src", "test-ids", "bindings.ts")
    if not os.path.exists(bindings):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"未找到 {bindings}")
    with open(bindings, encoding="utf-8") as f:
        testids = parse_testids(f.read())
    if not testids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="该分支 bindings.ts 未扫到任何 data-testid")
    known_default = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "docs", "testid-selectors-export-full.json"))
    known_map = load_known_map(known_default)
    registry, auto_keys = build_registry(testids, known_map)

    # 5) 导入当前作用域(复用手动导入的同一套校验/写库)
    res = _apply_import(db, user, body.project_id, sub, registry,
                        'iframe[src*=".work.n.cn"]', body.overwrite)

    # 6) 联动回填「选择器待补」用例(补齐 key 后自动恢复可执行)
    restored = 0
    try:
        from app.api.ai import backfill_testcases
        restored = (backfill_testcases(project_id=body.project_id, db=db, user=user)
                    or {}).get("data", {}).get("restored", 0)
    except Exception:   # 回填失败不影响导入结果
        restored = 0

    return ok({**res, "branch": branch, "head": head, "scanned": len(testids),
               "auto_desc": len(auto_keys), "restored": restored})
