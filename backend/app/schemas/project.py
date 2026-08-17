from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import ProjectRole


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = None
    platform_type: str | None = None  # pc / app / None(未分类)


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None  # active / archived
    platform_type: str | None = None  # pc / app / None(未分类)


class ProjectOut(BaseModel):
    id: int
    name: str
    code: str
    description: str | None = None
    platform_type: str | None = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class MemberAdd(BaseModel):
    user_id: int
    role: ProjectRole = ProjectRole.member
    team_id: int | None = None


class MemberUpdate(BaseModel):
    role: ProjectRole
    team_id: int | None = None


class MemberOut(BaseModel):
    id: int
    user_id: int
    username: str
    name: str
    project_id: int
    role: str
    team_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True
