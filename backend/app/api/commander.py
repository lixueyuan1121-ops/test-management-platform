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
    """对话式提问。先校验提问者是该项目成员，再走 commander 两跳编排。

    返回 data 为 router.ask 的信封之一：
      {"type":"answer", ...} / {"type":"clarify","answer":...} / {"type":"draft","intent","draft":...}
    """
    assert_project_role(db, user, body.project_id, _ASK_ROLES)
    from app.services.commander.router import ask as commander_ask
    # 服务端校验过的 project_id 才可信；router.ask 会把它注入 params 覆盖模型给的值。
    data = commander_ask(db, user, body.project_id, body.question, body.provider, body.context)
    return ok(data)


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    """列出全部能力（name/desc/params/kind）。登录即可，不校验项目角色。"""
    from app.services.commander.registry import list_capabilities
    return ok({"capabilities": list_capabilities()})
