"""CI/CD 集成钩子——P3 集成层的最小可用子集（建议项④）。

两个无人值守端点（CI_HOOK_TOKEN 鉴权，与用户 JWT 分离，仿 RUNNER_TOKEN 模式）：
- POST /api/hooks/run-plan   流水线在发版前触发某测试计划（按 plan_id 或 project_code+plan_name 定位）
- GET  /api/hooks/gate       按 batch_id 查质量门禁：finished + gate=pass/fail/pending，流水线据此阻断

对标主流形态：TestRail 的 CI 触发 run / Xray 的 quality gate。设计取舍：
- 不做通用适配器/事件总线/回传端点（DESIGN P3 的完整形态）——先让「提交即回归、
  不达标不上线」跑通，通用化等有第二个接入方再说。
- 门禁口径与执行结果页一致：pass_rate 分母 = passed+failed（blocked 是环境/选择器
  阻塞，默认不拖垮门禁；strict=1 时 blocked 也算失败）。
- 触发记 trigger=ci 的 test_plan_run：批次完成且有失败时同样走飞书批次告警
  （CI 批次和定时批次一样无人盯页面）。
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models import ExecRun, Project, TestPlan, TestPlanRun
from app.schemas.common import ok

logger = logging.getLogger("test_platform")
router = APIRouter(prefix="/api/hooks", tags=["ci-hooks"])


def require_hook_auth(x_ci_token: str | None = Header(default=None)):
    """CI 钩子鉴权：X-CI-Token 必须与 CI_HOOK_TOKEN 一致。未配置即整条通道关闭。"""
    if not settings.CI_HOOK_TOKEN:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="CI 钩子未启用（服务端未配置 CI_HOOK_TOKEN）")
    if x_ci_token != settings.CI_HOOK_TOKEN:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="X-CI-Token 无效")


class RunPlanHookIn(BaseModel):
    """触发计划：优先 plan_id；否则 project_code + plan_name 定位（流水线配置里写名字更直观）。"""
    plan_id: int | None = None
    project_code: str | None = Field(None, max_length=64)
    plan_name: str | None = Field(None, max_length=255)
    runner: str | None = Field(None, max_length=64)   # 临时换执行机；空用计划默认
    note: str | None = Field(None, max_length=255)    # 流水线号/commit 等留痕（记日志）


@router.post("/run-plan")
def hook_run_plan(
    body: RunPlanHookIn,
    db: Session = Depends(get_db),
    _: None = Depends(require_hook_auth),
):
    """流水线触发测试计划执行。返回 batch_id 供轮询门禁。"""
    from app.api.test_plan import _auto_case_ids_of_plan, _dispatch_plan

    plan = None
    if body.plan_id:
        plan = db.get(TestPlan, body.plan_id)
    elif body.project_code and body.plan_name:
        proj = db.query(Project).filter_by(code=body.project_code).first()
        if proj:
            plan = (db.query(TestPlan)
                    .filter(TestPlan.project_id == proj.id, TestPlan.name == body.plan_name)
                    .order_by(TestPlan.id.desc()).first())
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="计划不存在（给 plan_id，或 project_code+plan_name）")
    case_ids = _auto_case_ids_of_plan(db, plan.id)
    if not case_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="计划内无可自动化用例")
    runner = body.runner or plan.runner
    res = _dispatch_plan(db, plan, case_ids, runner, trigger="ci", started_by=None)
    logger.info("CI 触发计划 plan=%s batch=%s note=%s", plan.id, res["batch_id"], body.note or "-")
    return ok({**res, "plan_id": plan.id, "plan_name": plan.name, "case_count": len(case_ids)})


@router.get("/gate")
def hook_gate(
    batch_id: str = Query(...),
    min_pass_rate: float = Query(100.0, ge=0, le=100),
    strict: bool = Query(False),
    db: Session = Depends(get_db),
    _: None = Depends(require_hook_auth),
):
    """质量门禁：按 batch_id 聚合执行结果，返回 gate=pass/fail/pending。

    - pending：批次还有 pending/running（流水线继续轮询）。
    - 口径：pass_rate = passed/(passed+failed)；blocked 默认不计（环境/选择器阻塞非功能失败），
      strict=1 时 blocked 记入 failed。
    - gate=pass 条件：finished 且 pass_rate >= min_pass_rate（默认 100=零失败）。
    """
    rows = db.query(ExecRun).filter(ExecRun.batch_id == batch_id).all()
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="批次不存在或无执行记录")
    total = len(rows)
    stat = {"passed": 0, "failed": 0, "blocked": 0, "pending": 0, "running": 0}
    for r in rows:
        key = r.status.value if hasattr(r.status, "value") else str(r.status)
        stat[key] = stat.get(key, 0) + 1
    finished = stat["pending"] == 0 and stat["running"] == 0
    failed_eff = stat["failed"] + (stat["blocked"] if strict else 0)
    denom = stat["passed"] + failed_eff
    pass_rate = round(stat["passed"] * 100.0 / denom, 1) if denom else 100.0
    if not finished:
        gate = "pending"
    else:
        gate = "pass" if pass_rate >= min_pass_rate else "fail"
    # 失败用例摘要（给流水线日志直接可读的定位信息）
    failures = []
    if finished and gate == "fail":
        import json as _json
        for r in rows:
            key = r.status.value if hasattr(r.status, "value") else str(r.status)
            if key == "failed" or (strict and key == "blocked"):
                try:
                    title = _json.loads(r.payload or "{}").get("title")
                except (ValueError, TypeError):
                    title = None
                failures.append({"run_id": r.id, "title": title or f"run#{r.id}",
                                 "fail_kind": r.fail_kind, "reason": (r.reason or "")[:200]})
    pr = db.query(TestPlanRun).filter(TestPlanRun.batch_id == batch_id).first()
    return ok({
        "batch_id": batch_id,
        "plan_id": pr.plan_id if pr else None,
        "gate": gate,
        "finished": finished,
        "total": total,
        **stat,
        "pass_rate": pass_rate,
        "min_pass_rate": min_pass_rate,
        "strict": strict,
        "failures": failures[:20],
    })
