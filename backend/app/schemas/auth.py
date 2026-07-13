from datetime import datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    username: str
    name: str
    email: str | None = None
    is_platform_admin: bool
    status: str

    class Config:
        from_attributes = True


class MeOut(BaseModel):
    user: UserOut
    # 该用户在各项目的成员关系：[{project_id, project_code, project_name, role}]
    memberships: list[dict]
