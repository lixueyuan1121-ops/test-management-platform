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
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import AiTaskStatus, ChecklistStatus, ProjectRole, ReviewStatus
from app.db.session import SessionLocal, get_db
from app.models import AiTask, ChecklistItem, Project, Task, TestCase, User
from app.schemas.ai import ExtractUrlIn, TestCaseGenIn, TestCaseReviewIn, BulkRegressionIn
from app.schemas.common import ok
from app.services import claude_runner, extractors, generators, selectors
from app.services.claude_runner import selector_fix_info, _SELECTOR_FIX_MARK, pages_for_script, revalidate_for_backfill, validate_script_for_edit
from app.services.playwright_exporter import export_case_to_playwright

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
        "page": getattr(tc, "page", None),  # 关联选择器页面(逗号分隔多页)
        "is_regression": bool(getattr(tc, "is_regression", False)),  # 是否在回归用例库
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
    pages = body.pages or None   # 目标页面:收窄注入 key + 无 key 用例兜底打标

    def sse():
        raw = ""
        meta: dict | None = None
        err: str | None = None
        t0 = time.monotonic()
        try:
            for evt in engine.stream_generate(requirement, project_id=project_id, pages=pages):
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
                    # 页面:优先按 script 用到的 key 自动推断(parse 已填);无 key 用例回落生成时所选页面
                    page=c.get("page") or (",".join(pages) if pages else None),
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
    """某项目的 AI 生成历史（战绩基础：状态/条数/成本/token/耗时）。

    仅测试点生成(kind=testcase_gen);对话测评 query 生成的历史另见 eval 侧。
    """
    assert_project_role(db, user, project_id, _ALL_ROLES)
    rows = (
        db.query(AiTask)
        .filter(AiTask.project_id == project_id, AiTask.kind == "testcase_gen")
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
    page: str | None = Query(None),
    is_regression: bool | None = Query(None),
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
            # exec_kind 支持逗号分隔多值(如 gui,e2e):按并集过滤;单值时等价于 ==(向后兼容)。
            kinds = [k.strip() for k in exec_kind.split(",") if k.strip()]
            if kinds:
                q = q.filter(TestCase.exec_kind.in_(kinds))
        if provider:
            q = q.filter(TestCase.provider == provider)
        if selector_fix:
            # 「仅补选择器即可自动化」= kind_reason 以标识前缀开头(SQL 下推,不全量捞)。
            q = q.filter(TestCase.kind_reason.like(f"{_SELECTOR_FIX_MARK}%"))
        if is_regression is not None:
            q = q.filter(TestCase.is_regression == is_regression)
        if page:
            # page 逗号分隔多页,按整段匹配(避免"任务"误命中"任务列表"):恰等 / 首 / 尾 / 中。
            q = q.filter(or_(
                TestCase.page == page,
                TestCase.page.like(f"{page},%"),
                TestCase.page.like(f"%,{page}"),
                TestCase.page.like(f"%,{page},%"),
            ))
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
            TestCase.page, TestCase.is_regression,
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


@router.patch("/testcases/regression")
def bulk_set_regression(
    body: BulkRegressionIn,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量标记/取消回归。project_id 走体外鉴权;只改属于该项目的用例(跨项目 id 忽略)。

    路由须注册在 /testcases/{cid} 之前,否则 "regression" 会被当成 cid 捕获。
    """
    assert_project_role(db, user, project_id, _WRITE_ROLES)
    ids = list(dict.fromkeys(body.ids))
    n = (db.query(TestCase)
         .filter(TestCase.id.in_(ids), TestCase.project_id == project_id)
         .update({TestCase.is_regression: body.is_regression}, synchronize_session=False))
    db.commit()
    return ok({"updated": n, "is_regression": body.is_regression})


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
    if body.page is not None:
        # 手动指定用例所属页面(逗号分隔多页);空串→清空(置 None)
        tc.page = body.page.strip()[:255] or None
    if body.is_regression is not None:
        tc.is_regression = body.is_regression

    # 直接编辑结构化 script:按用例当前(含本次可能刚改的)exec_kind 分流校验,合法才入库,
    # 并按新 script 用到的 key 重推关联页面(与重生逻辑一致)。不合法 → 400 附原因。
    if body.script is not None:
        eff_kind = getattr(tc, "exec_kind", "gui") or "gui"
        norm, verr = validate_script_for_edit(eff_kind, body.script, project_id=tc.project_id, db=db)
        if verr is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"script 不合法:{verr}")
        tc.script = json.dumps(norm, ensure_ascii=False)
        p = pages_for_script(norm, tc.project_id)
        if p:
            tc.page = p   # 按新 script 的 key 重推页面(推断为空则保留原页面,不清)

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
    tc_old_script = _load_script_list(getattr(tc, "script", None))   # 待补用例保留的原始 script(供确定性回填)

    # ---- 确定性回填快路径(仅「选择器待补」的 gui/e2e)----
    # 补 key 后,若旧 script 引用的 key 现已全部注册且结构合法 → 直接回填,不调 AI:
    # 避免 AI 盲重写导致 key 名漂移、反复降级(同批次多条缺同一 key 时补一次即可全部回填)。
    if sel_fix and kind in ("gui", "e2e") and tc_old_script:
        norm, verr = revalidate_for_backfill(tc_old_script, project_id=tc_project_id)
        if verr is None:
            db.close()
            return _write_back_script(cid, norm, kind, sel_fix, tc_project_id)
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
    # ---- 新 session 写回结果(与确定性回填共用)----
    return _write_back_script(cid, script, kind, sel_fix, tc_project_id)


def _load_script_list(raw) -> list | None:
    """把库里 script(JSON 字符串)解析为 list;空/坏 → None(无旧 script 可回填,落 AI)。"""
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return v if isinstance(v, list) and v else None


def _write_back_script(cid: int, script: list, kind: str, sel_fix: bool, project_id: int) -> dict:
    """新开 session 把重生/回填得到的 script 写回用例并返回 _to_case_out。

    确定性回填与 AI 重生两条路径共用:恢复可执行 exec_kind、清「选择器待补」标识与上次失败原因、
    按 script 用到的 key 重新打页面标。db 已在调用前关闭,故此处另开短 session。
    """
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
        p = pages_for_script(script, project_id)
        if p:
            tc2.page = p                  # 按新 script 用到的 key 重新打页面标(推断为空则保留原页面,不清)
        s.commit()
        s.refresh(tc2)
        return ok(_to_case_out(tc2))
    finally:
        s.close()


# ---- 导出 Playwright 脚本（回归用例库：给开发本地自测）----
# gui/e2e 用例的结构化 script → 自包含 .spec.mjs（connectOverCDP 连被测客户端）。
# 下载响应直接返回文件字节，**绕开** {code,msg,data} 信封（前端用 blob 接收，见 http.js 约定）。
import io
import re
import zipfile


def _export_kind(tc) -> str | None:
    """该用例用于导出的有效 kind：gui/e2e 直接用；「选择器待补」降级(manual)的取其原意图。
    返回 None 表示不可导出（api/cli/纯 manual）。"""
    kind = getattr(tc, "exec_kind", "gui") or "gui"
    if kind in ("gui", "e2e"):
        return kind
    sel_fix, _keys, intended = selector_fix_info(getattr(tc, "kind_reason", None))
    if kind == "manual" and sel_fix and intended in ("gui", "e2e"):
        return intended
    return None


def _safe_filename(title: str, cid: int) -> str:
    """用例标题 → 安全文件名（去路径/特殊字符，限长），带 id 防重名。"""
    base = re.sub(r"[^\w一-鿿-]+", "_", (title or "case").strip())[:40].strip("_") or "case"
    return f"case-{cid}-{base}.spec.mjs"


def _content_disposition(filename: str) -> str:
    """构造 Content-Disposition。HTTP 头须 latin-1，中文文件名走 RFC 5987 的 filename*，
    并给一个纯 ASCII 的 filename 兜底（老浏览器）。"""
    from urllib.parse import quote
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _case_for_export(tc) -> dict:
    """ORM TestCase → 翻译器入参（解析 script JSON 字符串为 list）。"""
    try:
        script = json.loads(tc.script) if tc.script else None
    except (json.JSONDecodeError, ValueError):
        script = None
    return {
        "id": tc.id, "title": tc.title, "exec_kind": _export_kind(tc) or "gui",
        "steps": tc.steps or "", "expected": tc.expected or "", "script": script,
    }


@router.get("/testcases/{cid}/export-playwright")
def export_playwright_one(
    cid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单条 gui/e2e 用例 → 下载一个 .spec.mjs。非 gui/e2e 或无 script → 400。"""
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _ALL_ROLES)   # 读操作：项目内任意角色可导
    if not _export_kind(tc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="仅 gui/e2e 用例支持导出 Playwright 脚本")
    reg = selectors.resolved_registry(db, tc.project_id)
    try:
        text = export_case_to_playwright(_case_for_export(tc), reg["registry"], reg["vmIframe"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    fname = _safe_filename(tc.title, tc.id)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(fname)},
    )


@router.post("/testcases/export-playwright")
def export_playwright_bulk(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """多条用例 → 打包 zip 下载。body: {"ids": [1,2,...]}。
    非 gui/e2e 或无 script 的用例跳过（不阻断其余）；全部被跳过 → 400。"""
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请提供要导出的用例 id 列表")
    rows = db.query(TestCase).filter(TestCase.id.in_(ids)).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未找到任何用例")
    # 逐项目缓存注册表（避免每条重复查库）；同时按项目做鉴权（去重项目集）。
    reg_cache: dict[int, dict] = {}
    checked: set[int] = set()
    buf = io.BytesIO()
    exported, skipped = 0, []
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for tc in rows:
            if tc.project_id not in checked:
                assert_project_role(db, user, tc.project_id, _ALL_ROLES)
                checked.add(tc.project_id)
            if not _export_kind(tc):
                skipped.append(tc.id)
                continue
            if tc.project_id not in reg_cache:
                reg_cache[tc.project_id] = selectors.resolved_registry(db, tc.project_id)
            reg = reg_cache[tc.project_id]
            try:
                text = export_case_to_playwright(_case_for_export(tc), reg["registry"], reg["vmIframe"])
            except ValueError:
                skipped.append(tc.id)   # 无 script 等 → 跳过
                continue
            name = _safe_filename(tc.title, tc.id)
            while name in used_names:   # 理论上 id 已保证唯一，防御性兜底
                name = name.replace(".spec.mjs", f"-{len(used_names)}.spec.mjs")
            used_names.add(name)
            zf.writestr(name, text)
            exported += 1
    if exported == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="选中用例均不可导出（需 gui/e2e 且已生成 script）")
    buf.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="playwright-cases.zip"'}
    if skipped:
        # 附带跳过清单（前端可读此头提示用户）；逗号分隔的 id。
        headers["X-Export-Skipped"] = ",".join(str(i) for i in skipped)
    return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)
