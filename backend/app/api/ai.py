"""QA Copilot 路由：AI 生成测试点（流式 SSE + 落库）、历史/用例查询、采纳标记。

流式落库的坑：StreamingResponse 的生成器在依赖清理（get_db 关闭 session）之后
才被迭代，所以生成器内部不能用注入的 db。做法是：路由函数体内（db 仍活）先建
running 记录拿到 id；SSE 生成器内部另开 SessionLocal 完成落库。
"""
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import AiTaskStatus, ChecklistStatus, ProjectRole, ReviewStatus
from app.db.session import SessionLocal, get_db
from app.models import AiTask, ChecklistItem, Project, Task, TestCase, User
from app.schemas.ai import ExtractUrlIn, TestCaseGenIn, TestCaseReviewIn
from app.schemas.common import ok
from app.services import claude_runner, extractors, generators
from app.services.claude_runner import selector_fix_info, _SELECTOR_FIX_MARK

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/ai", tags=["ai"])

_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _user_name(db: Session, uid: int) -> str:
    u = db.get(User, uid)
    return u.name if u else ""


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _to_case_out(tc, task_title: str | None = None, with_script: bool = True) -> dict:
    # tc 可为 ORM TestCase 或 with_entities 的具名 Row(列表瘦身场景,不含 script)。
    # review_status 两种来源都可能是枚举或裸字符串,统一取 .value 兜底。
    rs = tc.review_status
    kind_reason = getattr(tc, "kind_reason", None)
    sel_fix, sel_fix_keys, _sel_fix_kind = selector_fix_info(kind_reason)   # 仅因选择器缺失降级?缺哪些 key
    out = {
        "id": tc.id,
        "ai_task_id": tc.ai_task_id,
        "project_id": tc.project_id,
        "task_id": tc.task_id,
        "category": tc.category,
        "title": tc.title,
        "steps": tc.steps,
        "expected": tc.expected,
        "priority": tc.priority,
        "exec_kind": getattr(tc, "exec_kind", "gui"),
        "provider": getattr(tc, "provider", "claude"),
        "kind_reason": kind_reason,
        "selector_fix": sel_fix,            # True=仅补选择器即可自动化(前端据此显标签/筛选)
        "selector_fix_keys": sel_fix_keys,  # 待补的选择器 key 列表(直接展示,免 hover)
        "last_gen_error": getattr(tc, "last_gen_error", None),  # 上次重生 script 失败原因(成功清空;列表瘦身 Row 无此列→None)
        "adopted": tc.adopted,
        "review_status": getattr(rs, "value", rs),
        "reviewed_at": tc.reviewed_at.isoformat() if tc.reviewed_at else None,
        "created_at": tc.created_at.isoformat() if tc.created_at else None,
        "task_title": task_title,
    }
    if with_script:
        # 单条/详情场景才带 script(结构化步骤 JSON,可达数 KB);列表瘦身时不查此列。
        out["script"] = getattr(tc, "script", None)
    return out


def _to_task_out(db: Session, at: AiTask) -> dict:
    return {
        "id": at.id,
        "project_id": at.project_id,
        "task_id": at.task_id,
        "user_id": at.user_id,
        "user_name": _user_name(db, at.user_id),
        "kind": at.kind,
        "provider": getattr(at, "provider", "claude"),
        "input_type": at.input_type.value,
        "status": at.status.value,
        "case_count": at.case_count,
        "cost_usd": float(at.cost_usd) if at.cost_usd is not None else None,
        "output_tokens": at.output_tokens,
        "duration_ms": at.duration_ms,
        "created_at": at.created_at.isoformat() if at.created_at else None,
    }


@router.get("/status")
def ai_status(user: User = Depends(get_current_user)):
    """AI 是否可用 + 各生成引擎可用性（前端据此显隐入口、渲染引擎选择器）。

    providers: [{id, available}, ...]；available: 任一引擎可用即为真（兼容旧前端字段）。
    """
    provs = generators.available_providers()
    return ok({
        "available": any(p["available"] for p in provs),
        "providers": provs,
        "default": generators.DEFAULT_PROVIDER,
    })


@router.post("/extract-url")
def extract_url_ep(body: ExtractUrlIn, user: User = Depends(get_current_user)):
    """抓取需求 URL 正文，返回给前端预览/编辑后再生成（不直接触发 AI）。"""
    try:
        title, text = extractors.extract_from_url(body.url)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    text = (text or "").strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未从该链接提取到正文")
    return ok({"title": title, "chars": len(text), "text": text[:20000]})


@router.post("/extract-file")
async def extract_file_ep(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """解析上传文档（txt/md/docx/pdf）为纯文本，供前端预览/编辑后再生成。"""
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件过大（>5MB）")
    try:
        text = extractors.extract_from_file(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception:
        logger.exception("文档解析失败")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文档解析失败，请检查文件是否损坏")
    text = (text or "").strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未从文档提取到文本")
    return ok({"filename": file.filename, "chars": len(text), "text": text[:20000]})


@router.post("/testcases")
def gen_testcases(
    body: TestCaseGenIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """流式生成测试点并落库。SSE 事件：delta（增量文本）/ error / done（含落库结果）。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    # 选择生成引擎(claude/deepseek/...);非法/空回落默认。可用性针对所选引擎判定。
    provider_id = generators.normalize_provider(body.provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"生成引擎「{provider_id}」未启用或不可用")

    at = AiTask(
        project_id=body.project_id,
        task_id=body.task_id,
        user_id=user.id,
        kind="testcase_gen",
        provider=provider_id,
        input_type=body.input_type,
        input_ref=body.requirement[:20000],
        status=AiTaskStatus.running,
    )
    db.add(at)
    db.commit()
    db.refresh(at)

    ai_task_id = at.id
    project_id = body.project_id
    task_id = body.task_id
    requirement = body.requirement

    def sse():
        raw = ""
        meta: dict | None = None
        err: str | None = None
        t0 = time.monotonic()
        try:
            for evt in engine.stream_generate(requirement, project_id=project_id):
                etype = evt.get("type")
                if etype == "heartbeat":
                    # SSE 注释帧:保持连接有字节流动,防网关空闲超时切断;前端解析忽略非 data: 行
                    yield ": hb\n\n"
                elif etype == "delta":
                    raw += evt["text"]
                    yield _sse({"type": "delta", "text": evt["text"]})
                elif etype == "result":
                    meta = evt
                    if evt.get("text"):
                        raw = evt["text"]
                elif etype == "error":
                    err = evt.get("msg")
                    yield _sse({"type": "error", "msg": err})
        except Exception as e:  # 流中断（客户端断开/子进程异常）
            logger.exception("AI 流式生成异常")
            err = err or f"生成中断：{e}"

        # ---- 落库（新 session，注入 db 此刻已关闭）----
        s = SessionLocal()
        try:
            at2 = s.get(AiTask, ai_task_id)
            if at2 is None:
                yield _sse({"type": "error", "msg": "任务记录丢失"})
                return
            if meta:
                # duration_ms:引擎给了就用,没给(如 deepseek)用 wall-clock 兜底,保证战绩墙耗时可统计
                at2.duration_ms = meta.get("duration_ms") or int((time.monotonic() - t0) * 1000)
                at2.cost_usd = meta.get("cost_usd")
                at2.output_tokens = meta.get("output_tokens")
            at2.output_raw = raw or None

            cases = engine.parse_testcases(raw, project_id=project_id)
            if not cases:
                at2.status = AiTaskStatus.failed
                # 诊断:区分「claude 没输出/被切」(raw 短或空) vs「输出了但没解析出」(raw 长但格式不符)。
                # 把 raw 长度+尾部带进 error,便于排查(完整 raw 已存 output_raw)。
                if err:
                    detail = err
                elif not raw:
                    detail = "未检测到有效测试点:claude 无任何输出(可能被网关/超时切断)"
                else:
                    detail = f"未检测到有效测试点:claude 输出 {len(raw)} 字但未解析出用例数组(尾部:…{raw[-200:]})"
                at2.error = detail[:2000]
                s.commit()
                yield _sse({"type": "done", "ai_task_id": ai_task_id,
                            "status": "failed", "msg": at2.error, "cases": []})
                return

            objs = []
            for c in cases:
                tc = TestCase(
                    ai_task_id=ai_task_id,
                    provider=provider_id,
                    project_id=project_id,
                    task_id=task_id,
                    category=c["category"] or None,
                    title=c["title"],
                    steps=c["steps"] or None,
                    expected=c["expected"] or None,
                    priority=c["priority"] or None,
                    exec_kind=c.get("kind") or "manual",       # 生成侧已判类型;缺省 manual(不误派)
                    kind_reason=c.get("kind_reason") or None,
                    script=c.get("script") or None,            # gui/e2e 的结构化步骤(JSON 字符串);其余为 None
                )
                s.add(tc)
                objs.append(tc)
            at2.status = AiTaskStatus.done
            at2.case_count = len(cases)
            s.commit()
            for tc in objs:
                s.refresh(tc)
            yield _sse({
                "type": "done",
                "ai_task_id": ai_task_id,
                "status": "done",
                "cases": [_to_case_out(tc) for tc in objs],
                "meta": {
                    "case_count": at2.case_count,
                    "duration_ms": at2.duration_ms,
                    "cost_usd": float(at2.cost_usd) if at2.cost_usd is not None else None,
                    "output_tokens": at2.output_tokens,
                },
            })
        except Exception as e:
            logger.exception("AI 结果落库失败")
            s.rollback()
            yield _sse({"type": "error", "msg": f"落库失败：{e}"})
        finally:
            s.close()

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.get("/tasks")
def list_ai_tasks(
    project_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """某项目的 AI 生成历史（战绩基础：状态/条数/成本/token/耗时）。"""
    assert_project_role(db, user, project_id, _ALL_ROLES)
    rows = (
        db.query(AiTask)
        .filter(AiTask.project_id == project_id)
        .order_by(AiTask.id.desc())
        .limit(limit)
        .all()
    )
    return ok([_to_task_out(db, r) for r in rows])


@router.get("/tasks/{aid}/cases")
def list_ai_task_cases(
    aid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取某次生成的测试点清单。"""
    at = db.get(AiTask, aid)
    if not at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI 任务不存在")
    assert_project_role(db, user, at.project_id, _ALL_ROLES)
    rows = (
        db.query(TestCase)
        .filter(TestCase.ai_task_id == aid)
        .order_by(TestCase.id)
        .all()
    )
    return ok([_to_case_out(tc) for tc in rows])


@router.get("/cases")
def list_cases(
    project_id: int = Query(...),
    task_id: int | None = Query(None),
    review_status: ReviewStatus | None = Query(None),
    category: str | None = Query(None),
    exec_kind: str | None = Query(None),
    provider: str | None = Query(None),
    selector_fix: bool | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """跨批次查询某项目的测试点（用例库 / 日报已采纳用例共用）。只读，支持多维过滤 + 分页。

    列表**不返回 script**(结构化步骤 JSON,可达数 KB);需要 script 走 GET /ai/testcases/{cid}。
    返回 {items, total}:items 为当前页,total 为过滤后总数(供前端分页控件)。
    """
    assert_project_role(db, user, project_id, _ALL_ROLES)

    def _apply_filters(q):
        q = q.filter(TestCase.project_id == project_id)
        if task_id is not None:
            q = q.filter(TestCase.task_id == task_id)
        if review_status is not None:
            q = q.filter(TestCase.review_status == review_status)
        if category:
            q = q.filter(TestCase.category == category)
        if exec_kind:
            q = q.filter(TestCase.exec_kind == exec_kind)
        if provider:
            q = q.filter(TestCase.provider == provider)
        if selector_fix:
            # 「仅补选择器即可自动化」= kind_reason 以标识前缀开头(SQL 下推,不全量捞)。
            q = q.filter(TestCase.kind_reason.like(f"{_SELECTOR_FIX_MARK}%"))
        if keyword:
            q = q.filter(TestCase.title.ilike(f"%{keyword}%"))
        return q

    total = _apply_filters(db.query(func.count(TestCase.id))).scalar() or 0
    # 只 SELECT 列表展示所需列,刻意排除 script(大 TEXT,仅详情按需取),减小响应体与内存。
    cols = _apply_filters(
        db.query(
            TestCase.id, TestCase.ai_task_id, TestCase.project_id, TestCase.task_id,
            TestCase.category, TestCase.title, TestCase.steps, TestCase.expected,
            TestCase.priority, TestCase.exec_kind, TestCase.provider, TestCase.kind_reason,
            TestCase.adopted, TestCase.review_status, TestCase.reviewed_at, TestCase.created_at,
        )
    )
    rows = cols.order_by(TestCase.id.desc()).limit(limit).offset(offset).all()

    # 批量预取关联任务名，避免 N+1
    task_ids = {r.task_id for r in rows if r.task_id is not None}
    title_map = {}
    if task_ids:
        title_map = dict(
            db.query(Task.id, Task.title).filter(Task.id.in_(task_ids)).all()
        )
    items = [_to_case_out(r, task_title=title_map.get(r.task_id), with_script=False) for r in rows]
    return ok({"items": items, "total": total})


@router.get("/testcases/{cid}")
def get_testcase(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取单条测试点完整信息(含 script)。列表已瘦身不含 script,详情/编辑按需调此接口。"""
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _ALL_ROLES)
    title = None
    if tc.task_id is not None:
        t = db.get(Task, tc.task_id)
        title = t.title if t else None
    return ok(_to_case_out(tc, task_title=title))


@router.patch("/testcases/{cid}")
def review_testcase(
    cid: int,
    body: TestCaseReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑一条测试点：评审三态 和/或 执行类型（exec_kind）。

    - 传 review_status：采纳/否决/置回待定，写 reviewed_at、同步 adopted 兼容列，并触发清单回流。
    - 传 exec_kind：改自动化执行类型（gui/api/cli），供下发到 runner 时决定怎么跑。
    schema 保证两者至少有一个。
    """
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _WRITE_ROLES)

    if body.exec_kind is not None:
        tc.exec_kind = body.exec_kind.value

    if body.review_status is not None:
        tc.review_status = body.review_status
        if body.review_status == ReviewStatus.pending:
            tc.reviewed_at = None
        else:
            tc.reviewed_at = datetime.utcnow()  # 与 issues.py resolved_at 对齐（UTC naive），供 /stats/ai 按日聚合
        tc.adopted = (body.review_status == ReviewStatus.adopted)

        # ---- 采纳回流副作用：带 task_id 的测试点采纳→upsert 清单项；取消采纳→删仍 pending 的清单项 ----
        if tc.task_id is not None:
            existing = (
                db.query(ChecklistItem)
                .filter(ChecklistItem.task_id == tc.task_id,
                        ChecklistItem.test_case_id == tc.id)
                .first()
            )
            if body.review_status == ReviewStatus.adopted:
                if existing is None:
                    db.add(ChecklistItem(
                        task_id=tc.task_id, test_case_id=tc.id, project_id=tc.project_id,
                    ))
                # 已存在则幂等跳过（保留其执行状态）
            else:
                # 取消采纳：仅删仍 pending 未执行的清单项，已执行过的保留（避免丢执行记录）
                if existing is not None and existing.exec_status == ChecklistStatus.pending:
                    db.delete(existing)

    # 正文字段人工修订(可选)。注:改了 steps/expected 后,已生成的 script 可能与新步骤失配,
    # 不在此自动重生成(避免隐式改动);如需按新 steps 重建 script,走单独入口。
    if body.title is not None:
        tc.title = body.title.strip()[:512]
    if body.steps is not None:
        tc.steps = body.steps.strip() or None
    if body.expected is not None:
        tc.expected = body.expected.strip() or None
    if body.category is not None:
        tc.category = body.category.strip()[:32] or None
    if body.priority is not None:
        tc.priority = body.priority.strip()[:8] or None

    db.commit()
    db.refresh(tc)
    return ok(_to_case_out(tc))


@router.delete("/testcases/{cid}")
def delete_testcase(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除一条测试点。级联清理其验收清单项(exec_run 有 SET NULL 外键,自动断开,留痕)。"""
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _WRITE_ROLES)
    # 先删其清单项(checklist_item.test_case_id 无级联删,手动清;exec_run.test_case_id 是 SET NULL 自动断)
    db.query(ChecklistItem).filter(ChecklistItem.test_case_id == cid).delete(synchronize_session=False)
    db.delete(tc)
    db.commit()
    return ok({"deleted": cid})


@router.post("/testcases/{cid}/gen-script")
def gen_script(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按用例当前 steps/expected 重新生成结构化 script(gui/e2e/api)。同步调引擎,写回并返回。

    注意：generate_script 可能阻塞数十秒~数分钟（调 claude CLI），若整个过程持有同一
    DB connection，连接会因 MySQL/中间层空闲超时被断开，commit 时报 2013 Lost connection。
    因此先提取所需字段、关闭原 session，AI 完成后再新开 session 写入。

    对「选择器待补」降级(exec_kind=manual)的用例:补齐 key 后点此可**一键按原意图重生**——
    自动用降级前的 gui/e2e 类型生成,成功则恢复 exec_kind 并清除待补标识(闭环回到可执行)。
    """
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _WRITE_ROLES)
    kind = getattr(tc, "exec_kind", "gui") or "gui"
    sel_fix, _keys, intended = selector_fix_info(getattr(tc, "kind_reason", None))
    if kind == "manual" and sel_fix and intended:
        kind = intended   # 选择器待补的降级用例:按降级前意图(gui/e2e)重生
    if kind not in ("gui", "e2e", "api"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"仅 gui/e2e/api 用例支持生成 script(当前 {kind})")
    # 用与该用例相同的引擎重生 script(保持一致);引擎不可用则回落默认。
    engine = generators.get_provider(getattr(tc, "provider", None))
    if not engine.is_available():
        engine = generators.get_provider(generators.DEFAULT_PROVIDER)

    # ---- 提取 AI 生成所需字段后，关闭原 DB session，避免长阻塞期间连接被断 ----
    tc_title = tc.title
    tc_steps = tc.steps or ""
    tc_expected = tc.expected or ""
    tc_project_id = tc.project_id
    db.close()

    # ---- 调 AI 引擎（阻塞,可能数十秒~数分钟）----
    script, err = engine.generate_script(kind, tc_title, tc_steps, tc_expected, project_id=tc_project_id)
    if err:
        detail = f"生成 script 失败:{err}"
        # db 已在调 AI 前关闭,另开 session 落库失败原因(供事后逐条回看修复),再 raise。
        es = SessionLocal()
        try:
            etc = es.get(TestCase, cid)
            if etc:
                etc.last_gen_error = detail[:2000]
                es.commit()
        finally:
            es.close()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=detail)
    # ---- 新 session 写回结果 ----
    s = SessionLocal()
    try:
        tc2 = s.get(TestCase, cid)
        if not tc2:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点已被删除")
        tc2.script = json.dumps(script, ensure_ascii=False)
        if kind in ("gui", "e2e"):
            tc2.exec_kind = kind          # 降级用例重生成功 → 恢复为可执行类型
        if sel_fix:
            tc2.kind_reason = None        # 已成功重生,清除「选择器待补」标识(前端 badge 随之消失)
        tc2.last_gen_error = None         # 重生成功 → 清除上次失败原因
        s.commit()
        s.refresh(tc2)
        return ok(_to_case_out(tc2))
    finally:
        s.close()
