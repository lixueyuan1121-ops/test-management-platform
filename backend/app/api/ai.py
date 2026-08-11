"""QA Copilot 路由：AI 生成测试点（流式 SSE + 落库）、历史/用例查询、采纳标记。

流式落库的坑：StreamingResponse 的生成器在依赖清理（get_db 关闭 session）之后
才被迭代，所以生成器内部不能用注入的 db。做法是：路由函数体内（db 仍活）先建
running 记录拿到 id；SSE 生成器内部另开 SessionLocal 完成落库。
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import AiTaskStatus, ProjectRole, ReviewStatus
from app.db.session import SessionLocal, get_db
from app.models import AiTask, Project, TestCase, User
from app.schemas.ai import ExtractUrlIn, TestCaseGenIn, TestCaseReviewIn
from app.schemas.common import ok
from app.services import claude_runner, extractors

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/ai", tags=["ai"])

_ALL_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _user_name(db: Session, uid: int) -> str:
    u = db.get(User, uid)
    return u.name if u else ""


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _to_case_out(tc: TestCase) -> dict:
    return {
        "id": tc.id,
        "ai_task_id": tc.ai_task_id,
        "project_id": tc.project_id,
        "task_id": tc.task_id,
        "category": tc.category,
        "title": tc.title,
        "steps": tc.steps,
        "expected": tc.expected,
        "priority": tc.priority,
        "adopted": tc.adopted,
        "review_status": tc.review_status.value,
        "reviewed_at": tc.reviewed_at.isoformat() if tc.reviewed_at else None,
        "created_at": tc.created_at.isoformat() if tc.created_at else None,
    }


def _to_task_out(db: Session, at: AiTask) -> dict:
    return {
        "id": at.id,
        "project_id": at.project_id,
        "task_id": at.task_id,
        "user_id": at.user_id,
        "user_name": _user_name(db, at.user_id),
        "kind": at.kind,
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
    """AI 是否可用（前端据此显隐入口 / 优雅降级）。"""
    return ok({"available": claude_runner.is_available()})


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
    if not claude_runner.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI 功能未启用或不可用")

    at = AiTask(
        project_id=body.project_id,
        task_id=body.task_id,
        user_id=user.id,
        kind="testcase_gen",
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
        try:
            for evt in claude_runner.stream_generate(requirement):
                etype = evt.get("type")
                if etype == "delta":
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
                at2.duration_ms = meta.get("duration_ms")
                at2.cost_usd = meta.get("cost_usd")
                at2.output_tokens = meta.get("output_tokens")
            at2.output_raw = raw or None

            cases = claude_runner.parse_testcases(raw)
            if not cases:
                at2.status = AiTaskStatus.failed
                at2.error = (err or "未解析出有效测试点")[:2000]
                s.commit()
                yield _sse({"type": "done", "ai_task_id": ai_task_id,
                            "status": "failed", "msg": at2.error, "cases": []})
                return

            objs = []
            for c in cases:
                tc = TestCase(
                    ai_task_id=ai_task_id,
                    project_id=project_id,
                    task_id=task_id,
                    category=c["category"] or None,
                    title=c["title"],
                    steps=c["steps"] or None,
                    expected=c["expected"] or None,
                    priority=c["priority"] or None,
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


@router.patch("/testcases/{cid}")
def review_testcase(
    cid: int,
    body: TestCaseReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """采纳 / 否决 / 置回待定一条测试点。写 reviewed_at，同步 adopted 兼容列。"""
    tc = db.get(TestCase, cid)
    if not tc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测试点不存在")
    assert_project_role(db, user, tc.project_id, _WRITE_ROLES)
    tc.review_status = body.review_status
    if body.review_status == ReviewStatus.pending:
        tc.reviewed_at = None
    else:
        tc.reviewed_at = datetime.utcnow()  # 与 issues.py resolved_at 对齐（UTC naive），供 /stats/ai 按日聚合
    tc.adopted = (body.review_status == ReviewStatus.adopted)
    db.commit()
    db.refresh(tc)
    return ok(_to_case_out(tc))
