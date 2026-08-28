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


# ---- 运行时自学习候选(self-healing 上报 + 评审;注册在 /{kid} 动态路由之前避免吞路径)----

# 每个 key 同时在注册表里挂的「试用中(src=learned 未转正)」候选上限——防持续误愈把候选链撑爆。
_MAX_LEARNED_PER_KEY = 2


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
    行为:同 (scope,key,by,value) 幂等去重(重复上报 hit_count+1);新候选若 key 在注册表且
    试用位未满 → 追加到候选链尾部(src:"learned" 试用标);rejected 过的同候选不再入注册表。
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
        best = cands[0]
        row = (db.query(SelectorLearned)
               .filter(SelectorLearned.project_id == project_id,
                       SelectorLearned.sub_product == sub,
                       SelectorLearned.key == key,
                       SelectorLearned.cand_by == best.get("by"),
                       SelectorLearned.cand_value == str(best.get("value"))[:255])
               .first())
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
        # 追加到注册表候选链尾部(试用位):key 必须已注册,且试用中候选未超上限、无同 by+value
        sk = (db.query(SelectorKey)
              .filter(SelectorKey.project_id == project_id,
                      SelectorKey.sub_product == sub, SelectorKey.key == key).first())
        if sk:
            try:
                existing = json.loads(sk.candidates or "[]")
            except (json.JSONDecodeError, ValueError):
                existing = []
            if not isinstance(existing, list):
                existing = []
            dup = any(isinstance(c, dict) and c.get("by") == best.get("by")
                      and c.get("value") == best.get("value") for c in existing)
            probation = sum(1 for c in existing
                            if isinstance(c, dict) and c.get("src") == "learned")
            if not dup and probation < _MAX_LEARNED_PER_KEY:
                existing.append(best)   # best 已带 src:"learned"(runner 铸造时打标)
                sk.candidates = json.dumps(existing, ensure_ascii=False)
                sk.updated_at = datetime.utcnow()
                appended += 1
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
    row = db.get(SelectorLearned, lid)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="记录不存在")
    assert_project_role(db, user, row.project_id, _RW)
    action = (body or {}).get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="action 须为 approve/reject")
    sk = (db.query(SelectorKey)
          .filter(SelectorKey.project_id == row.project_id,
                  SelectorKey.sub_product == row.sub_product,
                  SelectorKey.key == row.key).first())
    cands = []
    if sk:
        try:
            cands = json.loads(sk.candidates or "[]")
        except (json.JSONDecodeError, ValueError):
            cands = []
        if not isinstance(cands, list):
            cands = []
    if action == "approve":
        row.status = "approved"
        # 去掉试用标 → 变成正式候选(位置保持在链尾:它是"其它候选都挂了才有的"最后防线,不抢排序)
        if sk:
            changed = False
            for c in cands:
                if isinstance(c, dict) and c.get("by") == row.cand_by \
                        and c.get("value") == row.cand_value and c.get("src") == "learned":
                    c.pop("src", None)
                    changed = True
            if changed:
                sk.candidates = json.dumps(cands, ensure_ascii=False)
                sk.updated_at = datetime.utcnow()
    else:
        row.status = "rejected"
        # 从注册表移除该候选(同 by+value 且带 learned 标的;已转正的不误删)
        if sk:
            kept = [c for c in cands
                    if not (isinstance(c, dict) and c.get("by") == row.cand_by
                            and c.get("value") == row.cand_value and c.get("src") == "learned")]
            if len(kept) != len(cands):
                sk.candidates = json.dumps(kept, ensure_ascii=False)
                sk.updated_at = datetime.utcnow()
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    db.commit()
    return ok(_learned_out(row))


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
