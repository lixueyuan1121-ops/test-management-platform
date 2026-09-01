"""对话测评 query 生成路由(SSE 流式 + 落库 eval_query)。

独立于 api/ai.py 的功能测试点生成(隔离,避免改动互相波及)。SSE 骨架与 gen_testcases
同构(双 session、心跳/delta/error 转发、指标写入),但落 EvalQuery、支持多轮分组。
流式落库同坑:生成器在 get_db 关闭后才迭代,故函数体内建 running 记录,生成器内另开 SessionLocal。
"""
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import AiTaskStatus, ProjectRole
from app.db.session import SessionLocal, get_db
from app.models import AiTask, EvalQuery, Project, User
from app.models.ai_eval import EvalTask
from app.schemas.ai import EvalQueryGenIn
from app.schemas.common import ok
from app.services import claude_runner, extractors, feishu, generators
from app.services.eval_import import parse_eval_template

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/ai", tags=["ai-eval"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _to_query_out(q: EvalQuery) -> dict:
    rs = q.review_status
    return {
        "id": q.id,
        "ai_task_id": q.ai_task_id,
        "project_id": q.project_id,
        "task_id": q.task_id,
        "title": q.title,
        "prompt": q.prompt,
        "dimension": q.dimension,
        "expected": q.expected,
        "attachments": json.loads(q.attachments) if q.attachments else [],
        "conversation_group": q.conversation_group,
        "turn_index": q.turn_index,
        "review_status": getattr(rs, "value", rs),
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


@router.post("/eval-queries")
def gen_eval_queries(
    body: EvalQueryGenIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """流式生成对话测评 query 并落库。SSE 事件:delta / error / done(含 queries)。
    传 eval_task_id 时生成结果自动追加进该测评任务用例集(挂任务到点即享)。"""
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if body.eval_task_id is not None:
        et = db.get(EvalTask, body.eval_task_id)
        if not et or et.project_id != body.project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在或不属于该项目")
    provider_id = generators.normalize_provider(body.provider)
    engine = generators.get_provider(provider_id)
    if not engine.is_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"生成引擎「{provider_id}」未启用或不可用")

    at = AiTask(
        project_id=body.project_id,
        task_id=body.task_id,
        user_id=user.id,
        kind="eval_query_gen",
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
    eval_task_id = body.eval_task_id
    requirement = body.requirement
    dimensions = body.dimensions

    def sse():
        raw = ""
        meta: dict | None = None
        err: str | None = None
        t0 = time.monotonic()
        try:
            for evt in engine.stream_generate(
                requirement, project_id=project_id,
                prompt_builder=lambda: claude_runner.build_eval_query_prompt(requirement, dimensions),
                system_prompt=claude_runner.EVAL_SYSTEM_PROMPT,
            ):
                etype = evt.get("type")
                if etype == "heartbeat":
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
        except Exception as e:
            logger.exception("AI 对话 query 流式生成异常")
            err = err or f"生成中断：{e}"

        s = SessionLocal()
        try:
            at2 = s.get(AiTask, ai_task_id)
            if at2 is None:
                yield _sse({"type": "error", "msg": "任务记录丢失"})
                return
            if meta:
                at2.duration_ms = meta.get("duration_ms") or int((time.monotonic() - t0) * 1000)
                at2.cost_usd = meta.get("cost_usd")
                at2.output_tokens = meta.get("output_tokens")
            at2.output_raw = raw or None

            queries = claude_runner.parse_eval_queries(raw)
            if not queries:
                at2.status = AiTaskStatus.failed
                if err:
                    detail = err
                elif not raw:
                    detail = "未生成有效 query：引擎无任何输出(可能被网关/超时切断)"
                else:
                    detail = f"未生成有效 query：输出 {len(raw)} 字但未解析出 query 数组(尾部:…{raw[-200:]})"
                at2.error = detail[:2000]
                s.commit()
                yield _sse({"type": "done", "ai_task_id": ai_task_id,
                            "status": "failed", "msg": at2.error, "queries": []})
                return

            objs = []
            for i, c in enumerate(queries):
                # conversation_group 为空 → 补唯一组名(单轮题各自独立),避免与别批/别题混淆
                cg = c["conversation_group"] or f"g{ai_task_id}_{i}"
                q = EvalQuery(
                    ai_task_id=ai_task_id,
                    provider=provider_id,
                    project_id=project_id,
                    task_id=task_id,
                    title=c["title"],
                    prompt=c["prompt"],
                    dimension=c.get("dimension"),
                    expected=c.get("expected"),
                    attachments=json.dumps(c["attachments"], ensure_ascii=False) if c.get("attachments") else None,
                    conversation_group=cg,
                    turn_index=c.get("turn_index") or 0,
                )
                s.add(q)
                objs.append(q)
            at2.status = AiTaskStatus.done
            at2.case_count = len(queries)
            s.commit()
            for q in objs:
                s.refresh(q)
            # 关联了测评任务 → 把本批 query 追加进任务用例集(有序去重,与 eval_task.py 同口径)。
            # 挂载失败不能连累已生成的用例:单独 try,仅在 done 帧标记 attached。
            attached = 0
            if eval_task_id is not None:
                try:
                    et2 = s.get(EvalTask, eval_task_id)
                    if et2 is not None:
                        qids = json.loads(et2.query_ids) if et2.query_ids else []
                        merged = list(dict.fromkeys(qids + [q.id for q in objs]))
                        et2.query_ids = json.dumps(merged, ensure_ascii=False)
                        s.commit()
                        attached = len(merged) - len(qids)
                except Exception:
                    logger.exception("挂载测评任务失败 eval_task_id=%s", eval_task_id)
                    s.rollback()
            yield _sse({
                "type": "done",
                "ai_task_id": ai_task_id,
                "status": "done",
                "eval_task_id": eval_task_id,
                "attached": attached,
                "queries": [_to_query_out(q) for q in objs],
                "meta": {
                    "case_count": at2.case_count,
                    "duration_ms": at2.duration_ms,
                    "cost_usd": float(at2.cost_usd) if at2.cost_usd is not None else None,
                    "output_tokens": at2.output_tokens,
                },
            })
        except Exception as e:
            logger.exception("对话 query 落库失败")
            s.rollback()
            yield _sse({"type": "error", "msg": f"落库失败：{e}"})
        finally:
            s.close()

    return StreamingResponse(sse(), media_type="text/event-stream")


@router.get("/eval-dimensions")
def list_eval_dimensions(user: User = Depends(get_current_user)):
    """对话测评维度注册表(key/中文标签/说明)。前端各测评页从这里取,免两端硬编码漂移。"""
    dims = [
        {"key": k, "label": claude_runner.EVAL_DIM_LABELS.get(k, k), "desc": v}
        for k, v in claude_runner.EVAL_DIMENSIONS.items()
    ]
    return ok({"dimensions": dims})


class EvalQueryManualIn(BaseModel):
    """手工录入/编辑一条测评用例(测评任务的「定制用例」入口;ai_task_id 为空区别于 AI 生成)。"""
    project_id: int
    title: str = Field(..., min_length=1, max_length=512)
    prompt: str = Field(..., min_length=1)
    dimension: str | None = None
    expected: str | None = None
    conversation_group: str | None = Field(None, max_length=64)
    turn_index: int = 0


class EvalQueryUpdateIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=512)
    prompt: str | None = Field(None, min_length=1)
    dimension: str | None = None
    expected: str | None = None
    conversation_group: str | None = Field(None, max_length=64)
    turn_index: int | None = None


def _norm_dimension(dim: str | None) -> str | None:
    return dim if dim in claude_runner.EVAL_DIMENSIONS else None


@router.post("/eval-queries/manual")
def create_eval_query_manual(body: EvalQueryManualIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    q = EvalQuery(
        project_id=body.project_id,
        title=body.title.strip(),
        prompt=body.prompt.strip(),
        dimension=_norm_dimension(body.dimension),
        expected=(body.expected or "").strip() or None,
        conversation_group=(body.conversation_group or "").strip() or None,
        turn_index=max(0, body.turn_index or 0),
        provider="manual",
    )
    db.add(q); db.commit(); db.refresh(q)
    # 手工单轮题补唯一组名(与 AI 生成落库口径一致,避免与别的题混组)
    if not q.conversation_group:
        q.conversation_group = f"m{q.id}"
        db.commit(); db.refresh(q)
    return ok(_to_query_out(q))


class EvalQueryImportIn(BaseModel):
    """模板导入(本地 CSV/TSV 文本 或 飞书文档链接)。text 与 feishu_url 二选一。"""
    project_id: int
    text: str | None = None
    feishu_url: str | None = None
    eval_task_id: int | None = None
    dry_run: bool = False


@router.post("/eval-queries/import")
def import_eval_queries(body: EvalQueryImportIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """按模板(CSV/TSV)批量导入对话测评用例。

    text=本地粘贴/上传的模板文本;feishu_url=飞书电子表格/文档链接(走 extractors 取文,
    自动路由飞书 sheets/docx)。二选一。dry_run=True 仅解析预览、不落库。
    可选 eval_task_id:导入的同时追加进该测评任务用例集(有序去重,与生成侧同口径)。
    """
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    et = None
    if body.eval_task_id is not None:
        et = db.get(EvalTask, body.eval_task_id)
        if not et or et.project_id != body.project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评任务不存在或不属于该项目")

    text = (body.text or "").strip()
    if not text:
        url = (body.feishu_url or "").strip()
        if not url:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请粘贴模板文本或提供飞书文档链接")
        # 仅放行飞书文档链接:飞书分支只用 URL 里的 token 调飞书 OpenAPI(固定 open.feishu.cn),
        # 从不直接请求用户给的主机 → 本端点无 SSRF 面(不做通用 URL 抓取,与"飞书导入"语义一致)。
        if not feishu.is_feishu_url(url):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="仅支持飞书文档链接(docx/wiki/sheets/base)")
        try:
            _, text = extractors.extract_from_url(url)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        rows, skipped = parse_eval_template(text)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    if body.dry_run:
        return ok({"dry_run": True, "count": len(rows), "skipped": skipped, "preview": rows})

    objs = []
    for r in rows:
        q = EvalQuery(
            project_id=body.project_id,
            title=r["title"],
            prompt=r["prompt"],
            dimension=r["dimension"],
            expected=r["expected"],
            conversation_group=r["conversation_group"],
            turn_index=r["turn_index"],
            provider="import",
        )
        db.add(q); db.flush()
        # 单轮无对话组补唯一组名(与手工录入/生成落库口径一致,避免与别题混组)
        if not q.conversation_group:
            q.conversation_group = f"i{q.id}"
        objs.append(q)

    attached = 0
    if et is not None:
        qids = json.loads(et.query_ids) if et.query_ids else []
        merged = list(dict.fromkeys(qids + [q.id for q in objs]))
        et.query_ids = json.dumps(merged, ensure_ascii=False)
        attached = len(merged) - len(qids)
    db.commit()
    for q in objs:
        db.refresh(q)
    return ok({
        "dry_run": False,
        "count": len(objs),
        "skipped": skipped,
        "attached": attached,
        "queries": [_to_query_out(q) for q in objs],
    })


class EvalQueryExpandIn(BaseModel):
    """占位符模板展开(promptfoo 式参数化):base 题的 title/prompt/expected 里写 {{变量}},
    variables 给每个变量的取值列表,笛卡尔积批量生成变体题——同一考点稳定产出 N 个变体。"""
    base_query_id: int
    variables: dict[str, list[str]]


_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_一-鿿]+)\s*\}\}")
_EXPAND_MAX = 50  # 单次展开上限(笛卡尔积爆炸保护)


@router.post("/eval-queries/expand")
def expand_eval_query(body: EvalQueryExpandIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    base = db.get(EvalQuery, body.base_query_id)
    if not base:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="模板题不存在")
    assert_project_role(db, user, base.project_id, _WRITE_ROLES)

    texts = {"title": base.title or "", "prompt": base.prompt or "", "expected": base.expected or ""}
    placeholders = sorted({m for t in texts.values() for m in _VAR_RE.findall(t)})
    if not placeholders:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="该题的标题/提问/期望里没有 {{占位符}},先编辑题目加入如 {{城市}} 再展开")
    # 每个占位符必须给非空取值列表(清洗空串);多余的键忽略
    values: list[tuple[str, list[str]]] = []
    for name in placeholders:
        vs = [str(v).strip() for v in (body.variables.get(name) or []) if str(v).strip()]
        if not vs:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"占位符 {{{{{name}}}}} 未提供取值")
        values.append((name, vs))
    total = 1
    for _, vs in values:
        total *= len(vs)
    if total > _EXPAND_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"组合数 {total} 超上限 {_EXPAND_MAX},请减少取值")

    import itertools
    created = []
    for combo in itertools.product(*(vs for _, vs in values)):
        mapping = {name: combo[i] for i, (name, _) in enumerate(values)}
        def subst(s: str) -> str:
            return _VAR_RE.sub(lambda m: mapping.get(m.group(1), m.group(0)), s)
        q = EvalQuery(
            project_id=base.project_id,
            title=subst(texts["title"])[:512],
            prompt=subst(texts["prompt"]),
            dimension=base.dimension,
            expected=subst(texts["expected"]) or None,
            turn_index=0,  # 变体各自单轮成组(多轮模板展开语义复杂,不支持;组名建完补)
            provider="template",
        )
        db.add(q); db.flush()
        q.conversation_group = f"t{q.id}"
        created.append(q.id)
    db.commit()
    return ok({"created": created, "count": len(created)})


@router.patch("/eval-queries/{query_id}")
def update_eval_query(query_id: int, body: EvalQueryUpdateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.get(EvalQuery, query_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评用例不存在")
    assert_project_role(db, user, q.project_id, _WRITE_ROLES)
    if body.title is not None:
        q.title = body.title.strip()
    if body.prompt is not None:
        q.prompt = body.prompt.strip()
    if body.dimension is not None:
        q.dimension = _norm_dimension(body.dimension)
    if body.expected is not None:
        q.expected = body.expected.strip() or None
    if body.conversation_group is not None:
        q.conversation_group = body.conversation_group.strip() or None
    if body.turn_index is not None:
        q.turn_index = max(0, body.turn_index)
    db.commit(); db.refresh(q)
    return ok(_to_query_out(q))


@router.delete("/eval-queries/{query_id}")
def delete_eval_query(query_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.get(EvalQuery, query_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="测评用例不存在")
    assert_project_role(db, user, q.project_id, _WRITE_ROLES)
    db.delete(q); db.commit()
    return ok({"deleted": query_id})


@router.get("/eval-queries")
def list_eval_queries(
    project_id: int = Query(...),
    dimension: str | None = Query(None),
    eval_task_id: int | None = Query(None),
    limit: int = Query(200, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出某项目历史生成的对话测评 query(供用例库查看 + 再次下发)。

    可选筛选:dimension=按测评维度、eval_task_id=只看某测评任务用例集内的题(读其 query_ids);
    二者可叠加(AND)。测评任务无用例时返回空列表。
    """
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(EvalQuery).filter(EvalQuery.project_id == project_id)
    if dimension:
        q = q.filter(EvalQuery.dimension == dimension)
    if eval_task_id is not None:
        task = db.get(EvalTask, eval_task_id)
        qids = json.loads(task.query_ids) if (task and task.query_ids) else []
        if not qids:
            return ok([])
        q = q.filter(EvalQuery.id.in_(qids))
    rows = q.order_by(EvalQuery.id.desc()).limit(limit).all()
    return ok([_to_query_out(r) for r in rows])
