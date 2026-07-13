from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    is_platform_admin: bool = False


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    status: str | None = None  # active / disabled
    is_platform_admin: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=128)
