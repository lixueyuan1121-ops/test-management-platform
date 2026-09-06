"""对话式测试指挥官(Commander) API：两跳编排的对话入口 + 能力清单。

纯编排层，不建新表。业务全在 app.services.commander：
- `POST /api/commander/ask`：项目成员（含 guest，只读/草稿）提问 → router.ask 两跳编排。
- `GET  /api/commander/capabilities`：登录即可看能力清单（供前端渲染/调试）。

鉴权口径：
- ask 用 assert_project_role(全部角色) 校验「提问者是该项目成员」——问答本身只读/出草稿，
  真正的写权限收口在各能力 runner（draft 按真实端点角色反查、read 靠 IDOR 反查）。
- 服务端校验过的 body.project_id 传给 router.ask 并被注入 params（覆盖模型给的任何 project_id，
  模型不得选项目）——切勿从别处透传客户端 project id。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import User
from app.schemas.common import ok
from app.services import ai_jobs

router = APIRouter(prefix="/api/commander", tags=["commander"])

# 提问=该项目任意角色成员即可（含 guest：只读/草稿；写动作的角色闸在能力 runner 内）。
_ASK_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)


class AskBody(BaseModel):
    project_id: int
    question: str
    provider: str | None = None
    context: dict | None = None


@router.post("/ask")
def ask(body: AskBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """对话式提问 → 入队异步跑（两跳 LLM 数十秒，远超网关/代理超时，故不同步执行）。

    先校验提问者是该项目成员，再 enqueue kind=commander，返回 {job_id}；前端轮询
    /ai-jobs/{id} 取 result（result 即 router.ask 的信封 {type:answer|clarify|draft,...}）。
    与 rts/fail_cluster/judge 同款：慢 AI 一律走 ai_jobs 队列，不占请求连接。
    """
    assert_project_role(db, user, body.project_id, _ASK_ROLES)
    job = ai_jobs.enqueue(
        db, "commander", project_id=body.project_id, user_id=user.id,
        provider=body.provider,
        input={"project_id": body.project_id, "question": body.question,
               "provider": body.provider, "context": body.context},
    )
    return ok({"job_id": job.id})


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    """列出全部能力（name/desc/params/kind）。登录即可，不校验项目角色。"""
    from app.services.commander.registry import list_capabilities
    return ok({"capabilities": list_capabilities()})
